import copy
import json

import pytest
from test_research_team import Evidence, TeamModel, team

from trader.config import Settings
from trader.research import build_graph
from trader.research_team import Budget, DeepResearchTeam, ResearchDesk


def seed(desk):
    tools = {tool.name: tool for tool in desk.tools()}
    tools["sum_cash_flows"].invoke(
        {
            "period_end": "2025-12-31",
            "components": [
                {"label": "Recorded cash flow", "amount_usd": "-10.00", "evidence_id": "fixture-sec"}
            ],
        }
    )
    tools["multiply_values"].invoke({"left": "2", "right": "3"})


def test_arithmetic_context_is_bounded_detached_and_not_evidence():
    cfg = Settings()
    desk = ResearchDesk(
        cfg, Evidence(), {"symbol": "ACME", "portfolio": {}}, Evidence().get("ACME"), Budget(cfg), None
    )
    seed(desk)
    multiply = next(tool for tool in desk.tools() if tool.name == "multiply_values")
    for value in range(25):
        multiply.invoke({"left": str(value), "right": "3"})
    original = copy.deepcopy(desk.budget.events)
    context = desk.arithmetic_context()
    assert len(context["records"]) == 20 and context["omitted_records"] == 7
    assert len(json.dumps(context["records"]).encode()) <= 12000
    assert "not primary evidence" in context["note"]
    context["records"][0]["result"]["product"] = "tampered"
    records = desk.arithmetic_records()
    records[0]["result"]["components"][0]["amount_usd"] = "0"
    assert desk.budget.events == original
    assert set(desk.sources) == {"fixture-sec"} and not desk.calculations


