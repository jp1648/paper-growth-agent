"""Offline hypothetical cash convention; never changes broker records or orders."""

from copy import deepcopy
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from .models import dec, dt

CENT = Decimal("0.01")
RATES = ("0", "0.02", "0.05")
AMENDMENT_SHA256 = "2e1f261c1364dcd9555f20b50d6eb3e781a529241cd37b86937aaf44736d19d0"


def cash_amount(value, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError("Require explicit decimal cash amounts")
    number = dec(value)
    if abs(number) > 10**12 or number.as_tuple().exponent < -2 or (not signed and number < 0):
        raise ValueError("Require bounded cent-precision cash")
    return number


def interval_interest(cash, annual_rate, start, end):
    if annual_rate not in RATES:
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
            if event["kind"] not in (
                "trade",
                "fee",
                "dividend",
                "interest",
                "interest_withholding",
                "lock_change",
            ):
                raise ValueError("Unknown cash event or external flow makes scenario noncomparable")
            delta = cash_amount(event["delta_cash"], signed=True)
            if event["kind"] == "lock_change" and delta:
                raise ValueError("Lock changes cannot change cash")
            actual += delta
            if actual != cash_amount(event["cash_after"]):
                raise ValueError("Observed cash event does not reconcile")
            if event["kind"] in ("interest", "interest_withholding"):
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


def compare_scenarios(observations, cash_input, marked_inventory, benchmarks, amendment_sha256):
    """Derive every predeclared rate from one retained portfolio and two ETF ledgers."""
    with localcontext() as context:
        context.prec = 60
        return _compare_scenarios(observations, cash_input, marked_inventory, benchmarks, amendment_sha256)


def _compare_scenarios(observations, cash_input, marked_inventory, benchmarks, amendment_sha256):
    from .benchmark import build_ledger, evidence_hash
    from .paper_observations import build_observations

    evidence_hash(amendment_sha256)
    if amendment_sha256 != AMENDMENT_SHA256:
        raise ValueError("Cash comparison requires the exact reviewed cash amendment")
    if cash_input["series"] != "agent" or cash_input["amendment_sha256"] != amendment_sha256:
        raise ValueError("Cash input must bind the agent and approved amendment")
    if set(benchmarks) != {"SPY", "QQQ"}:
        raise ValueError("Require both primary SPY and descriptive QQQ")
    build_observations(observations)
    points = [observations["opening"], *observations["observations"]]
    dates = [point["date"] for point in points]
    cash_rows = cash_input["sessions"]
    inventory = marked_inventory["observations"]
    if dates != [r["date"] for r in cash_rows] or dates != [r["date"] for r in inventory]:
        raise ValueError("Cash, inventory and returns must share every opening/observation boundary")
    for point, cash, mark in zip(points, cash_rows, inventory, strict=True):
        if dec(point["portfolio"]["reported_equity"]) != dec(mark["portfolio"]["marked_equity"]):
            raise ValueError("Portfolio equity differs from independently marked inventory")
        if dec(cash["cash"]) != dec(mark["portfolio"]["cash"]):
            raise ValueError("Cash replay differs from marked inventory")
        accrual = point["portfolio"]["cash_interest_receivable_not_in_equity"]
        evidence_hash(accrual["evidence_sha256"])
        if accrual.get("status") != "final":
            raise ValueError("Unknown actual cash-interest receivables cannot finalize modeled returns")
        interest_receivable = cash_amount(accrual["amount"])
        if interest_receivable > dec(point["portfolio"]["net_receivables_not_in_equity"]):
            raise ValueError("Interest receivable exceeds total net receivables")
        liability = point["portfolio"]["cash_interest_liability_not_in_equity"]
        evidence_hash(liability["evidence_sha256"])
        if liability.get("status") != "final":
            raise ValueError("Unknown interest-linked liabilities cannot finalize modeled returns")
        interest_liability = cash_amount(liability["amount"])
        total_liability = (
            dec(point["portfolio"]["accrued_liabilities_not_in_equity"]["amount"])
            if observations["version"] == 2
            else Decimal(0)
        )
        if interest_liability > total_liability:
            raise ValueError("Interest liability exceeds declared liabilities; nonzero requires version 2")
    if dec(points[0]["portfolio"]["cash_interest_receivable_not_in_equity"]["amount"]):
        raise ValueError("Opening cash-interest claims require a separately reviewed carry-in treatment")
    if dec(points[0]["portfolio"]["cash_interest_liability_not_in_equity"]["amount"]):
        raise ValueError("Opening interest liabilities require a separately reviewed carry-in treatment")
    capital = dec(points[0]["portfolio"]["reported_equity"]) + dec(
        points[0]["portfolio"]["net_receivables_not_in_equity"]
    )
    if observations["version"] == 2:
        capital -= dec(points[0]["portfolio"]["accrued_liabilities_not_in_equity"]["amount"])
    cash_amount(str(capital))
    baseline = {}
    for symbol, source in benchmarks.items():
        if source["symbol"] != symbol or dec(source["opening_capital"]) != capital:
            raise ValueError("Comparators must share opening economic capital")
        if source.get("cash_model") is not None:
            raise ValueError("Require original unmodeled benchmark inputs")
        if source["calendar_sha256"] != cash_input["calendar_sha256"]:
            raise ValueError("Cash and ETF calendars differ")
        if [(r["date"], dt(r["close"])) for r in source["sessions"]] != [
            (r["date"], dt(r["close"])) for r in cash_rows
        ]:
            raise ValueError("Cash and ETF close boundaries differ")
        baseline[symbol] = build_ledger(source)
    scenarios = {}
    for rate in RATES:
        agent = replay(cash_input | {"annual_rate": rate})
        cash_only = deepcopy(cash_input)
        cash_only.update(series="all_cash", annual_rate=rate)
        for row in cash_only["sessions"]:
            row.update(cash=str(capital), locked_cash="0", events=[])
        all_cash = replay(cash_only)
        all_cash["basis"] = "Synthetic cash-only comparator derived from common opening economic capital"
        compared = {}
        for symbol in ("SPY", "QQQ", "CASH"):
            ledger = None
            if symbol != "CASH":
                source = deepcopy(benchmarks[symbol])
                source["cash_model"] = {"annual_rate": rate, "amendment_sha256": amendment_sha256}
                for row in source["sessions"]:
                    row["interest_cash"] = "0"
                ledger = build_ledger(source)
                flexible_payments = {
                    event["id"]
                    for row in source["sessions"]
                    for event in row["events"]
                    if event["kind"] == "payment" and event.get("settle_remaining") is True
                }
                for key, claim in ledger["entitlements"].items():
                    if claim["net"] != baseline[symbol]["entitlements"][key]["net"] and any(
                        p["id"] not in flexible_payments or p["reversals"] for p in claim["payments"]
                    ):
                        raise ValueError("Changed entitlement requires scenario-specific payment evidence")
            derived = deepcopy(observations)
            derived.update(benchmark=symbol, benchmark_method=f"Hypothetical cash rate {rate}; {symbol}")
            for index, point in enumerate([derived["opening"], *derived["observations"]]):
                point["portfolio"]["reported_equity"] = str(
                    dec(point["portfolio"]["reported_equity"]) + dec(agent["balances"][index]["adjustment"])
                )
                point["portfolio"]["net_receivables_not_in_equity"] = str(
                    dec(point["portfolio"]["net_receivables_not_in_equity"])
                    - dec(point["portfolio"]["cash_interest_receivable_not_in_equity"]["amount"])
                )
                if derived["version"] == 2:
                    liability = point["portfolio"]["accrued_liabilities_not_in_equity"]
                    liability["amount"] = str(
                        dec(liability["amount"])
                        - dec(point["portfolio"]["cash_interest_liability_not_in_equity"]["amount"])
                    )
                balance = ledger["balances"][index] if ledger else None
                receivable = dec(balance["net_receivable"]) if balance and index else Decimal(0)
                equity = (
                    (dec(balance["nav"]) - receivable if index else capital)
                    if balance
                    else dec(all_cash["balances"][index]["modeled_cash"])
                )
                point["benchmark"] = {
                    "reported_equity": str(equity),
                    "net_receivables_not_in_equity": str(receivable),
                    "complete": True,
                    "evidence": f"derived:{amendment_sha256}:{rate}:{symbol}",
                    "cumulative_external_costs": {
                        category: {
                            "amount": "0",
                            "status": "final",
                            "evidence": "modeled-ledger-costs-in-NAV",
                        }
                        for category in ("model", "data", "infrastructure", "other")
                    },
                }
                if derived["version"] == 2:
                    point["benchmark"]["accrued_liabilities_not_in_equity"] = {
                        "amount": "0",
                        "status": "final",
                        "evidence": "modeled-ledger-liabilities-in-NAV",
                    }
            compared[symbol] = {"returns": build_observations(derived), "ledger": ledger}
        scenarios[rate] = {"agent_cash": agent, "all_cash": all_cash, "comparisons": compared}
    excess = [
        dec(scenarios[rate]["comparisons"]["SPY"]["returns"]["performance"]["net_excess_return"])
        for rate in RATES
    ]
    return {
        "status": "diagnostic_only",
        "amendment_sha256": amendment_sha256,
        "primary_rate": "0",
        "scenarios": scenarios,
        "primary_excess_sign_changes": len({v.compare(0) for v in excess}) > 1,
        "activation_authorized": False,
    }
