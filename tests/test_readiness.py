"""Production review regressions: every broker/model response here is an offline fixture."""

import sqlite3
from datetime import timedelta

import httpx
import openai
import pytest
from conftest import AT
from test_graph_reporting import FixtureTeam

from trader.accounting import LedgerError, replay
from trader.alpaca import safe_error
from trader.models import dec
from trader.reports import daily_report
from trader.research import build_graph
from trader.research_team import Budget, ResearchLimit
from trader.runner import Runner


def test_expiration_cancellation_survives_reconciliation_failure(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "old-order")["client_order_id"]
    broker.orders[cid]["created_at"] = (AT - timedelta(hours=1)).isoformat()
    broker.raw_account["cash"] = "1"  # Reconciliation cannot converge.
    with pytest.raises(LedgerError):
        Runner(service, None).maintain()
    assert broker.cancels == [broker.orders[cid]["id"]]
    assert service.reservation_summary() == dec(1001)
    assert not service.store.get("reconciliation")["ok"]


def test_kill_never_cancels_orders_in_changed_account(env, decision):
    service, broker, _ = env
    service.execute(decision, "old-account")
    broker.raw_account["id"] = "different-account"
    with pytest.raises(LedgerError, match="identity"):
        service.kill("pause")
    assert service.kill_path.exists()
    assert not broker.cancels


def test_preopen_decision_executes_without_rebuying_research(env, decision):
    service, broker, data = env
    model = FixtureTeam(decision)
    graph = build_graph(service, model)
    first = graph.invoke({"symbol": "ACME", "run_key": "preopen:ACME", "research_only": True})
    assert first["execution"]["status"] == "deferred"
    assert broker.submits == 0
    assert not service.store.items("intent")
    service.cfg.research_daily_cost_usd = dec(0)
    result = Runner(service, graph).research_batch("open", "intraday")
    assert next(x for x in result["outcomes"] if x["symbol"] == "ACME")["execution"] == "submitted"
    assert model.calls == 1
    assert broker.submits == 1
    # Restart with no quote service must still resolve the original order, never submit twice.
    data.quote = lambda symbol: (_ for _ in ()).throw(RuntimeError("quote outage"))
    graph.invoke({"symbol": "ACME", "run_key": "preopen:ACME"})
    assert broker.submits == 1


def test_new_news_prevents_deferred_execution_without_budget(env, decision):
    service, broker, data = env
    graph = build_graph(service, FixtureTeam(decision))
    graph.invoke({"symbol": "ACME", "run_key": "preopen", "research_only": True})
    service.cfg.research_daily_cost_usd = dec(0)
    data.news_items = [{"created_at": (AT + timedelta(seconds=1)).isoformat()}]
    Runner(service, graph).research_batch("open", "intraday")
    assert broker.submits == 0


def test_paused_trading_still_produces_close_report(env, monkeypatch):
    service, broker, _ = env
    service.kill("review")
    close = AT + timedelta(hours=6)
    monkeypatch.setattr("trader.runner.now", lambda: close)
    monkeypatch.setattr("trader.reports.now", lambda: close)
    broker.at, broker.open = close, False
    result = Runner(service, None).tick()
    assert result["close_equity"] == dec(100000)
    assert result["kill_switch"] is True


def test_kill_blocks_manual_research_before_model_call(env, decision):
    service, _, _ = env
    model = FixtureTeam(decision)
    service.kill("review")
    with pytest.raises(RuntimeError, match="research paused"):
        build_graph(service, model).invoke({"symbol": "ACME", "run_key": "paused"})
    assert model.calls == 0


def test_ledger_sorts_absolute_times_not_timestamp_strings():
    buy = {
        "id": "buy",
        "activity_type": "FILL",
        "side": "buy",
        "symbol": "ACME",
        "qty": "1",
        "price": "100",
        "order_id": "1",
        "transaction_time": "2026-10-21T15:00:00Z",
    }
    sell = {
        **buy,
        "id": "sell",
        "side": "sell",
        "price": "110",
        "order_id": "2",
        "transaction_time": "2026-10-21T11:01:00-04:00",
    }
    audit = replay("1000", [sell, buy], {})
    assert audit["cash"] == dec(1010)
    assert audit["shares"]["ACME"] == 0
    assert audit["realized_average_cost"] == dec(10)