@pytest.mark.parametrize("tamper", [None, "total", "product", "source", "operation", "old_audit"])
def test_saved_finalization_revalidates_arithmetic_without_paid_recomputation(
    env, decision, monkeypatch, tamper
):
    service, broker, _ = env
    worker, original_model = team(env, decision)
    original_run = ResearchDesk.run

    def seeded_run(desk):
        seed(desk)
        return original_run(desk)

    monkeypatch.setattr(ResearchDesk, "run", seeded_run)
    context = {"symbol": "ACME", "run_key": "parent", "portfolio": {}, "quote": {"ask": "100"}}
    worker.run(context)
    source = service.store.get("parent", "research_audit")
    source["stopped"] = True
    if tamper == "total":
        source["arithmetic_records"][0]["result"]["total_usd"] = "999"
    elif tamper == "product":
        source["arithmetic_records"][1]["result"]["product"] = "999"
    elif tamper == "source":
        source["arithmetic_records"][0]["result"]["components"][0]["evidence_id"] = "unknown"
    elif tamper == "operation":
        source["arithmetic_records"][0]["event"] = "unrecognized"
    elif tamper == "old_audit":
        source.pop("arithmetic_records")
    service.store.put("parent", source, "research_audit")

    class FinalModel(TeamModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            context = json.loads(messages[1].content)
            assert context["arithmetic"]["records"] == source.get("arithmetic_records", [])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = FinalModel(decision=original_model.decision)
    recovery = DeepResearchTeam(service.cfg, service.store, Evidence(), model)
    if tamper in {"total", "product", "source", "operation"}:
        with pytest.raises(ValueError):
            recovery.run({**context, "run_key": "recovery"}, finalize_from="parent")
        assert not model.calls
    else:
        result = recovery.run({**context, "run_key": "recovery"}, finalize_from="parent")
        assert result["arithmetic_records"] == source.get("arithmetic_records", [])
        assert model.calls == ["Decision"]
        audit = service.store.get("recovery", "research_audit")
        assert audit["arithmetic_records"] == result["arithmetic_records"]
        assert audit["model_calls"] == 1 and audit["tool_calls"] == 0
    assert service.store.get("parent", "research_audit") == source and broker.submits == 0


def test_targeted_cached_reports_retain_arithmetic(env, decision, monkeypatch):
    service, _, _ = env
    worker, _ = team(env, decision, deeper=False)
    original_run = ResearchDesk.run

    def initial_arithmetic(desk):
        if desk.context["run_key"] == "baseline":
            seed(desk)
        return original_run(desk)

    monkeypatch.setattr(ResearchDesk, "run", initial_arithmetic)
    graph = build_graph(service, worker)
    first = graph.invoke({"symbol": "ACME", "run_key": "baseline", "research_only": True})
    result = graph.invoke(
        {"symbol": "ACME", "run_key": "news", "trigger": "new news evidence", "research_only": True}
    )
    assert set(result["reused_reports"]) == {"business", "valuation"}
    assert result["arithmetic_records"] == first["arithmetic_records"]
    assert service.store.get("news", "research_audit")["arithmetic_records"] == result["arithmetic_records"]


def test_byte_bound_retains_full_paged_history_without_recomputation_or_evidence_promotion(decision):
    from trader.models import Decision
    from trader.research_team import UnknownEvidence

    cfg = Settings()
    desk = ResearchDesk(
        cfg, Evidence(), {"symbol": "ACME", "portfolio": {}}, Evidence().get("ACME"), Budget(cfg), None
    )
    tools = {tool.name: tool for tool in desk.tools()}
    components = [
        {"label": "x" * 115 + str(i), "amount_usd": "1", "evidence_id": "fixture-sec"} for i in range(20)
    ]
    for _ in range(7):
        tools["sum_cash_flows"].invoke({"period_end": "2025-12-31", "components": components})
    original = copy.deepcopy(desk.budget.events)
    context = desk.arithmetic_context()
    assert context["omitted_records"] > 0
    assert len(json.dumps(context["records"]).encode()) <= 12000
    assert "NOT missing or unperformed" in context["note"]
    assert "blanket verification" in context["note"]
    chunks, offset = [], 0
    while True:
        page = tools["read_source"].invoke({"source_id": context["history_handle"], "offset": offset})
        assert page["original_source"] is False and page["kind"] == "derived_arithmetic_history"
        assert len(page["content"]) <= 6000
        chunks.append(page["content"])
        if not page["truncated"]:
            break
        offset += 6000
    assert json.loads("".join(chunks)) == desk.arithmetic_records()
    assert desk.budget.events == original and desk.budget.models == desk.budget.tools == 0
    assert context["history_handle"] not in desk.sources
    with pytest.raises(UnknownEvidence):
        desk.validate(Decision, decision.model_copy(update={"evidence_ids": [context["history_handle"]]}))


def test_evaluation_failure_preserves_arithmetic_diagnostics_without_decision(tmp_path, monkeypatch):
    from test_evaluation import inputs

    from trader.evaluation import run_case
    from trader.research_team import ResearchLimit
    from trader.store import Store

    def stopped(desk):
        next(tool for tool in desk.tools() if tool.name == "multiply_values").invoke(
            {"left": "2", "right": "3"}
        )
        raise ResearchLimit("Fixture stop after arithmetic")

    monkeypatch.setattr(ResearchDesk, "run", stopped)
    cfg = Settings(database=str(tmp_path / "ledger.sqlite"))
    store = Store(cfg.database)
    try:
        result = run_case(cfg, store, inputs()[0]["cases"][0], None, "math-failure")
        assert result["decision"] is None and result["failure_category"] == "ResearchLimit"
        assert result["arithmetic_records"][0]["result"]["product"] == "6"
        assert (
            store.get("math-failure", "evaluation_case")["arithmetic_records"] == result["arithmetic_records"]
        )
    finally:
        store.db.close()


def test_prior_thesis_cannot_bypass_bounded_validated_arithmetic_context():
    cfg = Settings()
    original = ResearchDesk(
        cfg, Evidence(), {"symbol": "ACME", "portfolio": {}}, Evidence().get("ACME"), Budget(cfg), None
    )
    multiply = next(tool for tool in original.tools() if tool.name == "multiply_values")
    for value in range(100):
        multiply.invoke({"left": str(value), "right": "3"})
    prior = {"research_arithmetic_records": original.arithmetic_records()}
    assert len(json.dumps(prior).encode()) > 12000
    untouched = copy.deepcopy(prior)
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {"prior_thesis": prior}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
    )
    assert "research_arithmetic_records" not in desk.context["portfolio"]["prior_thesis"]
    assert desk.final_context()["arithmetic"]["records"] == []
    desk.restore_arithmetic(desk.prior["research_arithmetic_records"])
    context = desk.final_context()
    assert "research_arithmetic_records" not in context["portfolio"]["prior_thesis"]
    assert len(context["arithmetic"]["records"]) == 20
    assert context["arithmetic"]["omitted_records"] == 80
    assert prior == untouched
