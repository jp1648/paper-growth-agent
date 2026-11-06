"""Independent expected balances across distributions, capital flows and share changes."""

import pytest

from trader.accounting import LedgerError, replay
from trader.models import dec


def fill(identifier, side, quantity, price, day):
    return {
        "id": identifier,
        "activity_type": "FILL",
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
