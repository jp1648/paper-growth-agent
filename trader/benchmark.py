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


def evidence_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Require exact evidence hashes; hashes alone do not authenticate claims")


def preview(document):
    """Preview one sourced hypothetical purchase, not a completed benchmark series."""
    if document.get("version") != 1 or type(document["version"]) is not int:
        raise ValueError("Require preview version 1")
    if document.get("symbol") not in {"SPY", "QQQ"} or document.get("currency") != "USD":
        raise ValueError("Require selected USD ETF comparator")
    for key in ("price_sha256", "fee_evidence_sha256"):
        evidence_hash(document[key])
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


def build_ledger(document):
    with localcontext() as context:
        context.prec = 60
        return _build_ledger(document)


def _build_ledger(document):
    """Replay a complete supplied session/event set; retain explicit evidence limits."""

    def evidence(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Missing evidence reference")

    def amount(value, *, cents=False):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("Require explicit decimal amounts")
        number = dec(value)
        if number < 0 or number > Decimal(1000000000000) or number.as_tuple().exponent < -12:
            raise ValueError("Amount outside supported range")
        if cents and number != number.quantize(CENT):
            raise ValueError("Require cent-precision cash amounts")
        return number

    if type(document.get("version")) is not int or document["version"] != 1:
        raise ValueError("Require benchmark ledger version 1")
    if document["currency"] != "USD" or document["symbol"] not in {"SPY", "QQQ"}:
        raise ValueError("Require USD SPY/QQQ comparator")
    if document.get("coverage_complete") is not True:
        raise ValueError("Explicit complete calendar/action coverage claim required")
    for key in ("calendar_evidence", "action_coverage_evidence", "investor_terms_evidence", "fee_evidence"):
        evidence(document[key])
    rows = document["sessions"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= 1000:
        raise ValueError("Require opening and at least one observation session")
    if [r["date"] for r in rows] != document["expected_sessions"]:
        raise ValueError("Require every expected session exactly once")
    # Validate the entire ordered calendar even if no distribution is paid.
    reinvestment_session(rows[0]["date"], rows[0]["close"], rows)
    cash = amount(document["opening_capital"], cents=True)
    if cash <= 0:
        raise ValueError("Opening capital must be positive")
    capital = cash
    fee = amount(document["order_fee"], cents=True)
    quantity = Decimal(0)
    entitlements, seen, fills, balances, audit = {}, set(), [], [], []
    previous_nav = capital
    for index, row in enumerate(rows):
        day = row["date"]
        mark = amount(row["mark"])
        if mark <= 0:
            raise ValueError("Missing/nonpositive mark")
        evidence_hash(row["price_sha256"])
        evidence(row["interest_evidence"])
        interest = amount(row["interest_cash"], cents=True)
        if index == 0 and interest:
            raise ValueError("Opening interest belongs in opening capital")
        if amount(row["external_deposits"], cents=True) or amount(row["external_withdrawals"], cents=True):
            raise ValueError("External flows invalidate the no-flow ledger")
        events = row["events"]
        if not isinstance(events, list):
            raise TypeError("Require an explicit event list")
        if sum(e.get("kind") in {"split", "dividend"} for e in events) > 1:
            raise ValueError("Same-date action ordering requires separate review")
        for event in events:
            identifier = event["id"]
            evidence(identifier)
            evidence(event["evidence"])
            if identifier in seen:
                raise ValueError("Duplicate event identity")
            seen.add(identifier)
            kind = event["kind"]
            if kind == "split":
                numerator, denominator = amount(event["numerator"]), amount(event["denominator"])
                if not numerator or not denominator:
                    raise ValueError("Split ratio must be positive")
                changed = quantity * numerator / denominator
                grid = Decimal(".000001") if document["fractional"] else Decimal(1)
                if changed % grid:
                    raise ValueError("Split requires unsupported cash-in-lieu treatment")
                audit.append(
                    {"event_id": identifier, "date": day, "before": str(quantity), "after": str(changed)}
                )
                quantity = changed
            elif kind == "dividend":
                gross = (quantity * amount(event["gross_per_share"])).quantize(CENT, rounding=ROUND_HALF_UP)
                withholding = (quantity * amount(event["withholding_per_share"])).quantize(
                    CENT, rounding=ROUND_HALF_UP
                )
                charges = (quantity * amount(event["fee_per_share"])).quantize(CENT, rounding=ROUND_HALF_UP)
                if withholding + charges > gross:
                    raise ValueError("Dividend deductions exceed entitlement")
                entitlements[identifier] = {
                    "ex_date": day,
                    "quantity": str(quantity),
                    "gross": str(gross),
                    "withholding": str(withholding),
                    "fee": str(charges),
                    "net": str(gross - withholding - charges),
                    "paid": False,
                    "reinvest_on": None,
                    "evidence": event["evidence"],
                }
            elif kind == "payment":
                entitlement = entitlements[event["dividend_id"]]
                if entitlement["paid"] or day <= entitlement["ex_date"]:
                    raise ValueError("Duplicate or ambiguous payment boundary")
                if event["payment_date"] != day:
                    raise ValueError("Place payment on its economic payment-date session")
                if dt(event["available_at"]).astimezone(
                    ZoneInfo("America/New_York")
                ).date() < date.fromisoformat(day):
                    raise ValueError("Completed-payment confirmation cannot predate payment")
                paid = amount(event["net_cash"], cents=True)
                if paid != dec(entitlement["net"]):
                    raise ValueError("Partial/reversed or inconsistent payment requires separate review")
                cash += paid
                entitlement.update(
                    paid=True,
                    payment_id=identifier,
                    payment_evidence=event["evidence"],
                    available_at=event["available_at"],
                    reinvest_on=reinvestment_session(day, event["available_at"], rows),
                )
            else:
                raise ValueError("Unsupported corporate action")
        cash += interest
        due = [key for key, claim in entitlements.items() if claim["reinvest_on"] == day]
        if index == 0 or due:
            locked = sum(
                (
                    dec(e["net"])
                    for e in entitlements.values()
                    if e["paid"] and (e["reinvest_on"] is None or e["reinvest_on"] > day)
                ),
                Decimal(0),
            )
            result = preview(
                {
                    "version": 1,
                    "symbol": document["symbol"],
                    "currency": "USD",
                    "cash": str(cash - locked),
                    "mark": str(mark),
                    "fee": str(fee),
                    "slippage_bps": document["slippage_bps"],
                    "fractional": document["fractional"],
                    "session": day,
                    "price_sha256": row["price_sha256"],
                    "fee_evidence_sha256": document["fee_evidence_sha256"],
                    "reason": "opening",
                }
            )
            # Purchase helper's cash/lot NAV is not the total benchmark NAV.
            quantity += dec(result["quantity"])
            cash = dec(result["cash_after"]) + locked
            fills.append(
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"input", "limitations", "marked_nav", "opening_return_denominator"}
                }
                | {
                    "date": day,
                    "reason": "opening" if index == 0 else "distribution_reinvestment",
                    "distribution_ids": due,
                    "price_sha256": row["price_sha256"],
                }
            )
        receivable = sum((dec(e["net"]) for e in entitlements.values() if not e["paid"]), Decimal(0))
        nav = cash + quantity * mark + receivable
        balances.append(
            {
                "date": day,
                "cash": str(cash),
                "shares": str(quantity),
                "net_receivable": str(receivable),
                "nav": str(nav),
                "return": None if index == 0 else str(nav / previous_nav - 1),
            }
        )
        # Keep pre-purchase capital for the first observation's denominator.
        if index:
            previous_nav = nav
    return {
        "kind": "HYPOTHETICAL",
        "status": "diagnostic_only",
        "symbol": document["symbol"],
        "opening_capital": str(capital),
        "balances": balances,
        "purchases": fills,
        "entitlements": entitlements,
        "split_audit": audit,
        "unpaid_entitlements": [
            key for key, e in entitlements.items() if not e["paid"] and dec(e["net"]) > 0
        ],
        "limitations": [
            "Evidence and coverage are supplied claims, not independently certified.",
            "Restated economic-date ledger; late payment knowledge cannot generate retroactive purchases.",
            "Non-session payments, partial/reversed payments, cash in lieu and ambiguous actions require review.",
            "Not trial activation or completed after-cost portfolio comparison.",
        ],
    }


def build_file(source, output):
    import hashlib
    import json
    from pathlib import Path

    from .models import dumps

    raw = Path(source).read_bytes()
    result = build_ledger(json.loads(raw))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    with Path(output).open("x") as stream:
        stream.write(dumps(result) + "\n")
    return result
