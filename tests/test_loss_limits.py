"""Cumulative loss controls use audited flows and survive restart without liquidation."""

from datetime import datetime

import httpx
import pytest

from trader.health import check_health
from trader.models import dec
from trader.reports import portfolio_report
from trader.risk import daily_loss
from trader.runner import Runner
from trader.service import Service
from trader.store import Store


def fee(service, broker, amount):
    broker.activities.append(
        {"id": "loss-fee", "activity_type": "FEE", "net_amount": str(-dec(amount)), "date": "2026-10-21"}
    )
    broker.raw_account["cash"] = str(dec(broker.raw_account["cash"]) - dec(amount))
    broker.raw_account["equity"] = str(dec(broker.raw_account["equity"]) - dec(amount))
    service.reconcile()


@pytest.mark.parametrize("amount,blocked", [("9999.99", False), ("10000", True), ("10000.01", True)])
def test_cumulative_loss_boundary(env, decision, amount, blocked):
    service, broker, _ = env
    service.cfg.max_daily_loss_fraction = dec("1")
    fee(service, broker, amount)
    result = service.execute(decision, "after-loss")
    assert result["status"] == ("rejected" if blocked else "submitted")
    assert broker.submits == (0 if blocked else 1)
    assert service.store.get("loss_limit")["latched"] is blocked
    assert portfolio_report(service)["loss_limit"]["latched"] is blocked


def test_deposits_do_not_dilute_or_clear_existing_loss_latch(env, decision):
    service, broker, _ = env
    fee(service, broker, "10000")
    broker.activities.append(
        {"id": "contribution", "activity_type": "CSD", "net_amount": "100000", "date": "2026-10-21"}
    )
    service.store.put(
        "contribution",
        {"id": "contribution", "cash": "100000", "reason": "Fixture contribution"},
        "adjustment",
    )
    broker.raw_account.update(cash="190000", equity="190000")
    service.reconcile()
    assert service.store.get("loss_limit")["pnl"] == "-10000"
    assert service.store.get("loss_limit")["threshold_usd"] == "10000.00"
    with pytest.raises(ValueError, match="remains breached"):
        service.resume()
    assert service.execute(decision, "deposit-cannot-reset")["status"] == "rejected"
    assert broker.submits == 0


def test_loss_latch_survives_restart_recovery_requires_explicit_resume(env, decision):
    service, broker, data = env
    fee(service, broker, "10000")
    broker.activities.append(
        {"id": "recovery", "activity_type": "DIV", "net_amount": "10001", "date": "2026-10-22"}
    )
    broker.raw_account.update(cash="100001", equity="100001")
    reopened = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, reopened, broker, data)
        recovered.reconcile()
        assert not reopened.get("loss_limit")["breached"]
        assert reopened.get("loss_limit")["latched"]
        assert recovered.execute(decision, "recovered-still-blocked")["status"] == "rejected"
        recovered.resume()
        assert not reopened.get("loss_limit")["latched"]
        assert recovered.execute(decision, "operator-resumed")["status"] == "submitted"
        assert len(reopened.events("loss_limit_breached")) == 1
        assert len(reopened.events("loss_limit_released")) == 1
    finally:
        reopened.db.close()


def test_protective_exit_retains_gates_after_loss_limit(env, decision):
    service, broker, _ = env
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    fee(service, broker, "10000")
    assert len(broker.orders) == 1  # Reconciliation never liquidates automatically.
    result = service.execute(decision.model_copy(update={"action": "SELL", "exit_reason": "risk"}), "protect")
    assert result["status"] == "submitted"
    assert service.store.get("loss_limit")["latched"]


def test_withdrawal_is_not_an_investment_loss(env):
    service, broker, _ = env
    broker.activities.append(
        {"id": "withdrawal", "activity_type": "CSW", "net_amount": "-20000", "date": "2026-10-21"}
    )
    service.store.put(
        "withdrawal", {"id": "withdrawal", "cash": "-20000", "reason": "Fixture withdrawal"}, "adjustment"
    )
    broker.raw_account.update(cash="80000", equity="80000")
    service.reconcile()
    assert service.store.get("loss_limit")["pnl"] == "0"
    assert not service.store.get("loss_limit")["latched"]


