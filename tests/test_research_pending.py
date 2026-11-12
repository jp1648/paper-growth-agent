from types import SimpleNamespace

import pytest

from trader.research import build_graph
from trader.research_client import ResearchPending
from trader.runner import Runner


@pytest.mark.parametrize("outcome", ["reserved", "aborted_accounted", "complete"])
@pytest.mark.parametrize("cached", [False, True])
def test_legacy_graph_cannot_reuse_durable_run(env, outcome, cached):
    service, _, _ = env
    key = "durable-only"
    service.store.put(
        key,
        {
            "run_key": key,
            "experiment_id": service.store.scope,
            "durable": True,
            "outcome": outcome,
        },
        "research_spend",
        scope="global",
    )
    if cached:
        service.store.put(key, {"decision": {"symbol": "ACME"}}, "research_result")
    team = SimpleNamespace(run=lambda *a, **kw: pytest.fail("model run"))
    graph = build_graph(service, team)
    with pytest.raises(ValueError, match="verified Temporal workflow"):
        graph.invoke({"symbol": "ACME", "run_key": key, "research_only": True})
    assert service.store.get(key, "research_committed") is None
    assert service.store.get("ACME", "thesis") is None
    assert service.store.get(key, "order_intent") is None


@pytest.mark.parametrize("kind", ["research_request", "research_workflow", "research_workflow_submission"])
def test_legacy_graph_cannot_run_before_durable_reservation(env, kind):
    service, _, _ = env
    service.reconcile()
    key = "durable-before-reservation"
    service.store.put(key, {"run_key": key}, kind)
    team = SimpleNamespace(run=lambda *a, **kw: pytest.fail("model run"))
    with pytest.raises(ValueError, match="verified Temporal workflow"):
        build_graph(service, team).invoke({"symbol": "ACME", "run_key": key, "research_only": True})
    assert service.store.get(key, "research_committed") is None
    assert service.store.get("ACME", "thesis") is None
    assert service.store.get(key, "order_intent") is None


def test_runner_preserves_and_drains_exact_pending_invocation(env, monkeypatch):
    service, _, _ = env
    service.reconcile()
    reference = {"run_key": "slot:ACME", "manifest_hash": "a" * 64}
    calls = []

    def invoke(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise ResearchPending(reference, "running", "valuation")
        return {"decision": {"action": "WATCH", "confidence": "0"}, "execution": {"status": "watch"}}

    runner = Runner(service, SimpleNamespace(invoke=invoke))
    monkeypatch.setattr(runner, "due", lambda symbol, phase: (True, "fixture"))
    monkeypatch.setattr(
        "trader.runner.spending_status",
        lambda store, limit: {
            "unbounded_unknown_runs": 0,
            "unknown_usage_runs": 0,
            "unresolved_testing_runs": 0,
            "ordinary_remaining_usd": 1,
        },
    )
    first = runner.research_batch("slot", "intraday", symbols=["ACME"])
    assert first["pending"] is True
    saved = service.store.get("slot:ACME", "job")
    assert saved["status"] == "pending"
    assert len(calls) == 1
    assert runner.resume_pending_research()["status"] == "pending research completed"
    assert calls == [saved["state"], saved["state"]]
    assert service.store.get("slot:ACME", "job")["status"] == "complete"
    assert runner.resume_pending_research() is None


def test_scheduled_claim_and_invocation_are_atomic(env):
    service, _, _ = env
    store = service.store
    receipt = {"symbol": "ACME", "schedule_sha256": "a" * 64}
    pending = {
        "status": "starting",
        "slot": "scheduled-1",
        "study_id": "fixture-study",
        "schedule_sha256": "a" * 64,
        "state": {"run_key": "scheduled-1", "symbol": "ACME"},
    }
    assert store.claim_paper_slot("fixture-study", "scheduled-1", receipt, pending=pending)
    assert store.get("scheduled-1", "paper_slot_pending") == pending
    assert not store.claim_paper_slot("fixture-study", "scheduled-1", receipt, pending=pending)
    assert len(store.items("paper_slot_pending")) == 1
    bad = {**pending, "slot": "different"}
    try:
        store.claim_paper_slot("fixture-study", "scheduled-2", receipt, pending=bad)
    except ValueError:
        pass
    else:
        raise AssertionError("Mismatched pending payload was accepted")
    assert store.get('["fixture-study","scheduled-2"]', "paper_slot") is None
