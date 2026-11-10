"""Source-bound cash-flow arithmetic and explicit assumptions; not a fair-value certification."""

import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from .models import Model, dec, dt


class CashFlowAdjustment(Model):
    category: Literal[
        "cash_taxes",
        "working_capital",
        "capital_expenditure",
        "net_borrowing",
        "financial_asset_reinvestment",
        "noncommon_equity_cash_flows",
    ]
    annual_delta_usd: str
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class CompensationPolicy(Model):
    """Cash cost of future service, separate from already outstanding award claims."""

    annual_cash_usd: str
    rationale: str = Field(min_length=1)
    legacy_award_treatment: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class CapitalExpenditureSource(Model):
    """Explicit original cash-purchase line selection; never a taxonomy alias."""

    tag: Literal["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class NormalizationBridge(Model):
    """Explicit equity cash-flow assumptions; arithmetic does not certify economics."""

    adjustments: list[CashFlowAdjustment] = Field(min_length=4, max_length=6)
    valuation_shares: str
    share_base_date: str
    share_evidence_ids: list[str] = Field(min_length=1)
    share_rationale: str = Field(min_length=1)
    financing_and_claims: str = Field(min_length=1)
    compensation_source_tag: Literal["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"] = (
        "ShareBasedCompensation"
    )
    compensation_policy: CompensationPolicy | None = None
    capital_expenditure_source: CapitalExpenditureSource | None = None


def capital_expenditure_selection(capital_expenditure_source=None, normalization_bridge=None):
    """Keep one explicit selection across a tool request and its normalization bridge."""
    selected = (
        CapitalExpenditureSource.model_validate(capital_expenditure_source).model_dump(mode="json")
        if capital_expenditure_source is not None
        else None
    )
    if normalization_bridge is not None:
        bridge = NormalizationBridge.model_validate(normalization_bridge)
        nested = bridge.capital_expenditure_source
        nested = nested.model_dump(mode="json") if nested is not None else None
        if selected is not None and selected != nested:
            raise ValueError(
                "Conflicting capital-expenditure source selection; put the same policy in the bridge"
            )
        return nested
    return selected


def normalized_cash_flow(source, period_end, cutoff, bridge, sources):
    """Reproduce an explicit bridge; selected adjustments remain reviewable assumptions."""
    bridge = NormalizationBridge.model_validate(bridge).model_dump(mode="json", exclude_none=True)
    capex_selection = bridge.get("capital_expenditure_source")
    proxy = annual_cash_flow(source, period_end, cutoff, capex_selection, sources)
    compensation_tag = bridge["compensation_source_tag"]
    if compensation_tag == "ShareBasedCompensation":
        bridge.pop("compensation_source_tag")  # Preserve existing canonical calculation payloads.
    if compensation_tag == "AllocatedShareBasedCompensationExpense" and not bridge.get("compensation_policy"):
        raise ValueError(
            "Allocated compensation expense requires an explicit future-service compensation_policy; it is not a verified cash-flow addback"
        )
    if (
        compensation_tag == "AllocatedShareBasedCompensationExpense"
        and source["content"].get("fact_taxonomies", {}).get(compensation_tag) != "us-gaap"
    ):
        raise ValueError("Allocated compensation expense requires original us-gaap taxonomy provenance")
    sbc = annual_facts(source, period_end, cutoff, [(compensation_tag, "USD")])[compensation_tag]
    baseline = next(iter(proxy["facts"].values()))
    if any(sbc[k] != baseline[k] for k in ("start", "end", "accn", "filed", "form")):
        raise ValueError("Normalization SBC and cash flows must use the same filing basis")
    categories = {"cash_taxes", "working_capital", "capital_expenditure", "net_borrowing"}
    selected = [row["category"] for row in bridge["adjustments"]]
    if (
        not categories <= set(selected)
        or len(set(selected)) != len(selected)
        or not set(selected) <= categories | {"financial_asset_reinvestment", "noncommon_equity_cash_flows"}
    ):
        raise ValueError(
            "Require each of the four core normalization categories exactly once, including zeros, and at most one of each optional financial_asset_reinvestment and noncommon_equity_cash_flows"
        )
    refs = set(bridge["share_evidence_ids"])
    if capex_selection:
        refs.update(capex_selection["evidence_ids"])
    policy = bridge.get("compensation_policy")
    if policy:
        if not policy["rationale"].strip() or not policy["legacy_award_treatment"].strip():
            raise ValueError("Compensation policy requires future-service and legacy-award explanations")
        refs.update(policy["evidence_ids"])
    for row in bridge["adjustments"]:
        if not row["rationale"].strip():
            raise ValueError("Normalization adjustments require a rationale")
        refs.update(row["evidence_ids"])
    if not refs or any(not ref.strip() or ref not in sources for ref in refs):
        raise ValueError("Normalization assumptions require retained original evidence")
    for ref in refs:
        original = sources[ref]
        for key in ("available_at", "published_at"):
            if original.get(key) and dt(original[key]) > cutoff:
                raise ValueError("Normalization cites future evidence")
        if original.get("source") == "SEC EDGAR filing passage" and not original.get("content", "").strip():
            raise ValueError("Normalization cites an empty filing passage")
    share_date = date.fromisoformat(bridge["share_base_date"])
    if (
        share_date.isoformat() != bridge["share_base_date"]
        or not date.fromisoformat(period_end) <= share_date < cutoff.date()
        or not bridge["share_rationale"].strip()
        or not bridge["financing_and_claims"].strip()
    ):
        raise ValueError("Require dated share-base evidence and explicit financing/claim treatment")
    shares, compensation = dec(bridge["valuation_shares"]), dec(sbc["val"])
    if shares <= 0 or compensation < 0:
        raise ValueError("Require positive shares and nonnegative source SBC")
    future_compensation = dec(policy["annual_cash_usd"]) if policy else compensation
    if future_compensation < 0:
        raise ValueError("Future compensation cash cost must be nonnegative")
    cash = dec(proxy["facts"]["NetCashProvidedByUsedInOperatingActivities"]["val"])
    capex_tag = capex_selection["tag"] if capex_selection else "PaymentsToAcquirePropertyPlantAndEquipment"
    capex = dec(proxy["facts"][capex_tag]["val"])
    adjustment = sum((dec(row["annual_delta_usd"]) for row in bridge["adjustments"]), dec(0))
    normalized = cash - capex - future_compensation + adjustment
    if normalized <= 0:
        raise ValueError("Positive bridged equity cash flow unavailable; do not fabricate a valuation")
    result = {
        "bridge_version": 2 if policy else 1,
        "historical_provenance": proxy,
        "sbc_fact": sbc,
        "historical_cfo_less_capex_usd": str(cash - capex),
        "sbc_expense_equivalent_usd": str(compensation),
        "adjustments_total_usd": str(adjustment),
        "bridged_equity_cash_flow_usd": str(normalized),
        "cash_flow_per_share": str((normalized / shares).quantize(dec("0.000001"))),
        "assumptions": bridge,
        "evidence_ids": sorted(refs | {source["id"]}),
        "limitations": [
            "Source SBC is deducted once as cash-equivalent compensation; no additional dilution-offset repurchases.",
            "Constant valuation share base assumes cash-equivalent future compensation, not funded future buybacks.",
            "Tax/working-capital/capex deltas replace effects already in CFO-capex; borrowing adds a net financing flow.",
            "Equity basis retains historical interest in CFO; no enterprise-value debt subtraction or automatic net-cash addition.",
            "Adjustment amounts, share base, existing award claims and forecasts require independent source/economic review.",
        ],
    }
    if policy:
        result["future_compensation_cash_usd"] = str(future_compensation)
        result["limitations"][0] = (
            "Explicit future-service cash policy replaces, rather than adds to, historical SBC; "
            "legacy award claims and tax effects require independent review."
        )
    if "financial_asset_reinvestment" in selected:
        result["bridge_version"] = 3
        result["financial_asset_reinvestment_usd"] = next(
            row["annual_delta_usd"]
            for row in bridge["adjustments"]
            if row["category"] == "financial_asset_reinvestment"
        )
        result["limitations"].append(
            "Financial-asset reinvestment is a signed incremental cash flow outside the consolidated CFO-minus-PPE baseline: negative deploys capital, positive releases it. Reconcile operating reclassifications and eliminations; do not duplicate working capital, leased/PPE investment or loan funding. Borrowing is separate funding, not recurring income. Sustainable lending, losses and financing require independent economic review."
        )
    if "noncommon_equity_cash_flows" in selected:
        result["bridge_version"] = 4
        result["noncommon_equity_cash_flows_usd"] = next(
            row["annual_delta_usd"]
            for row in bridge["adjustments"]
            if row["category"] == "noncommon_equity_cash_flows"
        )
        result["limitations"].append(
            "Noncommon-equity cash flows are signed incremental attribution outside the starting consolidated cash stream, not accounting minority earnings. Negative removes other owners' cash entitlement; positive needs an explicit release/reversal or funding basis, not recurring income by label. Reconcile minority retained ownership and terminal claims. Preferred conversion in the denominator must not also incur the same preferred dividend/claim deduction; equity contributions, issuance and redemptions are capital transfers, not perpetual distributable earnings. Avoid overlap with debt, operating cash, financial assets and employee/legacy compensation. Free-text policy and arithmetic do not independently validate economic attribution."
        )
    if capex_selection:
        result["bridge_version"] = 5
        result["limitations"].append(
            "Explicit source-tag selection retains the original gross cash-purchase fact; it does not net proceeds or add acquisitions, financial investments, noncash lease additions, unpaid asset stocks or lease principal. Any normalization of these items needs separate original support without double counting."
        )
    if compensation_tag == "AllocatedShareBasedCompensationExpense":
        result.update(
            compensation_source_tag=compensation_tag,
            compensation_source_taxonomy=source["content"].get("fact_taxonomies", {}).get(compensation_tag),
            compensation_measure_label="Allocated recognized pretax compensation expense; not verified CFO addback or total award cost",
        )
        result["limitations"].append(
            "Allocated expense is only a historical expense anchor. Capitalized compensation, tax effects, legacy awards and financing claims remain separate; immaterial is not exact zero, and no CFO addback equivalence is assumed."
        )
    return result


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


def annual_cash_flow(source, period_end, cutoff, capital_expenditure_source=None, sources=None):
    selection = capital_expenditure_selection(capital_expenditure_source)
    capex_tag = selection["tag"] if selection else "PaymentsToAcquirePropertyPlantAndEquipment"
    selections = annual_facts(
        source,
        period_end,
        cutoff,
        (
            ("NetCashProvidedByUsedInOperatingActivities", "USD"),
            (capex_tag, "USD"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
        ),
    )
    if selection:
        if not selection["rationale"].strip():
            raise ValueError("Capital-expenditure selection requires an original-line rationale")
        if source["content"].get("fact_taxonomies", {}).get(capex_tag) != "us-gaap":
            raise ValueError("Capital-expenditure selection requires original us-gaap taxonomy provenance")
        baseline = selections[capex_tag]
        for ref in selection["evidence_ids"]:
            original = (sources or {}).get(ref, {})
            url = urlsplit(original.get("url", ""))
            if (
                original.get("source") != "SEC EDGAR filing passage"
                or original.get("original_source") is False
                or original.get("available") is False
                or not isinstance(original.get("content"), str)
                or not original["content"].strip()
                or url.scheme != "https"
                or url.hostname != "www.sec.gov"
                or url.port not in {None, 443}
                or url.username is not None
                or url.password is not None
                or not re.fullmatch(
                    r"/Archives/edgar/data/[0-9]+/"
                    + baseline["accn"].replace("-", "")
                    + r"/[A-Za-z0-9_.-]+\.(?:htm|html|txt)",
                    url.path,
                )
                or original.get("form") != baseline["form"]
            ):
                raise ValueError(
                    "Capital-expenditure selection requires a retained original passage from the same filing"
                )
            for key in ("available_at", "published_at"):
                if original.get(key) and dt(original[key]) > cutoff:
                    raise ValueError("Capital-expenditure selection cites future evidence")
    cash, capex, shares = [dec(r["val"]) for r in selections.values()]
    if capex < 0 or shares <= 0 or cash - capex <= 0:
        raise ValueError("Positive annual cash-flow proxy unavailable; use unsupported valuation")
    value = ((cash - capex) / shares).quantize(dec("0.000001"))
    result = {
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

    if selection:
        result["capital_expenditure_source"] = selection
        result["limitations"].append(
            "Selected original cash-purchase line only; taxonomy name and registered evidence do not independently prove economic scope or normalized cash-flow treatment."
        )
    return result


def cash_flow_diagnostics(source, period_end, cutoff, capital_expenditure_source=None, sources=None):
    """Historical accounting arithmetic, never a normalized forecast or approval."""
    selection = capital_expenditure_selection(capital_expenditure_source)
    proxy = annual_cash_flow(source, period_end, cutoff, selection, sources)
    tags = (
        "ShareBasedCompensation",
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsOfDividendsCommonStock",
        "IncomeTaxesPaidNet",
    )
    extra = annual_facts(
        source,
        period_end,
        cutoff,
        [(t, "USD") for t in (*tags, "AllocatedShareBasedCompensationExpense")],
        optional=True,
    )
    baseline = next(iter(proxy["facts"].values()))
    for record in extra.values():
        if any(record[k] != baseline[k] for k in ("start", "end", "accn", "filed", "form")):
            raise ValueError("Accounting bridge has mixed filing basis")
        if dec(record["val"]) < 0:
            raise ValueError("Negative accounting diagnostic input requires separate review")
    cash = dec(proxy["facts"]["NetCashProvidedByUsedInOperatingActivities"]["val"])
    capex_tag = selection["tag"] if selection else "PaymentsToAcquirePropertyPlantAndEquipment"
    capex = dec(proxy["facts"][capex_tag]["val"])
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
    if selection:
        result["capital_expenditure_source"] = selection
    if "AllocatedShareBasedCompensationExpense" in extra:
        result["allocated_compensation_expense_fact"] = extra["AllocatedShareBasedCompensationExpense"]
        result["limitations"].append(
            "Allocated compensation expense is disclosed separately; it does not substitute for canonical SBC or verify a CFO addback, total award cost or future cash policy."
        )
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
        "limitation": "Sensitivity of supplied cash-flow assumptions, not approval of forecasts or normalized fair value. A negative required multiple means interim cash flows alone exceed the target.",
    }
