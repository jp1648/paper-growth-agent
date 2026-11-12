"""Bounded daily-budget override for explicit, nonexecuting research."""

from types import SimpleNamespace

import pytest

from trader import cli
from trader.models import dec
from trader.research import build_graph
from trader.research_team import DeepResearchTeam, ResearchLimit
from trader.spending import DailyBudgetExceeded, DailyReservation, spending_status


@pytest.mark.parametrize("flags", [[], ["--defer-execution"], ["--max-cost", "5"]])
def test_testing_research_rejects_incomplete_opt_in_before_config(monkeypatch, flags):
    monkeypatch.setattr(cli.Settings, "load", lambda _: pytest.fail("No config/providers before opt-in"))
    with pytest.raises(SystemExit, match="2"):
        cli.main(["research", "ACME", "--run-key", "test", "--testing", *flags])


def test_max_cost_requires_testing(monkeypatch):
    monkeypatch.setattr(cli.Settings, "load", lambda _: pytest.fail("No config/providers before opt-in"))
    with pytest.raises(SystemExit, match="2"):
        cli.main(["research", "ACME", "--run-key", "test", "--defer-execution", "--max-cost", "5"])


def test_cli_testing_changes_only_in_memory_cap_and_passes_deferred_context(env, monkeypatch):
    service, broker, data = env
    original = service.cfg.model_dump()
    captured = {}
    monkeypatch.setattr(cli.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(cli, "Store", lambda _: service.store)
    monkeypatch.setattr(cli, "AlpacaBroker", lambda _: broker)
    monkeypatch.setattr(cli, "AlpacaData", lambda _: data)

    def team(cfg, store, provider, *, testing):
        captured.update(cfg=cfg, testing=testing)
        return object()

    def invoke(context):
        captured["context"] = context
        return {"status": "deferred"}

    monkeypatch.setattr(cli, "DeepResearchTeam", team)
    monkeypatch.setattr(cli, "build_graph", lambda *args, **kwargs: SimpleNamespace(invoke=invoke))
    assert (
        cli.main(
            ["research", "ACME", "--run-key", "bounded", "--testing", "--defer-execution", "--max-cost", "3"]
        )
        == 0
    )
    assert captured["testing"] is True
    assert captured["context"]["research_only"] is True
    assert captured["context"]["fresh"] is True
    assert captured["cfg"].research_max_cost_usd == dec(3)
    assert service.cfg.model_dump() == original


def test_testing_team_keeps_history_and_normal_limits_without_orders(env, decision):
    from test_research_team import team

    service, broker, _ = env
    service.cfg.research_daily_cost_usd = dec(6)
    worker, model = team(env, decision)
    normal = DailyReservation(service.store, service.cfg, "previous")
    normal.finish(dec(6))
    original = service.store.get(normal.key, "research_spend", scope="global")
    context = {"symbol": "ACME", "run_key": "bounded-held-review", "research_only": True, "fresh": True}
    with pytest.raises(DailyBudgetExceeded):
        worker.run(context)
    assert not model.calls
    worker = DeepResearchTeam(service.cfg, service.store, worker.provider, model, testing=True)
    result = build_graph(service, worker).invoke(context)
    assert result["execution"]["status"] == "deferred"
    assert broker.submits == 0
    assert service.store.get(normal.key, "research_spend", scope="global") == original
    assert spending_status(service.store, dec(6))["charged_or_reserved_usd"] > 6
    audit = service.store.get(context["run_key"], "research_audit")
    assert audit["testing_budget_override"] is True and not audit["unrestricted_research"]
    assert dec(audit["estimated_usd"]) <= service.cfg.research_max_cost_usd
    assert audit["model_calls"] <= service.cfg.research_max_model_calls
    assert audit["tool_calls"] <= service.cfg.research_max_tool_calls


def test_testing_team_rejects_execution_and_still_enforces_per_run_cost(env, decision):
    from test_research_team import team

    service, broker, _ = env
    original, model = team(env, decision)
    cfg = service.cfg.model_copy(update={"research_max_cost_usd": dec("0.000000001")})
    worker = DeepResearchTeam(cfg, service.store, original.provider, model, testing=True)
    with pytest.raises(ValueError, match="deferred"):
        worker.run({"symbol": "ACME", "run_key": "invalid"})
    assert not model.calls
    with pytest.raises(ResearchLimit):
        build_graph(service, worker).invoke({"symbol": "ACME", "run_key": "tiny-cap", "research_only": True})
    assert not model.calls and broker.submits == 0
    assert service.store.get("tiny-cap", "research_audit")["stopped"]


@pytest.mark.parametrize("cap", ["0", "-1", "NaN", "Infinity", "101", "invalid"])
def test_invalid_testing_cap_rejected_before_store_or_broker(env, monkeypatch, cap):
    service, _, _ = env
    monkeypatch.setattr(cli.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(cli, "Store", lambda _: pytest.fail("Invalid cap must not open a ledger"))
    monkeypatch.setattr(cli, "AlpacaBroker", lambda _: pytest.fail("Invalid cap must not create broker"))
    assert (
        cli.main(
            [
                "research",
                "ACME",
                "--run-key",
                "invalid-cap",
                "--testing",
                "--defer-execution",
                "--max-cost",
                cap,
            ]
        )
        == 1
    )
