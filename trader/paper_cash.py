"""Offline hypothetical cash convention; never changes broker records or orders."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .models import dec, dt

CENT = Decimal("0.01")


def cash_amount(value, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("Require explicit decimal cash amounts")
    number = dec(value)
    if abs(number) > 10**12 or number.as_tuple().exponent < -2 or (not signed and number < 0):
        raise ValueError("Require bounded cent-precision cash")
    return number


def interval_interest(cash, annual_rate, start, end):
    if annual_rate not in ("0", "0.02", "0.05"):
        raise ValueError("Require predeclared hypothetical cash rate")
    principal = cash_amount(cash)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or last < first:
        raise ValueError("Require canonical increasing calendar dates")
    days = (last - first).days
    if days > 366:
        raise ValueError("Cash interval exceeds supported observation window")
    with localcontext() as context:
        context.prec = 60
        raw = principal * dec(annual_rate) * days / 365
        return {
            "principal": str(principal),
            "annual_rate": annual_rate,
            "start": start,
            "end": end,
            "calendar_days": days,
            "unrounded": str(raw),
            "credit": str(raw.quantize(CENT, rounding=ROUND_HALF_UP)),
        }


def replay(document):
    """Replay complete observed cash events, rejecting financing changed by the model."""
    from .benchmark import evidence_hash, reinvestment_session

    if type(document.get("version")) is not int or document["version"] != 1:
        raise ValueError("Require cash replay version 1")
    if document.get("series") not in ("agent", "all_cash") or document.get("currency") != "USD":
        raise ValueError("Require USD agent or all-cash series")
    if document.get("coverage_complete") is not True:
        raise ValueError("Require complete cash-event and calendar coverage claim")
    for key in ("amendment_sha256", "calendar_sha256"):
        evidence_hash(document[key])
    rows = document["sessions"]
    if not isinstance(rows, list) or not 2 <= len(rows) <= 1000:
        raise ValueError("Require opening and observation sessions")
    if [r["date"] for r in rows] != document["expected_sessions"]:
        raise ValueError("Require every expected session exactly once")
    reinvestment_session(rows[0]["date"], rows[0]["close"], rows)
    rate = document["annual_rate"]
    adjustment = Decimal(0)
    actual = cash_amount(rows[0]["cash"])
    modeled = actual
    previous_time = dt(rows[0]["close"])
    seen, balances, events_audit = set(), [], []
    for index, row in enumerate(rows):
        evidence_hash(row["evidence_sha256"])
        events = row["events"]
        if not isinstance(events, list) or len(events) > 10000 or (index == 0 and events):
            raise ValueError("Require bounded events; opening cash already includes prior bookings")
        if document["series"] == "all_cash" and events:
            raise ValueError("All-cash comparator has no observed transactions")
        for event in events:
            identifier = event["id"]
            if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
                raise ValueError("Require unique cash event identities")
            seen.add(identifier)
            evidence_hash(event["evidence_sha256"])
            at = dt(event["at"])
            if at < previous_time or at > dt(row["close"]) or at <= dt(rows[index - 1]["close"]):
                raise ValueError("Cash events must follow previous close in retained order")
            previous_time = at
            if event["kind"] not in ("trade", "fee", "dividend", "interest", "lock_change"):
                raise ValueError("Unknown cash event or external flow makes scenario noncomparable")
            delta = cash_amount(event["delta_cash"], signed=True)
            if event["kind"] == "lock_change" and delta:
                raise ValueError("Lock changes cannot change cash")
            actual += delta
            if actual != cash_amount(event["cash_after"]):
                raise ValueError("Observed cash event does not reconcile")
            if event["kind"] == "interest":
                adjustment -= delta
            locked = cash_amount(event["locked_cash_after"])
            if actual < locked or actual + adjustment < locked:
                raise ValueError("Noncomparable: observed event needs unsupported financing")
            events_audit.append(
                {"id": identifier, "modeled_cash": str(actual + adjustment), "adjustment": str(adjustment)}
            )
        if actual != cash_amount(row["cash"]):
            raise ValueError("Closing cash does not reconcile to complete events")
        locked = cash_amount(row["locked_cash"])
        if document["series"] == "all_cash" and locked:
            raise ValueError("All-cash comparator cannot inherit agent payment locks")
        if index and locked != cash_amount(rows[index - 1]["locked_cash"]) and not events:
            raise ValueError("Changed cash lock requires an observed event")
        if events and locked != cash_amount(events[-1]["locked_cash_after"]):
            raise ValueError("Closing lock does not reconcile")
        if actual < locked or actual + adjustment < locked:
            raise ValueError("Noncomparable: cash lock needs unsupported financing")
        audit = interval_interest(str(modeled), rate, rows[max(0, index - 1)]["date"], row["date"])
        adjustment += dec(audit["credit"])
        modeled = actual + adjustment
        balances.append(
            {
                "date": row["date"],
                "observed_cash": str(actual),
                "modeled_cash": str(modeled),
                "adjustment": str(adjustment),
                "locked_cash": str(locked),
                "interest": audit,
            }
        )
        previous_time = dt(row["close"])
    return {
        "kind": "HYPOTHETICAL",
        "status": "diagnostic_only",
        "input": document,
        "balances": balances,
        "events": events_audit,
        "limitations": [
            "Coverage and event classifications require independent verification.",
            "Not actual account yield, completed performance analysis or trade approval.",
        ],
    }
