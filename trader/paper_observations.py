"""Offline economic-NAV arithmetic over retained claims, not source certification."""

import hashlib
import json
from datetime import date
from decimal import localcontext
from pathlib import Path

from .models import ZERO, dec


def build_observations(document):
    with localcontext() as context:
        context.prec = 60
        return _build_observations(document)


def _build_observations(document):
    if type(document.get("version")) is not int or document["version"] not in (1, 2):
        raise ValueError("Require observation input version 1 or 2")
    if document.get("currency") != "USD" or document.get("benchmark") not in ("SPY", "QQQ", "CASH"):
        raise ValueError("Require SPY or descriptive secondary QQQ/CASH comparison in USD")
    if document.get("flow_policy") != "no_external_flows":
        raise ValueError("Intraday flows require subperiod valuations; this builder prohibits flows")
    for key in ("benchmark_method", "cost_allocation_method", "source_manifest_sha256"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ValueError(f"Missing provenance: {key}")
    digest = document["source_manifest_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid source manifest hash")
    sessions = document["expected_sessions"]
    if not isinstance(sessions, list) or not 1 <= len(sessions) <= 1000:
        raise ValueError("Require 1–1000 expected sessions")
    if sessions != sorted(set(sessions)) or any(date.fromisoformat(s).isoformat() != s for s in sessions):
        raise ValueError("Require unique increasing canonical session dates")
    opening = document["opening"]
    start = date.fromisoformat(opening["date"])
    if start.isoformat() != opening["date"] or opening["date"] >= sessions[0]:
        raise ValueError("Opening valuation must precede the first session")
    rows = document["observations"]
    if not isinstance(rows, list) or [row["date"] for row in rows] != sessions:
        raise ValueError("Require every expected session exactly once and in order")

    def amount(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("Use decimal strings or integers; unknown amounts are not zero")
        value = dec(value)
        if value < ZERO:
            raise ValueError("Negative components or corrections require separate review")
        return value

    def evidence(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Missing evidence reference")

    def nav(claim):
        evidence(claim["evidence"])
        if claim.get("complete") is not True:
            raise ValueError("Incomplete NAV, entitlement, withholding or cost evidence")
        # Receivables here exclude amounts already included in reported equity.
        gross = amount(claim["reported_equity"]) + amount(claim["net_receivables_not_in_equity"])
        if document["version"] == 2:
            # Accruals exclude amounts already booked in cash/equity or external expenses.
            liability = claim["accrued_liabilities_not_in_equity"]
            evidence(liability["evidence"])
            if liability.get("status") != "final":
                raise ValueError("Unknown accrued liabilities cannot finalize economic NAV")
            gross -= amount(liability["amount"])
        elif "accrued_liabilities_not_in_equity" in claim:
            raise ValueError("Accrued liabilities require input version 2; never silently ignore them")
        costs = claim["cumulative_external_costs"]
        if not isinstance(costs, dict) or set(costs) != {"model", "data", "infrastructure", "other"}:
            raise ValueError("Require every cost category, including explicit evidenced zeros")
        allocated = {}
        for category, cost in costs.items():
            evidence(cost["evidence"])
            if cost.get("status") != "final":
                raise ValueError("Estimated, reserved or unknown charges cannot finalize returns")
            allocated[category] = amount(cost["amount"])
        net = gross - sum(allocated.values(), ZERO)
        if gross <= ZERO or net <= ZERO:
            raise ValueError("Nonpositive economic NAV cannot form a return chain")
        return gross, net, allocated

    previous = {side: nav(opening[side]) for side in ("portfolio", "benchmark")}
    if any(sum(values[2].values(), ZERO) for values in previous.values()):
        raise ValueError("Opening cumulative observation-window costs must be zero")
    initial = previous.copy()
    observations, balances = [], []
    for row in rows:
        evidence(row["flow_evidence"])
        # Check gross directions: offsetting deposits/withdrawals are not no-flow.
        if amount(row["external_deposits"]) or amount(row["external_withdrawals"]):
            raise ValueError("External flow stops the no-flow comparison; do not count it as profit")
        result, balance = {"date": row["date"]}, {"date": row["date"]}
        for side in ("portfolio", "benchmark"):
            gross, net, costs = nav(row[side])
            prior_gross, prior_net, prior_costs = previous[side]
            if any(costs[key] < prior_costs[key] for key in costs):
                raise ValueError("Cost revision requires a new consistently restated input version")
            result[f"{side}_return"] = str(net / prior_net - 1)
            result[f"{side}_gross_return"] = str(gross / prior_gross - 1)
            balance[side] = {"gross_nav": str(gross), "net_nav": str(net)}
            previous[side] = gross, net, costs
        observations.append(result)
        balances.append(balance)
    performance = {}
    for side in ("portfolio", "benchmark"):
        metrics = {}
        for index, basis in enumerate(("gross", "net")):
            opening_nav = initial[side][index]
            peak, drawdown = opening_nav, ZERO
            for balance in balances:
                value = dec(balance[side][f"{basis}_nav"])
                peak = max(peak, value)
                drawdown = max(drawdown, 1 - value / peak)
            metrics[f"{basis}_total_return"] = str(value / opening_nav - 1)
            metrics[f"{basis}_max_drawdown"] = str(drawdown)
        performance[side] = metrics
    performance["net_excess_return"] = str(
        dec(performance["portfolio"]["net_total_return"]) - dec(performance["benchmark"]["net_total_return"])
    )
    return {
        "version": 1,
        "status": "diagnostic_only",
        "input_version": document["version"],
        "return_basis": "flow_adjusted_net_total_return",
        "currency": "USD",
        "benchmark": document["benchmark"],
        "comparison_role": "primary" if document["benchmark"] == "SPY" else "secondary_descriptive_only",
        "performance": performance,
        "drawdown_method": "Positive session-close peak-to-trough fraction including opening economic NAV; not intraday drawdown.",
        "benchmark_method": document["benchmark_method"],
        "cost_allocation_method": document["cost_allocation_method"],
        "flow_method": "No external flows; gross deposits and withdrawals checked separately",
        "source_manifest_sha256": digest,
        "expected_sessions": sessions,
        "observations": observations,
        "balances": balances,
        "limitations": [
            "Evidence references and completeness are supplied claims, not independently authenticated sources.",
            "Reported equity must already include broker fees; external costs exclude amounts already in equity.",
            "Net receivables require supported entitlement and deductions; gross unpaid dividends are insufficient.",
            "Calendar, marks, splits, benchmark holdings/reinvestment and cost allocation need independent review.",
            "Version 1 has no liability bridge; version 2 requires explicit evidenced unbooked liabilities.",
            "QQQ and CASH are descriptive only and cannot replace the preregistered primary SPY comparison.",
            "No trial activation, acceptance or trading authorization follows from this calculation.",
        ],
    }


def build_file(source, output, *, benchmark_input=None, benchmark_capture=None):
    raw = Path(source).read_bytes()
    document = json.loads(raw)
    result = build_observations(document)
    if (benchmark_input is None) != (benchmark_capture is None):
        raise ValueError("Benchmark input and retained capture must be supplied together")
    if benchmark_input is not None:
        from .benchmark import bind_prices

        benchmark_raw = Path(benchmark_input).read_bytes()
        benchmark_document = json.loads(benchmark_raw)
        prices = bind_prices(benchmark_capture, benchmark_document)
        result["benchmark_binding"] = bind_benchmark(document, benchmark_document) | {
            "input_sha256": hashlib.sha256(benchmark_raw).hexdigest(),
            "retained_prices": prices,
        }
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    with Path(output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def reconcile_marks(capture, document):
    """Reprice supplied closing inventory without replacing broker accounting."""
    from .price_capture import validate_prices

    prices = validate_prices(capture)
    if document.get("version") != 1 or document.get("currency") != "USD":
        raise ValueError("Require inventory version 1 in USD")
    if document.get("price_manifest_sha256") != prices["manifest_sha256"]:
        raise ValueError("Inventory must bind the exact retained price manifest")
    marks = {(r["date"], r["symbol"]): dec(r["bar"]["c"]) for r in prices["marks"]}
    sessions = sorted({day for day, _ in marks})
    rows = document["observations"]
    if [r["date"] for r in rows] != sessions:
        raise ValueError("Inventory must cover every captured session in order")

    def amount(value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise TypeError("Inventory amounts must be explicit decimal strings or integers")
        result = dec(value)
        if result < ZERO:
            raise ValueError("Negative cash, short inventory or corrections require separate review")
        return result

    result = []
    for row in rows:
        observation = {"date": row["date"]}
        for side in ("portfolio", "benchmark"):
            inventory = row[side]
            if inventory.get("basis") != "regular_session_close" or inventory.get("complete") is not True:
                raise ValueError("Require complete closing inventory, not a later snapshot")
            if not isinstance(inventory.get("evidence"), str) or not inventory["evidence"].strip():
                raise ValueError("Missing inventory reconciliation evidence")
            cash, equity = amount(inventory["cash"]), amount(inventory["reported_equity"])
            reported_value, marked_value, seen, positions = ZERO, ZERO, set(), []
            for position in inventory["positions"]:
                symbol = position["symbol"]
                if symbol in seen or (row["date"], symbol) not in marks:
                    raise ValueError("Duplicate position or missing same-session raw mark")
                seen.add(symbol)
                quantity = amount(position["quantity"])
                if quantity == ZERO:
                    raise ValueError("Zero positions must be omitted")
                reported = amount(position["reported_market_value"])
                marked = quantity * marks[row["date"], symbol]
                reported_value += reported
                marked_value += marked
                positions.append(
                    {
                        "symbol": symbol,
                        "quantity": str(quantity),
                        "raw_close": str(marks[row["date"], symbol]),
                        "marked_value": str(marked),
                        "reported_market_value": str(reported),
                    }
                )
            # Do not hide unexplained components in a balancing residual or tolerance.
            if cash + reported_value != equity:
                raise ValueError("Reported cash plus holdings do not exactly reconcile to reported equity")
            observation[side] = {
                "reported_equity": str(equity),
                "cash": str(cash),
                "marked_equity": str(cash + marked_value),
                "mark_adjustment": str(marked_value - reported_value),
                "positions": positions,
                "inventory_evidence": inventory["evidence"],
            }
        result.append(observation)
    return {
        "version": 1,
        "status": "diagnostic_only",
        "price_manifest_sha256": prices["manifest_sha256"],
        "observations": result,
        "limitations": [
            "Closing inventory and its completeness are supplied claims, not verified by this calculation.",
            "Raw prices require quantities after same-session splits; inventory must reconcile fills and corporate actions.",
            "Cash already includes booked fees and distributions; unpaid net entitlements and external expenses remain separate.",
            "Marked equity is a measurement overlay, not a broker ledger update or trial acceptance.",
        ],
    }


def bind_benchmark(document, benchmark_document):
    with localcontext() as context:
        context.prec = 60
        return _bind_benchmark(document, benchmark_document)


def _bind_benchmark(document, benchmark_document):
    """Recompute comparator accounting rather than trusting pasted NAV/return rows."""
    from .benchmark import build_ledger

    # shortcut: modeled cash stays diagnostic until all rate/portfolio paths are bound in the bundle.
    if benchmark_document.get("cash_model") is not None:
        raise ValueError("Modeled cash requires common portfolio/rate binding before comparison acceptance")
    ledger = build_ledger(benchmark_document)
    if document.get("benchmark") not in ("SPY", "QQQ") or ledger["symbol"] != document["benchmark"]:
        raise ValueError("Observation benchmark must match reconstructed SPY or QQQ ledger")
    points = [document["opening"], *document["observations"]]
    if [point["date"] for point in points] != [row["date"] for row in ledger["balances"]]:
        raise ValueError("Observation window must exactly match benchmark ledger")
    capital = dec(ledger["opening_capital"])
    opening = document["opening"]["portfolio"]
    portfolio_capital = dec(opening["reported_equity"]) + dec(opening["net_receivables_not_in_equity"])
    if document["version"] == 2:
        portfolio_capital -= dec(opening["accrued_liabilities_not_in_equity"]["amount"])
    if portfolio_capital != capital:
        raise ValueError("Portfolio and benchmark must start with identical economic capital")
    for index, (point, balance) in enumerate(zip(points, ledger["balances"], strict=True)):
        claim = point["benchmark"]
        # The opening denominator is pre-purchase capital; initial friction enters once on day one.
        receivable = ZERO if index == 0 else dec(balance["net_receivable"])
        equity = capital if index == 0 else dec(balance["nav"]) - receivable
        if (
            dec(claim["reported_equity"]) != equity
            or dec(claim["net_receivables_not_in_equity"]) != receivable
        ):
            raise ValueError("Benchmark equity/receivable differs from reconstructed ledger")
        if any(dec(cost["amount"]) for cost in claim["cumulative_external_costs"].values()):
            raise ValueError("Comparator costs already in ledger; external cost adjustment requires review")
        if document["version"] == 2 and dec(claim["accrued_liabilities_not_in_equity"]["amount"]):
            raise ValueError("Benchmark liabilities are not supported by reconstructed ledger")
    return {
        "symbol": ledger["symbol"],
        "opening_capital": str(capital),
        "balances_verified": len(points),
        "purchases": ledger["purchases"],
        "unpaid_entitlements": ledger["unpaid_entitlements"],
        "source_authenticity_verified": False,
    }


def compare_inputs(primary_source, secondary_source, output):
    """Recompute two comparisons only after their shared portfolio claims match."""
    raw = [Path(source).read_bytes() for source in (primary_source, secondary_source)]
    primary, secondary = [json.loads(value) for value in raw]
    if primary.get("benchmark") != "SPY" or secondary.get("benchmark") != "QQQ":
        raise ValueError("Require primary SPY and secondary QQQ inputs in that order")
    results = [build_observations(document) for document in (primary, secondary)]
    verify_shared_portfolio(primary, secondary)
    result = {
        "version": 1,
        "status": "diagnostic_only",
        "shared_portfolio_claims_verified": True,
        "comparisons": {
            symbol: report | {"input_sha256": hashlib.sha256(value).hexdigest()}
            for symbol, report, value in zip(("SPY", "QQQ"), results, raw, strict=True)
        },
        "limitations": [
            "Exact supplied portfolio, costs and flow claims match; external source authenticity is not verified.",
            "Returns are recomputed from NAV inputs; supplied derived outputs are not trusted.",
            "Separate benchmark source manifests may differ; retained price/ledger binding is still required before acceptance.",
            "Cash comparison, frozen trial identity and prospective eligibility are not verified here.",
            "QQQ is descriptive only; neither result authorizes trial activation or live trading.",
        ],
    }
    with Path(output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def verify_shared_portfolio(primary, secondary):
    """Compare original validated claims, including costs and flows."""
    for key in ("version", "currency", "flow_policy", "expected_sessions", "cost_allocation_method"):
        if primary[key] != secondary[key]:
            raise ValueError(f"Comparisons have different shared input: {key}")
    for key in ("date", "portfolio"):
        if primary["opening"][key] != secondary["opening"][key]:
            raise ValueError(f"Comparisons have different opening {key}")
    for left, right in zip(primary["observations"], secondary["observations"], strict=True):
        for key in ("date", "portfolio", "flow_evidence", "external_deposits", "external_withdrawals"):
            if left[key] != right[key]:
                raise ValueError(f"Comparisons have different {key} at {left['date']}")