def test_report_is_immutable_and_rejects_mixed_benchmark_feed(env, monkeypatch):
    service, _, data = env
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(hours=6))
    first = daily_report(service)
    data.session_close = lambda *args: (_ for _ in ()).throw(RuntimeError("outage"))
    assert dec(daily_report(service)["close_equity"]) == first["close_equity"]
    assert len(service.store.events("daily_report")) == 1
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(days=1, hours=6))
    service.cfg.feed = "sip"
    with pytest.raises(ValueError, match="feed changed"):
        daily_report(service)


def test_zero_comparison_equity_does_not_break_reporting(env, monkeypatch):
    service, broker, _ = env
    # A fully withdrawn experiment has no denominator for subsequent percentage returns.
    activity = {"id": "withdraw", "activity_type": "CSW", "date": "2026-10-21", "net_amount": "-100000"}
    broker.activities.append(activity)
    broker.raw_account.update(cash="0", equity="0")
    service.store.put(
        "withdraw", {"id": "withdraw", "cash": "-100000", "reason": "operator withdrawal"}, "adjustment"
    )
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(hours=6))
    daily_report(service)
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(days=1, hours=6))
    result = daily_report(service)
    assert result["experiment_price_return"] is None
    assert result["daily_equity"][-1]["return"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "OTHER"},
        {"side": "sell"},
        {"qty": "100"},
        {"filled_qty": "-1"},
        {"filled_qty": "11"},
        {"limit_price": "1000"},
        {"type": "market"},
        {"extended_hours": True},
        {"time_in_force": "gtc"},
        {"replaced_by": "outside-order"},
    ],
)
def test_changed_broker_order_pauses_execution(env, decision, change):
    service, broker, _ = env
    cid = service.execute(decision, "approved")["client_order_id"]
    broker.orders[cid].update(change)
    with pytest.raises(LedgerError, match="approved intent"):
        service.reconcile()
    assert not service.store.get("reconciliation")["ok"]


def test_mismatched_submission_keeps_unknown_intent(env, decision):
    service, broker, _ = env
    original = broker.submit

    def corrupt(payload):
        order = original(payload)
        order["client_order_id"] = "unexpected"
        return order

    broker.submit = corrupt
    with pytest.raises(LedgerError):
        service.execute(decision, "mismatch")
    assert service.store.items("intent")[0]["status"] == "unknown"
    assert service.reservation_summary() == dec(1001)
    broker.submit = original
    service.reconcile()  # Original broker order is found by stable client ID.
    assert service.store.get("reconciliation")["ok"]
    assert broker.submits == 1


def test_unapproved_external_execution_cannot_be_silently_adopted(env, decision):
    service, broker, _ = env
    broker.submit(
        {
            "symbol": "ACME",
            "client_order_id": "external",
            "qty": "10",
            "side": "buy",
            "limit_price": "100",
            "type": "limit",
            "extended_hours": False,
            "time_in_force": "day",
        }
    )
    broker.fixture_fill("external")
    with pytest.raises(LedgerError, match="no matching approved order"):
        service.reconcile()


def test_wal_backup_restores_history_and_has_private_permissions(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "backup")["client_order_id"]
    broker.fixture_fill(cid)
    service.reconcile()
    saved = service.store.backup()
    with sqlite3.connect(saved) as restored:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT COUNT(*) FROM documents WHERE kind='activity'").fetchone()[0] == 1
        assert (
            restored.execute("SELECT body FROM documents WHERE scope='global' AND key='active'").fetchone()
            == service.store.db.execute(
                "SELECT body FROM documents WHERE scope='global' AND key='active'"
            ).fetchone()
        )
    assert saved.stat().st_mode & 0o077 == 0
    assert service.store.path.stat().st_mode & 0o077 == 0
    assert service.kill_path.parent.stat().st_mode & 0o077 == 0
    assert service.store.backup() == saved


