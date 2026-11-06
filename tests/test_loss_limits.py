"""Cumulative loss controls use audited flows and survive restart without liquidation."""

import pytest

from trader.health import check_health
from trader.models import dec
from trader.reports import portfolio_report
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
    with pytest.raises(ValueError, match="Cumulative loss limit"):
        check_health(service.cfg)
    assert any("Cumulative loss limit" in item for item in portfolio_report(service)["risk_reviews"])
    assert len(broker.orders) == 1
