"""Deterministic hypothetical purchases; no broker access or source certification."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

from .models import dec, dt

CENT = Decimal("0.01")


def purchase(cash, mark, fee, *, slippage_bps=5, fractional=False):
    """Largest affordable quantity on the selected grid, including rounded notional."""
    if type(slippage_bps) is not int or slippage_bps not in (0, 5, 10):
        raise ValueError("Require selected zero/5/10 basis-point sensitivity")
    if type(fractional) is not bool or (fractional and slippage_bps != 0):
        raise ValueError("Fractional sensitivity requires zero slippage")
    values = []
    for value in (cash, mark, fee):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("Require explicit decimal strings or integers, never unknown amounts")
        number = dec(value)
        if number < 0 or number > Decimal(1000000000000) or number.as_tuple().exponent < -12:
            raise ValueError("Amount outside supported precision/range")
        values.append(number)
    cash, mark, fee = values
    if mark <= 0 or cash != cash.quantize(CENT) or fee != fee.quantize(CENT):
        raise ValueError("Require positive mark and cent-precision cash/fee")
    with localcontext() as context:
        context.prec = 60
        step = Decimal("0.000001") if fractional else Decimal(1)
        price = mark * (1 + Decimal(slippage_bps) / 10000)
        # Rounding can make a grid point above cash/price affordable; search exact cents.
        low = 0
        high = int((max(cash - fee, Decimal(0)) + CENT) / (price * step)) + 1
        while low < high:
            middle = (low + high + 1) // 2
            cost = (middle * step * price).quantize(CENT, rounding=ROUND_HALF_UP)
            if cost + fee <= cash:
                low = middle
            else:
                high = middle - 1
        quantity = low * step
        notional = (quantity * price).quantize(CENT, rounding=ROUND_HALF_UP)
        # A positive quantity rounding to a free purchase is not an investable fill.
        if notional == 0:
            quantity = Decimal(0)
        charged_fee = fee if quantity else Decimal(0)
        cash_after = cash - notional - charged_fee
        return {
            "kind": "HYPOTHETICAL",
            "quantity": str(quantity),
            "reference_price": str(mark),
            "modeled_price": str(price),
            "notional": str(notional),
            "fee": str(charged_fee),
            "slippage_usd_unrounded": str(quantity * (price - mark)),
            "rounding_usd": str(notional - quantity * price),
            "cash_before": str(cash),
            "cash_after": str(cash_after),
            "marked_nav": str(cash_after + quantity * mark),
            "opening_return_denominator": str(cash),
            "fractional": fractional,
            "slippage_bps": slippage_bps,
        }


def reinvestment_session(payment_date, available_at, sessions):
    """First supplied regular close after payment day and evidence availability."""
    payment = date.fromisoformat(payment_date)
    if payment.isoformat() != payment_date:
        raise ValueError("Require canonical payment date")
    available = dt(available_at)
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 1000:
        raise ValueError("Require bounded retained regular-session calendar")
    previous = None
    eligible = None
    for session in sessions:
        day = date.fromisoformat(session["date"])
        close = dt(session["close"])
        if (
            day.isoformat() != session["date"]
            or close.astimezone(ZoneInfo("America/New_York")).date() != day
            or (previous is not None and day <= previous)
        ):
            raise ValueError("Require unique increasing session dates with matching NY close dates")
        previous = day
        if eligible is None and day > payment and close > available:
            eligible = session["date"]
    return eligible


def preview(document):
    """Preview one sourced hypothetical purchase, not a completed benchmark series."""
    if document.get("version") != 1 or type(document["version"]) is not int:
        raise ValueError("Require preview version 1")
    if document.get("symbol") not in {"SPY", "QQQ"} or document.get("currency") != "USD":
        raise ValueError("Require selected USD ETF comparator")
    for key in ("price_sha256", "fee_evidence_sha256"):
        value = document[key]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Require exact evidence hashes; hashes alone do not authenticate claims")
    session = date.fromisoformat(document["session"])
    if session.isoformat() != document["session"]:
        raise ValueError("Require canonical session date")
    if document["reason"] not in {"opening", "distribution_reinvestment"}:
        raise ValueError("Unknown purchase reason")
    if document["reason"] == "distribution_reinvestment":
        selected = reinvestment_session(
            document["payment_date"], document["available_at"], document["sessions"]
        )
        if selected != document["session"]:
            raise ValueError("Purchase must use first eligible reinvestment session")
        for key in ("distribution_id", "payment_evidence"):
            if not isinstance(document.get(key), str) or not document[key].strip():
                raise ValueError("Require distribution/payment evidence")
    return {
        **purchase(
            document["cash"],
            document["mark"],
            document["fee"],
            slippage_bps=document["slippage_bps"],
            fractional=document["fractional"],
        ),
        "input": document,
        "status": "diagnostic_only",
        "limitations": [
            "Supplied evidence/calendar/fee claims require independent verification.",
            "Fixed order fee only; variable schedules require a supported fee calculation.",
            "Not an entitlement ledger, complete return series, or permission to trade.",
        ],
    }