def test_failed_research_does_not_starve_other_symbols(env):
    service, _, _ = env

    class Fail:
        def __init__(self):
            self.calls = []

        def invoke(self, state):
            self.calls.append(state["symbol"])
            raise RuntimeError("fixture outage")

    model = Fail()
    runner = Runner(service, model)
    runner.research_batch("slot", "preopen")
    assert model.calls == ["ACME", "OTHER"]
    runner.research_batch("slot", "preopen")
    assert model.calls == ["ACME", "OTHER"]  # Persistent error cooldown, even for stream wakeups.


def test_journal_filter_is_parameterized(env):
    service, _, _ = env
    assert service.store.events("'; DROP TABLE journal; --") == []
    assert service.store.events(("experiment_initialized", "reconciled"))


def test_investigation_cannot_make_new_calls_on_yesterdays_budget(env, monkeypatch):
    service, _, _ = env
    budget = Budget(service.cfg)
    monkeypatch.setattr("trader.research_team.now", lambda: AT + timedelta(days=1))
    with pytest.raises(ResearchLimit):
        budget.check()


@pytest.mark.parametrize(
    "code",
    [
        "insufficient_quota",
        "rate_limit_exceeded",
        "billing_not_active",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "slow_down",
    ],
)
def test_provider_error_keeps_safe_classification_without_body(code):
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
    error = openai.RateLimitError("sensitive response body", response=response, body={"code": code})
    message = safe_error(error)
    assert code in message
    assert "sensitive" not in message


def test_decision_retains_execution_time_risk_evidence(env, decision):
    service, _, _ = env
    result = service.execute(decision, "audited-gate")
    saved = service.store.get(result["client_order_id"], "intent")["risk_context"]
    assert dec(saved["snapshot"]["account"]["cash"]) == 100000
    assert saved["asset"]["symbol"] == "ACME"
    assert saved["clock"]["is_open"] is True
    assert saved["settings"]["max_position"] == "0.10"
    assert "sec_user_agent" not in saved["settings"]
    service.cfg.max_position = dec("0.01")
    assert service.store.get(result["client_order_id"], "intent")["risk_context"] == saved


def test_news_previews_keep_full_evidence_available(env):
    from trader.research_team import ResearchDesk

    service, _, _ = env
    source = {
        "id": "news-test",
        "content": {"headline": "Headline", "summary": "Summary", "content": "x" * 20000},
    }
    desk = ResearchDesk(
        service.cfg,
        None,
        {"symbol": "ACME", "portfolio": {}},
        {"sources": [source]},
        Budget(service.cfg),
        None,
    )
    preview = desk.source_previews(desk.sources.values())[0]
    assert len(str(preview)) < 1000
    reader = next(t for t in desk.tools() if t.name == "read_source")
    assert reader.invoke({"source_id": "news-test"})["truncated"]
    assert reader.invoke({"source_id": "news-test", "offset": 6000})["offset"] == 6000
    assert desk.sources["news-test"] == source


def test_financial_previews_require_original_evidence_reads(env):
    from trader.research_team import ResearchDesk

    service, _, _ = env
    original = {"id": "facts", "content": {"entity": "ACME", "facts": {"Revenue": {"USD": [{"val": 100}]}}}}
    desk = ResearchDesk(
        service.cfg,
        None,
        {"symbol": "ACME", "portfolio": {}},
        {"sources": [original]},
        Budget(service.cfg),
        None,
    )
    preview = desk.source_previews(desk.sources.values())[0]
    assert preview["content"]["available_facts"] == ["Revenue"]
    assert "facts" not in preview["content"]
    reader = next(t for t in desk.tools() if t.name == "read_source")
    assert "100" in reader.invoke({"source_id": "facts"})["content"]
    assert desk.sources["facts"] == original
