import copy

import pytest
from test_research_team import Evidence, team

from trader.config import Settings
from trader.research import build_graph
from trader.research_team import Budget, ResearchDesk


def reviewed():
    return {
        "addendum": {
            "text": "Conditional interpretations retain uncertainty.",
            "created_at": "2026-11-26T06:00:00+00:00",
            "source_ids": ["fixture-sec"],
            "calculation_ids": [],
        },
        "review": {"verdict": "accepted_addendum_only"},
        "status": "original_reports_unaccepted_reviewed_addendum_separate",
    }


def test_tool_free_lead_keeps_reports_and_gap_veto(env, decision, monkeypatch):
    original = ResearchDesk.run_lead
    before = {}

    def synthesize(desk):
        desk.continuation["reviewed_synthesis"] = reviewed()
        desk.budget = Budget(desk.cfg)  # A descendant has its own cumulative budget.
        desk.reports["adversarial"]["unresolved_material_gaps"] = True
        before.update(copy.deepcopy(desk.reports))
        assert not desk.correction_available()
        context = desk.final_context()
        assert context["reviewed_synthesis"] == reviewed()
        assert "correction_instruction" not in context
        result = original(desk)
        assert result["reviewed_synthesis"] == reviewed()
        return result

    monkeypatch.setattr(ResearchDesk, "run_lead", synthesize)
    research, model = team(env, decision)
    result = build_graph(env[0], research).invoke({"symbol": "ACME", "run_key": "reviewed-synthesis"})
    assert model.calls.count("Decision") == 1
    assert result["team_reports"] == before
    assert result["decision"]["action"] == "WATCH"
    assert env[1].submits == 0


def test_synthesis_attaches_durable_checkpoint_before_model(monkeypatch):
    import trader.research_team as module

    class Checkpoint:
        phase = "lead"
        saver = object()
        attached = False

        def attach_desk(self, desk, restore):
            assert restore is False
            self.attached = True

    checkpoint = Checkpoint()
    cfg = Settings()
    evidence = Evidence()
    desk = ResearchDesk(
        cfg, evidence, {"symbol": "ACME", "portfolio": {}}, evidence.get("ACME"), Budget(cfg), None
    )
    desk.continuation["reviewed_synthesis"] = reviewed()
    desk.reports = {role: {} for role in ("business", "catalysts", "valuation", "adversarial")}
    desk.checkpoint_factory = lambda role: checkpoint if role == "research_lead" else pytest.fail(role)

    class Captured(Exception):
        pass

    def create(**kwargs):
        assert checkpoint.attached
        assert desk.checkpoints["research_lead"] is checkpoint
        assert kwargs["name"] == "research_lead"
        assert kwargs["tools"] == []
        assert kwargs["checkpointer"] is checkpoint.saver
        assert kwargs["middleware"][0].checkpoint is checkpoint
        raise Captured

    monkeypatch.setattr(module, "create_agent", create)
    with pytest.raises(Captured):
        desk.run_lead()


@pytest.mark.parametrize("invalid", ["unknown_citation", "malformed"])
def test_synthesis_invalid_report_never_retries(decision, invalid):
    from trader.research_team import Decision

    cfg = Settings()
    provider = Evidence()
    desk = ResearchDesk(
        cfg, provider, {"symbol": "ACME", "portfolio": {}}, provider.get("ACME"), Budget(cfg), None
    )
    desk.continuation["reviewed_synthesis"] = reviewed()
    report = decision.model_dump(mode="json")
    report["evidence_ids"] = ["never-registered"]
    if invalid == "malformed":
        report.pop("symbol")

    class Agent:
        name = "research_lead"
        calls = 0

        def invoke(self, state, config):
            self.calls += 1
            return {"structured_response": report, "messages": []}

    agent = Agent()
    with pytest.raises(ValueError):
        desk.invoke_report(agent, Decision, {}, {})
    assert agent.calls == 1
    assert not any(kind == "research_citation_correction" for kind, _ in desk.budget.events)


def test_single_dispatch_guard_replays_completed_journal_but_refuses_new_operation(tmp_path):
    from trader.research_checkpoint import ResearchCheckpoint
    from trader.research_journal import Journal
    from trader.research_team import Guard, ResearchLimit

    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    provider = Evidence()
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, {"run": "synthesis"}).lease() as journal:
        desk = ResearchDesk(
            cfg, provider, {"symbol": "ACME", "portfolio": {}}, provider.get("ACME"), Budget(cfg), None
        )
        adapter = ResearchCheckpoint(
            journal, path.parent / "graph.sqlite", phase="lead", role="research_lead", thread_id="synthesis"
        )
        adapter.attach_desk(desk)
        guard = Guard(desk.budget, "research_lead", set(), checkpoint=adapter, dispatch_limit=1)

        def dispatch():
            guard.admit_model(100)
            return "original malformed response"

        assert (
            adapter.call(
                "model",
                "research_lead",
                "one",
                {},
                dispatch,
                preflight=lambda: guard.admit_model(100, admit_only=True),
            )
            == "original malformed response"
        )
        saved = journal.load_state()
        desk.restore(saved)
        assert desk.budget.models == 1
        assert (
            adapter.call(
                "model",
                "research_lead",
                "one",
                {},
                lambda: pytest.fail("duplicate provider call"),
                preflight=lambda: guard.admit_model(100, admit_only=True),
            )
            == "original malformed response"
        )
        with pytest.raises(ResearchLimit, match="only one"):
            adapter.call(
                "model",
                "research_lead",
                "two",
                {},
                lambda: pytest.fail("second provider call"),
                preflight=lambda: guard.admit_model(100, admit_only=True),
            )
        assert journal._db.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        with pytest.raises(ResearchLimit, match="only one"):
            guard.admit_model(100)
        assert desk.budget.models == 1
        adapter.close()
