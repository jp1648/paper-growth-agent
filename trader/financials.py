"""A narrow, source-bound annual cash-flow proxy; not an assertion of fair value."""

import re
from datetime import date

from .models import dec, dt


class MissingAnnualFact(ValueError):
    """An expected evidence gap, not permission to infer a financial input."""


def annual_facts(source, period_end, cutoff, fields, *, optional=False):
    content = source.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("facts"), dict):
        raise TypeError("Valuation requires structured original financial facts")
    end = date.fromisoformat(period_end)
    if end >= cutoff.date():
        raise ValueError("Financial period is not available at the research cutoff")
    for key in ("available_at", "published_at"):
        if source.get(key) and dt(source[key]) > cutoff:
            raise ValueError("Financial source is future-dated")
    selections = {}
    for tag, unit in fields:
        records = content["facts"].get(tag, {}).get(unit, [])
        eligible = []
        for record in records:
            if record.get("end") != period_end or not record.get("start") or not record.get("filed"):
                continue
            # A filing date alone does not prove availability earlier on that day.
            if not end <= date.fromisoformat(record["filed"]) < cutoff.date():
                continue
            duration = (end - date.fromisoformat(record["start"])).days
            if 350 <= duration <= 380 and record.get("form") in {"10-K", "10-K/A", "20-F", "20-F/A"}:
                if not isinstance(record.get("accn"), str) or not re.fullmatch(
                    r"[0-9]{10}-[0-9]{2}-[0-9]{6}", record["accn"]
                ):
                    raise ValueError("Financial fact lacks original accession")
                eligible.append(record)
        if not eligible:
            if optional:
                continue
            raise MissingAnnualFact(
                f"Missing matching annual {tag} in {unit}; do not infer units or annualize YTD"
            )
        latest = max(r["filed"] for r in eligible)
        latest_records = [r for r in eligible if r["filed"] == latest]
        if len({(r["start"], str(dec(r["val"]))) for r in latest_records}) != 1:
            raise ValueError("Conflicting financial facts require review")
        selected = max(latest_records, key=lambda r: r["accn"])
        selections[tag] = {**selected, "unit": unit}
    if len({r["start"] for r in selections.values()}) > 1:
        raise ValueError("Financial inputs have mismatched annual periods")
    if len({(r["accn"], r["filed"], r["form"]) for r in selections.values()}) > 1:
        raise ValueError("Financial inputs have mixed filing basis; review amendments before valuation")
    return selections


