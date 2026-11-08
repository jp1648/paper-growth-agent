"""Offline economic-NAV arithmetic over retained claims, not source certification."""

import hashlib
import json
from datetime import date
from pathlib import Path

from .models import ZERO, dec


def build_observations(document):
    if type(document.get("version")) is not int or document["version"] != 1:
        raise ValueError("Require observation input version 1")
    if document.get("currency") != "USD" or document.get("benchmark") != "SPY":
        raise ValueError("Require primary SPY comparison in USD")
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
    return {
        "version": 1,
        "status": "diagnostic_only",
        "return_basis": "flow_adjusted_net_total_return",
        "currency": "USD",
        "benchmark": "SPY",
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
            "No trial activation, acceptance or trading authorization follows from this calculation.",
        ],
    }


def build_file(source, output):
    raw = Path(source).read_bytes()
    result = build_observations(json.loads(raw))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    with Path(output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
