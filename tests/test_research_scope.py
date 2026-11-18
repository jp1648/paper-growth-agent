import pytest
from test_graph_reporting import FixtureTeam
from test_sec_facts import provider

from trader.config import Security, Settings
from trader.research import build_graph
from trader.runner import Runner


def test_scope_rejects_overlap_and_bad_symbols():
    security = Security(sector="Test", cik="1")
    with pytest.raises(ValueError, match="overlap"):
        Settings(universe={"ACME": security}, research_only_universe={"ACME": security})
    with pytest.raises(ValueError):
        Settings(research_only_universe={"bad symbol": security})


def test_research_only_collector_accepts_canonical_company_without_trade_permission(env, monkeypatch):
    service, _, _ = env
    service.cfg.research_only_universe = {"READ": Security(sector="Test", cik="1")}
    source = provider(env, monkeypatch)
    assert source.fetch("READ")["sources"][0]["content"]["cik"] == "0000000001"
    assert "READ" not in service.cfg.universe
    with pytest.raises(ValueError, match="outside"):
        source.fetch("UNKNOWN")


def test_graph_never_executes_research_only_even_when_flag_false(env, decision, monkeypatch):
    service, broker, _ = env
    security = service.cfg.universe.pop("ACME")
    service.cfg.research_only_universe = {"ACME": security}
    original_execute = service.execute

    def forbidden(*args, **kwargs):
        raise AssertionError("Research-only graph called execution")

    monkeypatch.setattr(service, "execute", forbidden)
    graph = build_graph(service, FixtureTeam(decision))
    for i in range(2):
        result = graph.invoke({"symbol": "ACME", "run_key": "research-only", "research_only": False})
        assert result["execution"]["status"] == "research_only"
    assert broker.submits == 0 and not service.store.items("intent")
    # Direct execution still goes through the unchanged allowlist guard.
    result = original_execute(decision.model_dump(mode="json"), "direct-outside", {})
    assert result["status"] == "rejected"
    assert "Security lacks operator-verified common-stock and sector metadata" in result["reasons"]
    assert broker.submits == 0


def test_runner_schedules_extra_as_research_only(env):
    service, _, _ = env
    service.cfg.research_only_universe = {"READ": Security(sector="Test", cik="1")}

    class Capture:
        def __init__(self):
            self.calls = []

        def invoke(self, state):
            self.calls.append(state)
            return {
                "decision": {"action": "WATCH", "confidence": "0"},
                "execution": {"status": "research_only"},
            }

    graph = Capture()
    Runner(service, graph).research_batch("scope-test", "intraday")
    assert {c["symbol"] for c in graph.calls} == {"ACME", "OTHER", "READ"}
    assert next(c for c in graph.calls if c["symbol"] == "READ")["research_only"] is True
    assert next(c for c in graph.calls if c["symbol"] == "ACME")["research_only"] is False