def test_unrealized_loss_triggers_limit_and_health_without_research(env, decision):
    service, broker, _ = env
    service.cfg.max_cumulative_loss_fraction = dec("0.005")
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    broker.positions[0].update(market_value="500", unrealized_pl="-500")
    broker.raw_account["equity"] = "99500"
    service.cfg.research_daily_cost_usd = dec(0)
    Runner(service, None).maintain()
    assert service.store.get("loss_limit")["pnl"] == "-500"
    with pytest.raises(ValueError, match="Portfolio loss limit"):
        check_health(service.cfg)
    assert any("Portfolio loss limit" in item for item in portfolio_report(service)["risk_reviews"])
    assert len(broker.orders) == 1


@pytest.mark.parametrize("amount,blocked", [("1999.99", False), ("2000", True), ("2000.01", True)])
def test_daily_loss_boundary(env, decision, amount, blocked):
    service, broker, _ = env
    fee(service, broker, amount)
    assert service.execute(decision, "daily-boundary")["status"] == ("rejected" if blocked else "submitted")
    limit = service.store.get("loss_limit")
    assert not limit["breached"]
    assert limit["daily"]["breached"] is blocked


@pytest.mark.parametrize(
    "field,value",
    [
        ("last_equity", None),
        ("last_equity", "NaN"),
        ("last_equity", "0"),
        ("last_equity", "Infinity"),
        ("balance_asof", "2026-10-19"),
        ("balance_asof", "2026-10-21"),
        ("intraday_adjustments", "100"),
    ],
)
def test_invalid_daily_baseline_blocks_entry_but_not_owned_exit(env, decision, field, value):
    service, broker, _ = env
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    broker.raw_account[field] = value
    result = service.execute(decision.model_copy(update={"action": "ADD"}), "bad-baseline")
    assert result["status"] == "rejected"
    assert not service.store.get("loss_limit")["daily"]["valid"]
    result = service.execute(decision.model_copy(update={"action": "SELL", "exit_reason": "risk"}), "exit")
    assert result["status"] == "submitted"


def test_same_day_baseline_revision_cannot_silently_reset_limit(env, decision):
    service, broker, _ = env
    broker.raw_account["last_equity"] = "99999"
    for _ in range(2):
        service.reconcile()
        assert not service.store.get("loss_limit")["daily"]["valid"]
    assert service.execute(decision, "changed-baseline")["status"] == "rejected"
    assert service.store.get("2026-10-21", "daily_loss_baseline")["equity"] == "100000"


def test_baseline_numeric_format_is_not_a_revision(env):
    service, broker, _ = env
    broker.raw_account["last_equity"] = "100000.00"
    service.reconcile()
    assert service.store.get("loss_limit")["daily"]["valid"]


def test_missing_calendar_baseline_fails_closed_and_health_flags_it(env, decision, monkeypatch):
    service, broker, _ = env
    original = broker.calendar
    monkeypatch.setattr(broker, "calendar", lambda start, end: [] if start != end else original(start, end))
    Runner(service, None).maintain()
    with pytest.raises(ValueError, match="Daily loss baseline"):
        check_health(service.cfg)
    assert service.execute(decision, "missing-calendar")["status"] == "rejected"


def test_daily_breach_latches_across_new_day_and_restart(env, monkeypatch):
    service, broker, data = env
    fee(service, broker, "2000")
    broker.raw_account.update(last_equity="98000", balance_asof="2026-10-21")
    monkeypatch.setattr("trader.service.now", lambda: datetime.fromisoformat("2026-10-22T15:00:00+00:00"))
    reopened = Store(service.cfg.database)
    try:
        fresh = Service(service.cfg, reopened, broker, data)
        fresh.reconcile()
        limit = reopened.get("loss_limit")
        assert limit["daily"]["pnl"] == "0" and limit["latched"]
        fresh.resume()
        assert not reopened.get("loss_limit")["latched"]
    finally:
        reopened.db.close()


def test_daily_baseline_covers_weekend_dst_and_capital_flows():
    account = {
        "last_equity": "100000",
        "equity": "108000",
        "balance_asof": "2026-10-30",
        "intraday_adjustments": "0",
    }
    calendar = [{"date": "2026-10-30", "close": "16:00"}]
    at = datetime.fromisoformat("2026-11-02T15:00:00+00:00")
    activities = [{"activity_type": "CSD", "date": "2026-11-01", "net_amount": "10000"}]
    result = daily_loss(account, activities, calendar, at, dec(".02"))
    assert result["valid"] and result["breached"] and result["pnl"] == dec(-2000)
    # Friday close was 20:00 UTC before the DST change, not 21:00 UTC.
    activities[0].update(transaction_time="2026-10-30T20:30:00+00:00")
    assert daily_loss(account, activities, calendar, at, dec(".02"))["breached"]
    activities[0].pop("transaction_time")
    activities[0]["date"] = "2026-10-30"
    assert not daily_loss(account, activities, calendar, at, dec(".02"))["valid"]
    calendar[0]["close"] = "13:00"
    assert not daily_loss(account, [], calendar, at, dec(".02"))["valid"]


