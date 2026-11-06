from datetime import timedelta

import pytest
from conftest import AT

from trader.accounting import LedgerError
from trader.models import D
from trader.reports import daily_report, portfolio_report
from trader.research import build_graph
from trader.runner import Runner


class Evidence:
    def get(self, symbol, fresh=False):
        return {
            "symbol": symbol,
            "fetched_at": AT.isoformat(),
            "sources": [{"id": "fixture-sec-1", "content": "Offline fixture"}],
        }


class FixtureTeam:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0
        self.gaps = False

    def run(self, context):
        self.calls += 1
        ref = {"evidence_ids": ["fixture-sec-1"]}
        reports = {
            "business": {**ref, "business_quality": "Fixture quality"},
            "valuation": {**ref, "bear_value": "70", "base_value": "140", "bull_value": "170"},
            "catalysts": {**ref, "why_now": "Fixture catalyst"},
            "adversarial": {**ref, "unresolved_material_gaps": self.gaps},
        }
        return {
            "decision": self.decision.model_dump(mode="json"),
            "research": reports["business"],
            "valuation": reports["valuation"],
            "catalysts": reports["catalysts"],
            "critique": reports["adversarial"],
            "team_reports": reports,
            "evidence": Evidence().get(context["symbol"]),
        }


def test_full_graph_research_risk_execution_and_report(env, decision):
    service, broker, _ = env
    model = FixtureTeam(decision)
    graph = build_graph(service, model)
    result = graph.invoke({"symbol": "ACME", "run_key": "graph-1"})
    assert model.calls == 1
    assert result["execution"]["status"] == "submitted"
    assert result["report"]["positions"] == []
    broker.fixture_fill(result["execution"]["client_order_id"])
    result2 = graph.invoke({"symbol": "ACME", "run_key": "graph-1"})
    assert broker.submits == 1
    assert model.calls == 1  # Completed research is immutable and not billed again on restart.
    assert result2["report"]["positions"][0]["qty"] == "10"
    assert (
        service.store.events("research_decision")[0]["body"]["evidence"]["sources"][0]["id"]
        == "fixture-sec-1"
    )


def test_critic_can_veto_buy_but_cannot_override_risk(env, decision):
    service, broker, _ = env
    model = FixtureTeam(decision)
    model.gaps = True
    result = build_graph(service, model).invoke({"symbol": "ACME", "run_key": "veto"})
    assert result["decision"]["action"] == "WATCH"
    assert broker.submits == 0


def test_unknown_evidence_cannot_generate_order(env, decision):
    service, broker, _ = env
    model = FixtureTeam(decision.model_copy(update={"evidence_ids": ["invented"]}))
    with pytest.raises(ValueError, match="unknown evidence"):
        build_graph(service, model).invoke({"symbol": "ACME", "run_key": "fake"})
    assert broker.submits == 0


def test_price_tick_below_threshold_does_not_research(env, decision):
    service, _, data = env
    service.store.put(
        "ACME",
        {
            "reviewed_at": (AT - timedelta(hours=1)).isoformat(),
            "decision": decision.model_dump(mode="json"),
            "reference_price": "100",
            "valuation": {"base_value": "140", "bear_value": "70"},
        },
        "thesis",
    )
    runner = Runner(service, None)
    data.ask = D("101")
    assert runner.due("ACME", "intraday") == (False, "no material trigger")
    data.ask = D("106")
    assert runner.due("ACME", "intraday")[0]


def test_review_deadline_reassesses_does_not_sell(env, decision):
    service, broker, _ = env
    service.store.put(
        "ACME",
        {
            "reviewed_at": (AT - timedelta(days=1)).isoformat(),
            "decision": decision.model_copy(update={"next_review": AT - timedelta(hours=1)}).model_dump(
                mode="json"
            ),
            "reference_price": "100",
            "valuation": {},
        },
        "thesis",
    )
    assert Runner(service, None).due("ACME", "intraday") == (True, "review deadline")
    assert broker.submits == 0


