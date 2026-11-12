"""Independent expected balances across distributions, capital flows and share changes."""

import pytest

from trader.accounting import LedgerError, replay
from trader.models import dec


def fill(identifier, side, quantity, price, day):
    return {
        "id": identifier,
        "activity_type": "FILL",
        "type": "fill",
        "symbol": "ACME",
        "qty": str(quantity),
        "price": str(price),
        "side": side,
        "order_id": identifier,
        "transaction_time": f"2026-10-{day:02d}T15:00:00Z",
    }


def test_accounting_corporate_action_and_distribution_lifecycle():
    records = [
        fill("entry", "buy", 10, 100, 1),
        {"id": "dividend", "activity_type": "DIV", "net_amount": "10", "date": "2026-10-02"},
        {"id": "withholding", "activity_type": "DIVNRA", "net_amount": "-3", "date": "2026-10-02"},
        {"id": "fee", "activity_type": "REGFEE", "net_amount": "-0.5", "date": "2026-10-02"},
        {"id": "split", "activity_type": "SPLIT", "date": "2026-10-03"},
        fill("reduce", "sell", 5, 60, 4),
        {"id": "deposit", "activity_type": "CSD", "net_amount": "100", "date": "2026-10-05"},
        {"id": "withdraw", "activity_type": "CSW", "net_amount": "-40", "date": "2026-10-05"},
        {"id": "reverse", "activity_type": "SPLIT", "date": "2026-10-06"},
        fill("exit", "sell", 5, 180, 7),
    ]
    adjustments = {
        "split": {"cash": "0", "positions": {"ACME": {"quantity_delta": "10", "basis_delta": "0"}}},
        "reverse": {"cash": "0", "positions": {"ACME": {"quantity_delta": "-10", "basis_delta": "0"}}},
        "deposit": {"cash": "100"},
        "withdraw": {"cash": "-40"},
    }
    result = replay("100000", list(reversed(records)), adjustments)
    # 10 original shares cost 1000. Split doubles shares without changing basis.
    # First sale realizes 300-250=50. Reverse split leaves 5 shares costing 750.
    # Final sale realizes 900-750=150. Distributions net 7, fees .5, net deposits 60.
    assert result["cash"] == dec("100266.5")
    assert result["realized_average_cost"] == dec("200")
    assert result["capital_flows"] == dec("60")
    assert result["dividends"] == dec("7")
    assert result["fees"] == dec(".5")
    assert result["shares"] == {"ACME": 0}
    assert result["basis"] == {"ACME": 0}
    assert result["cash"] - dec("100000") - result["capital_flows"] == dec("206.5")


@pytest.mark.parametrize("kind", ["MERGER", "SPINOFF", "DELISTING", "RETURN_OF_CAPITAL"])
def test_unknown_corporate_action_cannot_silently_change_balances(kind):
    with pytest.raises(LedgerError, match="explicit audited adjustment"):
        replay("100000", [{"id": "unknown", "activity_type": kind, "date": "2026-10-02"}], {})


def test_audited_return_of_capital_reduces_basis_not_trading_profit():
    records = [
        fill("buy", "buy", 10, 100, 1),
        {"id": "roc", "activity_type": "RETURN_OF_CAPITAL", "date": "2026-10-02"},
    ]
    result = replay(
        "100000",
        records,
        {"roc": {"cash": "20", "positions": {"ACME": {"quantity_delta": "0", "basis_delta": "-20"}}}},
    )
    assert result["cash"] == dec("99020")
    assert result["basis"]["ACME"] == dec("980")
    assert result["realized_average_cost"] == 0
    assert result["dividends"] == 0


def test_adjustment_approval_is_immutable_and_retry_is_idempotent(env):
    service, broker, _ = env
    broker.activities.append(
        {"id": "deposit", "activity_type": "CSD", "date": "2026-10-21", "net_amount": "1000"}
    )
    broker.raw_account.update(cash="101000", equity="101000")
    with pytest.raises(LedgerError):
        service.reconcile()
    adjustment = {"id": "deposit", "cash": "1000", "reason": "Verified contribution"}
    with pytest.raises(ValueError, match="Pause trading"):
        service.acknowledge_adjustment(adjustment)
    assert service.store.get("deposit", "adjustment") is None
    service.kill_path.write_text("Review adjustment")
    service.acknowledge_adjustment(adjustment)
    service.acknowledge_adjustment(adjustment)
    for altered in ({**adjustment, "cash": "2000"}, {**adjustment, "reason": "Rewritten reason"}):
        with pytest.raises(ValueError, match="immutable"):
            service.acknowledge_adjustment(altered)
    assert service.store.get("deposit", "adjustment") == adjustment
    assert len(service.store.events("explicit_adjustment")) == 1
    assert service.kill_path.exists()
    assert broker.submits == 0