def annual_cash_flow(source, period_end, cutoff):
    selections = annual_facts(
        source,
        period_end,
        cutoff,
        (
            ("NetCashProvidedByUsedInOperatingActivities", "USD"),
            ("PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
        ),
    )
    cash, capex, shares = [dec(r["val"]) for r in selections.values()]
    if capex < 0 or shares <= 0 or cash - capex <= 0:
        raise ValueError("Positive annual cash-flow proxy unavailable; use unsupported valuation")
    value = ((cash - capex) / shares).quantize(dec("0.000001"))
    return {
        "cash_flow_per_share": str(value),
        "source_id": source["id"],
        "period_end": period_end,
        "facts": selections,
        "formula": "(operating_cash_flow - capital_expenditure) / diluted_weighted_average_shares",
        "limitations": [
            "Historical proxy, not a forecast or normalized owner earnings.",
            "Debt changes, dilution, stock compensation and nonrecurring items require independent review.",
        ],
    }


def cash_flow_diagnostics(source, period_end, cutoff):
    """Historical accounting arithmetic, never a normalized forecast or approval."""
    proxy = annual_cash_flow(source, period_end, cutoff)
    tags = (
        "StockBasedCompensation",
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsOfDividendsCommonStock",
        "IncomeTaxesPaidNet",
    )
    extra = annual_facts(source, period_end, cutoff, [(t, "USD") for t in tags], optional=True)
    baseline = next(iter(proxy["facts"].values()))
    for record in extra.values():
        if any(record[k] != baseline[k] for k in ("start", "end", "accn", "filed", "form")):
            raise ValueError("Accounting bridge has mixed filing basis")
        if dec(record["val"]) < 0:
            raise ValueError("Negative accounting diagnostic input requires separate review")
    cash = dec(proxy["facts"]["NetCashProvidedByUsedInOperatingActivities"]["val"])
    capex = dec(proxy["facts"]["PaymentsToAcquirePropertyPlantAndEquipment"]["val"])
    result = {
        "source_id": source["id"],
        "period_end": period_end,
        "facts": {**proxy["facts"], **extra},
        "missing_facts": [t for t in tags if t not in extra],
        "cfo_less_capex_usd": str(cash - capex),
        "sbc_deduction_sensitivity_usd": None,
        "distributions_above_proxy_usd": None,
        "cash_taxes_paid_net_usd": None,
        "limitations": [
            "Historical arithmetic only; no normalization or forecast approval.",
            "SBC deduction is one sensitivity; do not also charge dilution or offsetting repurchases for the same cost.",
            "Distributions above the proxy require a full funding reconciliation, not a distress conclusion.",
            "Cash taxes are already in CFO; do not subtract them again or assume they are nonrecurring.",
            "Weighted-average shares are not a valuation-date or future diluted share base.",
        ],
    }
    if tags[0] in extra:
        result["sbc_deduction_sensitivity_usd"] = str(cash - capex - dec(extra[tags[0]]["val"]))
    if tags[1] in extra and tags[2] in extra:
        result["distributions_above_proxy_usd"] = str(
            dec(extra[tags[1]]["val"]) + dec(extra[tags[2]]["val"]) - (cash - capex)
        )
    if tags[3] in extra:
        result["cash_taxes_paid_net_usd"] = str(dec(extra[tags[3]]["val"]))
    return result


def normalization_supported(valuation):
    review = valuation.get("normalization_review")
    if not isinstance(review, dict) or review.get("unresolved_material_gaps") is not False:
        return False
    if any(
        not isinstance(review.get(key), str) or not review[key].strip()
        for key in ("cash_flow_basis", "debt_and_cash", "dilution_and_sbc", "nonrecurring_items")
    ):
        return False
    refs = review.get("evidence_ids")
    return (
        isinstance(refs, list)
        and bool(refs)
        and all(isinstance(ref, str) and ref in valuation.get("evidence_ids", []) for ref in refs)
    )


def valuation_diagnostics(inputs, target_price):
    """Sensitivity of already validated scenario inputs; no normalized-value claim."""
    cash, discount = dec(inputs["cash_flow_per_share"]), dec(inputs["discount_rate"])
    target = dec(target_price)
    if target <= 0:
        raise ValueError("Target price must be positive")
    years = inputs["years"]
    rows = []
    for growth, multiple in zip(inputs["growth_rates"], inputs["terminal_multiples"], strict=True):
        g = dec(growth)
        annual = sum((cash * (1 + g) ** y / (1 + discount) ** y for y in range(1, years + 1)), dec(0))
        terminal_unit = cash * (1 + g) ** years / (1 + discount) ** years
        terminal = terminal_unit * dec(multiple)
        rows.append(
            {
                "growth_rate": growth,
                "annual_cash_flows_pv": str(annual),
                "terminal_value_fraction": str(terminal / (annual + terminal)),
                "required_terminal_multiple": str((target - annual) / terminal_unit),
                "values_by_terminal_multiple": {
                    m: str((annual + terminal_unit * dec(m)).quantize(dec(".01")))
                    for m in inputs["terminal_multiples"]
                },
            }
        )
    return {
        "target_price": str(target),
        "rows": rows,
        "required_multiple_formula": "(target price - PV of annual cash flows) / discounted final-year cash flow",
        "limitation": "Sensitivity of historical-proxy assumptions, not supported forecasts or normalized fair value. A negative required multiple means interim cash flows alone exceed the target.",
    }