def test_preopen_candidate_rechecked_at_open(env, decision):
    service, _, _ = env
    service.store.put(
        "ACME",
        {
            "reviewed_at": (AT - timedelta(hours=1)).isoformat(),
            "decision": decision.model_dump(mode="json"),
            "reference_price": "100",
            "valuation": {},
            "execution": {"status": "rejected", "reasons": ["Market closed or broker clock stale"]},
        },
        "thesis",
    )
    assert Runner(service, None).due("ACME", "intraday")[0]


def test_daily_benchmarks_matching_dates_and_costs_separate(env, decision, monkeypatch):
    service, broker, _ = env
    cid = service.execute(decision, "entry")["client_order_id"]
    broker.fixture_fill(cid)
    service.reconcile()
    service.store.log("model_cost", {"estimated_usd": "3.00", "usage": {"input_tokens": 100}})
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(hours=6))
    first = daily_report(service)
    assert first["benchmarks"] == {"SPY": D(0), "QQQ": D(0)}
    assert first["market_data"]["feed"] == "iex"
    assert first["market_data"]["coverage"] == "IEX exchange only"
    assert any("IEX quotes" in note for note in first["limitations"])
    assert service.store.items("daily")[0]["feed"] == "iex"
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(days=1, hours=6))
    second = daily_report(service)
    assert second["benchmarks"] == {"SPY": D(".01"), "QQQ": D(".02")}
    assert second["matching_period_return"] == 0
    assert second["model_cost_usd_estimate"] == D("3")
    assert second["equity"] == D("100000")
    assert second["max_drawdown"] == 0
    assert second["positions"]  # Experiment reporting doesn't liquidate.


def test_stale_report_discloses_reconciliation_failure(env):
    service, broker, _ = env
    broker.raw_account["cash"] = "1"
    with pytest.raises(LedgerError):
        service.reconcile()
    assert portfolio_report(service)["reconciliation"]["ok"] is False


def test_scheduler_records_interruption_without_backfill(env):
    service, broker, _ = env
    service.store.put("heartbeat", {"at": (AT - timedelta(hours=1)).isoformat()})
    broker.open = False
    result = Runner(service, None).tick()
    assert result["status"] == "reconciled; outside research cadence"
    assert service.store.events("interruption")
    assert not service.store.items("daily")


def test_first_day_loss_counts_toward_experiment_drawdown(env, decision, monkeypatch):
    service, broker, data = env
    cid = service.execute(decision, "entry")["client_order_id"]
    broker.fixture_fill(cid)
    old_close = data.session_close
    data.session_close = lambda symbol, day: D("80") if symbol == "ACME" else old_close(symbol, day)
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(hours=6))
    report = daily_report(service)
    assert report["max_drawdown"] == D(".002")
    assert report["experiment_price_return"] == D("-.002")
    assert report["matching_period_return"] == 0


@pytest.mark.parametrize("fault", ["future_review", "future_quote", "future_news"])
def test_future_inputs_cannot_debounce_forever_or_trigger_price_research(env, decision, fault):
    service, broker, data = env
    reviewed = AT + timedelta(days=1) if fault == "future_review" else AT - timedelta(hours=1)
    service.store.put(
        "ACME",
        {
            "reviewed_at": reviewed.isoformat(),
            "decision": decision.model_dump(mode="json"),
            "reference_price": "100",
            "valuation": {},
        },
        "thesis",
    )
    if fault == "future_quote":
        data.at = AT + timedelta(minutes=1)
        data.ask = D("110")
    if fault == "future_news":
        data.news_items = [{"created_at": (AT + timedelta(days=1)).isoformat()}]
    result = Runner(service, None).due("ACME", "intraday")
    if fault == "future_review":
        assert result == (True, "future-dated thesis requires fresh review")
    else:
        assert result[0] is False
    assert broker.submits == 0