@pytest.mark.parametrize(
    "field,value",
    [("cash", "NaN"), ("cash", "Infinity"), ("quantity_delta", "NaN"), ("basis_delta", "Infinity")],
)
def test_nonfinite_adjustments_are_not_persisted(env, field, value):
    service, broker, _ = env
    broker.activities.append({"id": "unknown", "activity_type": "SPLIT", "date": "2026-10-21"})
    with pytest.raises(LedgerError):
        service.reconcile()
    service.kill_path.write_text("Review adjustment")
    adjustment = {
        "id": "unknown",
        "cash": "0",
        "reason": "Invalid fixture",
        "positions": {"ACME": {"quantity_delta": "0", "basis_delta": "0"}},
    }
    if field == "cash":
        adjustment[field] = value
    else:
        adjustment["positions"]["ACME"][field] = value
    with pytest.raises(ValueError, match="Non-finite"):
        service.acknowledge_adjustment(adjustment)
    assert service.store.get("unknown", "adjustment") is None
    assert not service.store.events("explicit_adjustment")
    assert service.kill_path.exists()


def test_adjustment_batch_preflights_before_any_durable_approval(env):
    service, broker, _ = env
    broker.activities.extend(
        [
            {"id": "deposit-a", "activity_type": "CSD", "date": "2026-10-21", "net_amount": "1000"},
            {"id": "deposit-b", "activity_type": "CSD", "date": "2026-10-21", "net_amount": "2000"},
        ]
    )
    broker.raw_account.update(cash="103000", equity="103000")
    with pytest.raises(LedgerError):
        service.reconcile()
    service.kill_path.write_text("Verify both contributions")
    batch = [
        {"id": "deposit-a", "cash": "1000", "reason": "Verified A"},
        {"id": "deposit-b", "cash": "2000", "reason": "Verified B"},
    ]
    with pytest.raises(LedgerError):
        service.acknowledge_adjustment(batch[0])
    assert service.store.items("adjustment") == []
    service.acknowledge_adjustment(batch)
    service.acknowledge_adjustment(batch)
    assert len(service.store.events("explicit_adjustment")) == 2
    assert service.store.get("audit")["capital_flows"] == "3000"
    assert service.kill_path.exists()


@pytest.mark.parametrize("fault", ["cash", "position", "account", "activity", "missing"])
def test_adjustment_rejects_broker_mismatch_without_persisting(env, fault):
    service, broker, _ = env
    broker.activities.append({"id": "journal", "activity_type": "JNLC", "date": "2026-10-21"})
    with pytest.raises(LedgerError):
        service.reconcile()
    service.kill_path.write_text("Verify journal")
    adjustment = {"id": "journal", "cash": "0", "reason": "Reviewed fixture"}
    if fault == "cash":
        adjustment["cash"] = "50"
    elif fault == "position":
        adjustment["positions"] = {"ACME": {"quantity_delta": "1", "basis_delta": "100"}}
    elif fault == "account":
        broker.raw_account["id"] = "different-account"
    elif fault == "missing":
        broker.activities = []
    else:
        broker.activities[0]["net_amount"] = "50"
    with pytest.raises(LedgerError):
        service.acknowledge_adjustment(adjustment)
    assert service.store.items("adjustment") == []
    assert not service.store.events("explicit_adjustment")
    assert service.kill_path.exists()


def test_corporate_action_cannot_leave_orphaned_cost_basis():
    records = [
        fill("buy", "buy", 10, 100, 1),
        {"id": "remove", "activity_type": "DELISTING", "date": "2026-10-02"},
    ]
    with pytest.raises(LedgerError, match="cost basis without shares"):
        replay(
            "100000",
            records,
            {"remove": {"cash": "0", "positions": {"ACME": {"quantity_delta": "-10", "basis_delta": "0"}}}},
        )


def test_same_cash_distribution_reclassification_requires_review_and_preserves_history(env):
    service, broker, _ = env
    original = {
        "id": "distribution-correction",
        "activity_type": "DIVNRA",
        "net_amount": "-3",
        "date": "2026-10-21",
    }
    broker.activities = [original.copy()]
    broker.raw_account.update(cash="99997", equity="99997")
    service.reconcile()
    assert service.store.get("audit")["dividends"] == "-3"
    assert service.store.get("audit")["fees"] == "0"

    corrected = {**original, "activity_type": "DIVFEE"}
    broker.activities = [corrected.copy()]
    with pytest.raises(RuntimeError, match="audited correction required"):
        service.reconcile()
    assert service.store.get("audit")["dividends"] == "-3"
    assert not service.store.items("broker_revision")

    service.kill_path.write_text("Review exact broker distribution classification")
    revision = {
        "before": original,
        "after": corrected,
        "reason": "Fixture broker reclassified withholding as a distribution fee",
    }
    service.acknowledge_revision(revision)
    service.acknowledge_revision(revision)
    service.reconcile()
    audit = service.store.get("audit")
    assert dec(audit["cash"]) == dec("99997")
    assert dec(audit["dividends"]) == 0
    assert dec(audit["fees"]) == 3
    assert dec(audit["capital_flows"]) == 0
    assert dec(audit["realized_average_cost"]) == 0
    assert service.store.get(original["id"], "activity") == original
    assert service.store.activities() == [corrected]
    assert len(service.store.items("broker_revision")) == 1
    assert len(service.store.events("broker_revision_approved")) == 1
    assert service.kill_path.exists()
    assert broker.submits == 0
