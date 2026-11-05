from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .models import ZERO, dec, dt


class LedgerError(RuntimeError):
    pass


def replay(initial, activities, adjustments):
    """Audit confirmed executions only. Broker snapshots remain authoritative holdings."""
    cash, realized, flows, fees, dividends = dec(initial), ZERO, ZERO, ZERO, ZERO
    shares, basis = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    fills, holdings = [], []
    started = {}

    def activity_time(a):
        return (
            dt(a["transaction_time"])
            if a.get("transaction_time")
            else datetime.fromisoformat(a["date"]).replace(tzinfo=UTC)
        )

    for a in sorted(activities, key=lambda a: (activity_time(a), a["id"])):
        kind = a["activity_type"]
        if kind == "FILL":
            symbol, qty, price = a["symbol"], dec(a["qty"]), dec(a["price"])
            if qty <= 0 or price <= 0 or a["side"] not in {"buy", "sell"}:
                raise LedgerError("Invalid fill activity")
            if a["side"] == "buy":
                if shares[symbol] == 0:
                    started[symbol] = a["transaction_time"]
                cash -= qty * price
                basis[symbol] += qty * price
                shares[symbol] += qty
            else:
                if qty > shares[symbol]:
                    raise LedgerError(f"Sell exceeds audited shares: {symbol}")
                cost = basis[symbol] / shares[symbol] * qty
                realized += qty * price - cost
                basis[symbol] -= cost
                shares[symbol] -= qty
                cash += qty * price
                holdings.append(
                    {
                        "symbol": symbol,
                        "quantity": qty,
                        "entry_at": started.get(symbol),
                        "exit_at": a["transaction_time"],
                        "order_id": a["order_id"],
                    }
                )
            fills.append(a)
        elif kind in {"CSD", "CSW"}:
            amount = dec(a["net_amount"])
            if a["id"] not in adjustments or dec(adjustments[a["id"]]["cash"]) != amount:
                raise LedgerError(f"Unacknowledged capital movement {a['id']}")
            if (kind == "CSD" and amount <= 0) or (kind == "CSW" and amount >= 0):
                raise LedgerError("Capital movement sign mismatch")
            cash += amount
            flows += amount
        elif kind in {"FEE", "CFEE", "DIVFEE", "REGFEE", "PTC"}:
            amount = dec(a["net_amount"])
            cash += amount
            fees -= amount
        elif kind in {"DIV", "DIVCGL", "DIVCGS", "DIVNRA"}:
            amount = dec(a["net_amount"])
            cash += amount
            dividends += amount
        elif a["id"] in adjustments:
            adjustment = adjustments[a["id"]]
            cash += dec(adjustment["cash"])
            for symbol, change in adjustment.get("positions", {}).items():
                shares[symbol] += dec(change["quantity_delta"])
                basis[symbol] += dec(change["basis_delta"])
                if shares[symbol] < 0 or basis[symbol] < 0:
                    raise LedgerError("Invalid corporate-action adjustment")
        else:
            raise LedgerError(
                f"Unsupported corporate activity {kind}: {a['id']}; explicit audited adjustment required"
            )
    return {
        "cash": cash,
        "realized_average_cost": realized,
        "capital_flows": flows,
        "fees": fees,
        "dividends": dividends,
        "shares": dict(shares),
        "basis": dict(basis),
        "fills": fills,
        "holding_periods": holdings,
        "opened_at": started,
    }


def remaining(order):
    return max(ZERO, dec(order["qty"]) - dec(order["filled_qty"]))


def reserved(orders, fee_buffer):
    return sum(
        (
            remaining(o) * dec(o["limit_price"]) + fee_buffer
            for o in orders
            if o["side"] == "buy" and remaining(o) > 0
        ),
        ZERO,
    )


def recent_turnover(activities, orders, at):
    boundary = at - timedelta(days=30)
    filled = sum(
        (
            dec(a["qty"]) * dec(a["price"])
            for a in activities
            if a["activity_type"] == "FILL" and dt(a["transaction_time"]) >= boundary
        ),
        ZERO,
    )
    return filled + sum((remaining(o) * dec(o["limit_price"]) for o in orders), ZERO)