@pytest.mark.parametrize(
    "stamp,valid,pnl",
    [
        ("2026-10-20T19:59:59+00:00", True, "-10000"),
        ("2026-10-20T20:00:01+00:00", True, "0"),
        ("2026-10-22T20:00:01+00:00", False, None),
    ],
)
def test_withdrawal_cutoff_and_future_activity(stamp, valid, pnl):
    account = {
        "last_equity": "100000",
        "equity": "90000",
        "balance_asof": "2026-10-20",
        "intraday_adjustments": "0",
    }
    activities = [{"activity_type": "CSW", "transaction_time": stamp, "net_amount": "-10000"}]
    result = daily_loss(
        account,
        activities,
        [{"date": "2026-10-20", "close": "16:00"}],
        datetime.fromisoformat("2026-10-21T15:00:00+00:00"),
        dec(".02"),
    )
    assert result["valid"] is valid
    if valid:
        assert result["pnl"] == dec(pnl)


def test_loss_maintenance_cancels_buy_and_reconciles_racing_fill(env, decision, monkeypatch):
    service, broker, _ = env
    cid = service.execute(decision, "working-buy")["client_order_id"]
    # Detect the breach during maintenance, not through a prior explicit reconcile.
    broker.activities.append(
        {"id": "loss", "activity_type": "FEE", "net_amount": "-2000", "date": "2026-10-21"}
    )
    broker.raw_account.update(cash="98000", equity="98000")
    original_cancel = broker.cancel

    def racing_cancel(order_id):
        original_cancel(order_id)
        broker.fixture_fill(cid)

    monkeypatch.setattr(broker, "cancel", racing_cancel)
    Runner(service, None).maintain()
    assert len(broker.cancels) == 1
    assert service.store.get(cid, "order")["status"] == "filled"
    assert service.store.get("snapshot")["positions"][0]["qty"] == "10"
    assert service.reservation_summary() == 0
    assert broker.submits == 1  # No automatic liquidation or replacement.


@pytest.mark.parametrize("cause", ["loss", "missing_baseline"])
def test_loss_cancel_pending_retains_reservation_and_does_not_spam(env, decision, cause):
    service, broker, _ = env
    cid = service.execute(decision, "partial-buy")["client_order_id"]
    broker.fixture_fill(cid, "4", status="partially_filled")
    if cause == "loss":
        fee(service, broker, "2000")
    else:
        broker.raw_account.pop("last_equity")
    Runner(service, None).maintain()
    Runner(service, None).maintain()
    assert len(broker.cancels) == 1
    assert service.store.get(cid, "order")["status"] == "pending_cancel"
    assert service.reservation_summary() == dec("601")
    broker.orders[cid]["status"] = "canceled"
    broker.orders[cid]["updated_at"] = "2026-10-21T15:00:10+00:00"
    Runner(service, None).maintain()
    assert service.reservation_summary() == 0
    assert service.store.get("snapshot")["positions"][0]["qty"] == "4"


def test_loss_cancel_timeout_remains_reserved_and_retries_next_maintenance(env, decision, monkeypatch):
    service, broker, _ = env
    service.execute(decision, "timeout-buy")
    fee(service, broker, "2000")
    calls = []

    def timeout(order_id):
        calls.append(order_id)
        raise httpx.ReadTimeout("Fixture cancel timeout")

    monkeypatch.setattr(broker, "cancel", timeout)
    for expected in (1, 2):
        Runner(service, None).maintain()
        assert len(calls) == expected
        assert service.reservation_summary() == dec("1001")
    assert len(service.store.events("cancel_error")) == 2


def test_loss_maintenance_leaves_working_protective_sell(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "owned")["client_order_id"]
    broker.fixture_fill(cid)
    fee(service, broker, "2000")
    exit_order = service.execute(
        decision.model_copy(update={"action": "SELL", "exit_reason": "risk"}), "protect"
    )
    Runner(service, None).maintain()
    assert not broker.cancels
    assert service.store.get(exit_order["client_order_id"], "order")["status"] == "new"
