from threading import Event
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from trader.research_checkpoint import PausedOperation, ResearchCancelled, ResearchCheckpoint, model_identity
from trader.research_journal import Journal, RequestHashMismatch


class Desk:
    def __init__(self):
        self.calls = 0

    def checkpoint(self):
        return {"calls": self.calls}

    def restore(self, saved):
        self.calls = saved["calls"]


def test_cancellation_finishes_current_call_and_prevents_next_dispatch(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    stop = Event()
    with Journal(path, {"run": 1}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal,
            path.parent / "graph.sqlite",
            phase="initial",
            role="business",
            thread_id="run",
            stop_event=stop,
        )
        adapter.attach_desk(Desk())

        def current():
            adapter.desk.calls += 1
            stop.set()
            return "finished"

        assert adapter.call("model", "business", "one", {}, current) == "finished"
        with pytest.raises(ResearchCancelled):
            adapter.call("model", "business", "two", {}, lambda: pytest.fail("new dispatch"))
        assert journal.load_state() == {"calls": 1}
        stop.clear()
        assert adapter.call("model", "business", "one", {}, lambda: pytest.fail("repeated")) == "finished"
        assert adapter.call("model", "business", "two", {}, lambda: "new") == "new"
        adapter.close()


def test_preflight_failure_does_not_create_an_ambiguous_call(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    blocked = True

    def preflight():
        if blocked:
            raise ValueError("controls changed")

    with Journal(path, {"run": 1}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal,
            path.parent / "graph.sqlite",
            phase="initial",
            role="business",
            thread_id="run",
            before_dispatch=preflight,
        )
        adapter.attach_desk(Desk())
        with pytest.raises(ValueError, match="controls changed"):
            adapter.call("model", "business", "one", {}, lambda: pytest.fail("dispatch"))
        blocked = False
        assert adapter.call("model", "business", "one", {}, lambda: "result") == "result"
        adapter.close()


def test_missing_required_trace_stops_before_pending_operation(tmp_path, monkeypatch):
    from trader import observability

    monkeypatch.setenv("OBSERVABILITY_DATABASE_URL", "configured-for-test")
    monkeypatch.setattr(observability, "_sink", None)
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal,
            path.parent / "graph.sqlite",
            phase="initial",
            role="business",
            thread_id="run",
            before_dispatch=lambda: observability.emit("research_dispatch_preflight", {}, required=True),
        )
        adapter.attach_desk(Desk())
        try:
            with pytest.raises(observability.TracePersistenceError):
                adapter.call("model", "business", "one", {}, lambda: pytest.fail("provider call"))
            assert journal.inspect_operations() == {"complete_operations": 0, "pending_operations": []}
            assert adapter.desk.calls == 0
        finally:
            adapter.close()


class Flow(TypedDict):
    prompt: str
    model: str
    tool: str


def graph_for(adapter, counts, crash):
    builder = StateGraph(Flow)

    def model_node(state):
        def provider():
            counts["provider"] += 1
            adapter.desk.calls += 1
            return "model answer"

        answer = adapter.call(
            "model",
            "valuation",
            model_identity({"messages": [state["prompt"]], "tools": [], "schema": {}}),
            {"request": state["prompt"]},
            provider,
        )
        if crash["at"] == "model":
            raise RuntimeError("crash after committed model call")
        return {"model": answer}

    def tool_node(state):
        def tool():
            counts["tool"] += 1
            adapter.desk.calls += 1
            return f"source for {state['model']}"

        answer = adapter.call("tool", "valuation", "source:1", {"model": state["model"]}, tool)
        if crash["at"] == "tool":
            raise RuntimeError("crash after committed tool call")
        return {"tool": answer}

    builder.add_node("model", model_node)
    builder.add_node("tool", tool_node)
    builder.add_edge(START, "model")
    builder.add_edge("model", "tool")
    builder.add_edge("tool", END)
    return builder.compile(checkpointer=adapter.saver)


@pytest.mark.parametrize("crash_at", ["model", "tool"])
def test_restart_replays_journaled_response_before_graph_checkpoint(tmp_path, crash_at):
    path = tmp_path / "private" / "operations.sqlite"
    graph_path = path.parent / "graph.sqlite"
    counts = {"provider": 0, "tool": 0}
    crash = {"at": crash_at}
    with Journal(path, {"run": "one"}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal, graph_path, phase="initial", role="valuation", thread_id="run-one"
        )
        adapter.attach_desk(Desk())
        graph = graph_for(adapter, counts, crash)
        with pytest.raises(RuntimeError, match="crash after committed"):
            adapter.invoke(graph, {"prompt": "request", "model": "", "tool": ""})
        assert graph.get_state(adapter._config(None)).config["configurable"]["checkpoint_id"]
        adapter.close()
    assert counts["provider"] == 1
    assert counts["tool"] == (0 if crash_at == "model" else 1)
    crash["at"] = None
    with Journal(path, {"run": "one"}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal, graph_path, phase="initial", role="valuation", thread_id="run-one"
        )
        desk = Desk()
        adapter.attach_desk(desk)
        graph = graph_for(adapter, counts, crash)
        result = adapter.invoke(graph, {"prompt": "ignored", "model": "", "tool": ""})
        assert result["model"] == "model answer"
        assert result["tool"] == "source for model answer"
        assert counts == {"provider": 1, "tool": 1}
        assert desk.calls == 2
        adapter.close()


def test_pending_operation_pauses_and_changed_request_hash_fails(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="run"
        )
        adapter.attach_desk(Desk())
        with pytest.raises(RuntimeError, match="provider failed"):
            adapter.call(
                "model",
                "business",
                "node",
                {"prompt": "one"},
                lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
            )
        with pytest.raises(PausedOperation):
            adapter.call("model", "business", "node", {"prompt": "one"}, lambda: "must not run")
        with pytest.raises(RequestHashMismatch):
            adapter.call("model", "business", "node", {"prompt": "different"}, lambda: "must not run")
        adapter.close()


def test_thread_namespace_and_explicit_new_input(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        one = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="run"
        )
        two = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="initial", role="valuation", thread_id="run"
        )
        assert one.thread_id != two.thread_id
        with pytest.raises(ValueError, match="thread ID differs"):
            one._config({"configurable": {"thread_id": two.thread_id}})
        one.close()
        two.close()


