"""A narrow, source-bound annual cash-flow proxy; not an assertion of fair value."""

import re
from datetime import date

from .models import dec, dt


def annual_cash_flow(source, period_end, cutoff):
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
    for tag, unit in (
        ("NetCashProvidedByUsedInOperatingActivities", "USD"),
        ("PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
        ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
    ):
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
            raise ValueError(f"Missing matching annual {tag} in {unit}; do not infer units or annualize YTD")
        latest = max(r["filed"] for r in eligible)
        latest_records = [r for r in eligible if r["filed"] == latest]
        if len({(r["start"], str(dec(r["val"]))) for r in latest_records}) != 1:
            raise ValueError("Conflicting financial facts require review")
        selected = max(latest_records, key=lambda r: r["accn"])
        selections[tag] = {**selected, "unit": unit}
    if len({r["start"] for r in selections.values()}) != 1:
        raise ValueError("Financial inputs have mismatched annual periods")
    if len({(r["accn"], r["filed"], r["form"]) for r in selections.values()}) != 1:
        raise ValueError("Financial inputs have mixed filing basis; review amendments before valuation")
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