def test_model_identity_distinguishes_intentional_repeated_occurrences():
    request = {"messages": [{"role": "user", "content": "same"}], "tools": [], "schema": {}}
    assert model_identity(request, occurrence=1) == model_identity(request, occurrence=1)
    assert model_identity(request, occurrence=1) != model_identity(request, occurrence=2)


def test_explicit_new_input_reinvokes_completed_graph(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="run"
        )
        adapter.attach_desk(Desk())
        builder = StateGraph(dict)
        builder.add_node("increment", lambda state: {"value": state["value"] + 1})
        builder.add_edge(START, "increment")
        builder.add_edge("increment", END)
        graph = builder.compile(checkpointer=adapter.saver)
        assert adapter.invoke(graph, {"value": 2})["value"] == 3
        assert adapter.invoke(graph, {"value": 8}, resume=False)["value"] == 9
        adapter.close()


def test_attach_without_restore_preserves_current_desk_state(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        journal.save_state({"calls": 1})
        adapter = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="run"
        )
        current = Desk()
        current.calls = 3
        adapter.attach_desk(current, restore=False)
        assert current.calls == 3
        assert adapter.model_identity({"messages": []}) == model_identity({"messages": []})
        adapter.close()


def test_local_preflight_rejection_never_creates_pending_operation(tmp_path):
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        adapter = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="run"
        )
        adapter.attach_desk(Desk())

        def rejected():
            raise ValueError("local budget denied")

        with pytest.raises(ValueError, match="local budget denied"):
            adapter.call(
                "model", "business", "node", {"prompt": "one"}, lambda: "must not run", preflight=rejected
            )
        assert journal._db.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert adapter.call("model", "business", "node", {"prompt": "one"}, lambda: "safe") == "safe"
        # A replayed provider result is already paid for; an exhausted current
        # admission ceiling must not turn that cached result into a new failure.
        assert (
            adapter.call(
                "model",
                "business",
                "node",
                {"prompt": "one"},
                lambda: "must not run",
                preflight=rejected,
            )
            == "safe"
        )
        adapter.close()
