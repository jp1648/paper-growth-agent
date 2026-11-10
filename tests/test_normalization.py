import copy
import json
from pathlib import Path

import pytest
from test_financials import CFO, CUTOFF
from test_research_team import Evidence

from trader.config import Settings
from trader.evaluation import score
from trader.financials import MissingAnnualFact, normalized_cash_flow
from trader.models import dec
from trader.research_team import Budget, ResearchDesk, Valuation


@pytest.fixture
def normalization():
    provider = Evidence()
    bundle = provider.get("ACME")
    source = bundle["sources"][0]
    facts = source["content"]["facts"]
    facts["ShareBasedCompensation"] = {"USD": [{**facts[CFO]["USD"][0], "val": 40}]}
    support = {"id": "assumptions", "content": "Synthetic share-base and adjustment evidence only."}
    bridge = {
        "adjustments": [
            {
                "category": key,
                "annual_delta_usd": amount,
                "rationale": "Synthetic delta, not whole expense",
                "evidence_ids": ["assumptions"],
            }
            for key, amount in (
                ("cash_taxes", "10"),
                ("working_capital", "-5"),
                ("capital_expenditure", "-20"),
                ("net_borrowing", "0"),
            )
        ],
        "valuation_shares": "80",
        "share_base_date": "2026-09-30",
        "share_evidence_ids": ["assumptions"],
        "share_rationale": "Synthetic constant-share policy",
        "financing_and_claims": "Synthetic cash-equivalent compensation, no additional claims or borrowing",
    }
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        provider,
        {"symbol": "ACME", "portfolio": {}},
        bundle,
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    desk.sources[support["id"]] = support
    return desk, source, bridge


def test_bridge_source_immutability_and_independent_arithmetic(normalization):
    desk, source, bridge = normalization
    original = copy.deepcopy((source, bridge))
    result = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    # (600 CFO - 100 capex - 40 SBC + 10 tax - 5 WC - 20 extra capex) / 80 shares.
    assert result["bridged_equity_cash_flow_usd"] == "445"
    assert result["cash_flow_per_share"] == "5.562500"
    assert result["historical_provenance"]["cash_flow_per_share"] == "5.000000"
    assert result["adjustments_total_usd"] == "-15"
    assert (source, bridge) == original


def test_future_service_policy_replaces_historical_sbc_and_retains_provenance(normalization):
    desk, source, bridge = normalization
    legacy = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert legacy["bridge_version"] == 1
    assert "compensation_policy" not in legacy["assumptions"]
    bridge["compensation_policy"] = {
        "annual_cash_usd": "60",
        "rationale": "Synthetic observed new-grant value used as conditional future cash cost",
        "legacy_award_treatment": "Existing awards reserved in dated shares; no second cash charge",
        "evidence_ids": ["assumptions"],
    }
    result = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert result["bridge_version"] == 2
    assert result["sbc_fact"]["val"] == 40
    assert result["sbc_expense_equivalent_usd"] == "40"
    assert result["future_compensation_cash_usd"] == "60"
    # 600 - 100 - 60 - 15 = 425; the historical 40 is not also deducted.
    assert result["bridged_equity_cash_flow_usd"] == "425"
    assert result["cash_flow_per_share"] == "5.312500"


@pytest.mark.parametrize(
    "fault", ["negative", "nan", "unknown_evidence", "future_evidence", "blank_legacy", "blank_rationale"]
)
def test_future_compensation_policy_rejects_unsupported_inputs(normalization, fault):
    desk, source, bridge = normalization
    policy = {
        "annual_cash_usd": "60",
        "rationale": "Conditional future-service policy",
        "legacy_award_treatment": "Existing shares include awards",
        "evidence_ids": ["assumptions"],
    }
    bridge["compensation_policy"] = policy
    if fault in {"negative", "nan"}:
        policy["annual_cash_usd"] = "-1" if fault == "negative" else "NaN"
    elif fault == "unknown_evidence":
        policy["evidence_ids"] = ["invented"]
    elif fault == "future_evidence":
        desk.sources["assumptions"]["available_at"] = "2027-01-01T00:00:00Z"
    else:
        policy["legacy_award_treatment" if fault == "blank_legacy" else "rationale"] = " "
    with pytest.raises(ValueError):
        normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)


@pytest.mark.parametrize(
    "fault",
    [
        "missing_sbc",
        "mixed_sbc",
        "negative_sbc",
        "duplicate_category",
        "missing_category",
        "unknown_reference",
        "future_source",
        "future_shares",
        "zero_shares",
        "negative_result",
        "nan_delta",
        "blank_rationale",
    ],
)
def test_invalid_normalization_is_not_a_supported_value(normalization, fault):
    desk, source, bridge = normalization
    facts = source["content"]["facts"]
    if fault == "missing_sbc":
        del facts["ShareBasedCompensation"]
    elif fault == "mixed_sbc":
        facts["ShareBasedCompensation"]["USD"][0]["accn"] = "0000000001-26-000002"
    elif fault == "negative_sbc":
        facts["ShareBasedCompensation"]["USD"][0]["val"] = -1
    elif fault == "duplicate_category":
        bridge["adjustments"][0]["category"] = "working_capital"
    elif fault == "missing_category":
        bridge["adjustments"].pop()
    elif fault == "unknown_reference":
        bridge["share_evidence_ids"] = ["invented"]
    elif fault == "future_source":
        desk.sources["assumptions"]["available_at"] = "2027-01-01T00:00:00Z"
    elif fault == "future_shares":
        bridge["share_base_date"] = CUTOFF.date().isoformat()
    elif fault == "zero_shares":
        bridge["valuation_shares"] = "0"
    elif fault == "negative_result":
        bridge["adjustments"][0]["annual_delta_usd"] = "-1000"
    elif fault == "nan_delta":
        bridge["adjustments"][0]["annual_delta_usd"] = "NaN"
    else:
        bridge["adjustments"][0]["rationale"] = " "
    with pytest.raises((ValueError, MissingAnnualFact)):
        normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)


@pytest.mark.parametrize("explicit_compensation", [False, True])
@pytest.mark.parametrize("financial_reinvestment", [False, True])
def test_tools_and_frozen_replay_recompute_bridge_and_require_all_citations(
    normalization, explicit_compensation, financial_reinvestment
):
    desk, source, bridge = normalization
    if explicit_compensation:
        bridge["compensation_policy"] = {
            "annual_cash_usd": "60",
            "rationale": "Synthetic future-service cash replacement",
            "legacy_award_treatment": "Existing awards already reserved in shares",
            "evidence_ids": ["assumptions"],
        }
    per_share = "5.312500" if explicit_compensation else "5.562500"
    present_value = "4.83" if explicit_compensation else "5.06"
    if financial_reinvestment:
        bridge["adjustments"].append(
            {
                "category": "financial_asset_reinvestment",
                "annual_delta_usd": "-40",
                "rationale": "Synthetic incremental lending cash deployment outside consolidated CFO/PPE; no funding netting",
                "evidence_ids": ["assumptions"],
            }
        )
        per_share = "4.812500" if explicit_compensation else "5.062500"
        present_value = "4.38" if explicit_compensation else "4.60"
    tools = {t.name: t for t in desk.tools()}
    args = {
        "financial_source_id": source["id"],
        "financial_period_end": "2025-12-31",
        "normalization_bridge": bridge,
    }
    inspected = tools["inspect_cash_flow"].invoke(args)
    assert inspected["cash_flow_per_share"] == per_share
    args.update(
        cash_flow_per_share=inspected["cash_flow_per_share"],
        growth_rates=["0"] * 3,
        discount_rate=".1",
        terminal_multiples=["0"] * 3,
        years=1,
    )
    calc = tools["value_scenarios"].invoke(args)
    assert calc["base_value"] == present_value  # Cash per share / 1.1, rounded to cents.
    assert calc["normalization_bridge"] == inspected
    with pytest.raises(ValueError, match="source-derived"):
        tools["value_scenarios"].invoke(dict(args, cash_flow_per_share="5"))
    assert len(desk.calculations) == 1

    case = json.loads(Path("evaluations/baseline-v1.json").read_text())["cases"][0]
    candidate = json.loads(Path("tests/fixtures/evaluation/recording.json").read_text())["cases"][case["id"]]
    case["inputs"]["sources"] = list(desk.sources.values())
    case["expected"].update(required_evidence_ids=[source["id"]], cash_flow_per_share="5.000000")
    candidate["sources"] = []
    for report in [candidate["decision"], *candidate["reports"].values()]:
        report["evidence_ids"] = [source["id"], "assumptions"]
    valuation = candidate["reports"]["valuation"]
    valuation.update({key: calc[key] for key in ("calculation_id", "bear_value", "base_value", "bull_value")})
    candidate["calculations"] = {calc["calculation_id"]: calc}
    assert score(case, candidate)["acceptance"] == "pending_human_review"
    if financial_reinvestment:
        calc["inputs"]["normalization_bridge"]["adjustments"][-1]["annual_delta_usd"] = "-41"
        assert "unverified_financial_provenance" in score(case, candidate)["failures"]
        calc["inputs"]["normalization_bridge"]["adjustments"][-1]["annual_delta_usd"] = "-40"
        assert score(case, candidate)["acceptance"] == "pending_human_review"
    # The baseline stays frozen even when a supported bridge changes forecast input.
    case["expected"]["cash_flow_per_share"] = per_share
    assert "unsupported_cash_flow_input" in score(case, candidate)["failures"]
    case["expected"]["cash_flow_per_share"] = "5.000000"
    calc["inputs"]["cash_flow_per_share"] = "5"
    assert "unverified_financial_provenance" in score(case, candidate)["failures"]
    calc["inputs"]["cash_flow_per_share"] = per_share
    assert desk.validate(Valuation, valuation)["base_value"] == present_value
    valuation["evidence_ids"] = [source["id"]]
    with pytest.raises(ValueError, match="bridge evidence"):
        desk.validate(Valuation, valuation)
    assert "unverified_normalization_evidence" in score(case, candidate)["failures"]
    valuation["evidence_ids"].append("assumptions")
    calc["normalization_bridge"]["bridged_equity_cash_flow_usd"] = "999"
    assert "unverified_financial_provenance" in score(case, candidate)["failures"]


def test_missing_sbc_tools_disclose_gap_without_creating_calculation(normalization):
    desk, source, bridge = normalization
    del source["content"]["facts"]["ShareBasedCompensation"]
    # Desk source ownership is retained; explicitly remove from its copied source as well.
    desk.sources[source["id"]] = source
    tools = {t.name: t for t in desk.tools()}
    args = {
        "financial_source_id": source["id"],
        "financial_period_end": "2025-12-31",
        "normalization_bridge": bridge,
    }
    assert tools["inspect_cash_flow"].invoke(args)["available"] is False
    args.update(
        cash_flow_per_share="5",
        growth_rates=["0"] * 3,
        discount_rate=".1",
        terminal_multiples=["0"] * 3,
        years=1,
    )
    assert tools["value_scenarios"].invoke(args)["available"] is False
    assert not desk.calculations


@pytest.mark.parametrize("symbol,sbc", [("AAPL", "12863000000"), ("MSFT", "11974000000")])
def test_frozen_v3_canonical_sec_sbc_enables_arithmetic_not_economic_approval(symbol, sbc):
    import hashlib
    from datetime import datetime

    from trader.financials import annual_facts
    from trader.models import dec

    path = Path("evaluations/annual-filings-v3.json")
    manifest = json.loads(Path("evaluations/annual-filings-v3-manifest.json").read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["suite_sha256"]
    old = json.loads(Path("evaluations/annual-filings-v2.json").read_text())
    new = json.loads(path.read_text())
    for before, after in zip(old["cases"], new["cases"], strict=True):
        stripped = copy.deepcopy(after)
        stripped["inputs"]["sources"][0]["content"]["facts"].pop("ShareBasedCompensation", None)
        stripped["inputs"]["sources"][0]["content"].get("fact_taxonomies", {}).pop(
            "ShareBasedCompensation", None
        )
        assert stripped == before
    case = next(
        c for c in new["cases"] if c["inputs"]["symbol"] == symbol and c["expected"]["outcome"] == "complete"
    )
    src = case["inputs"]["sources"][0]
    assert src["content"]["fact_taxonomies"]["ShareBasedCompensation"] == "us-gaap"
    entry = next(e for e in manifest["sources"] if e["symbol"] == symbol)
    cutoff = datetime.fromisoformat(case["cutoff"])
    record = annual_facts(src, entry["period_end"], cutoff, [("ShareBasedCompensation", "USD")])[
        "ShareBasedCompensation"
    ]
    assert str(record["val"]) == sbc
    assert record["accn"] == entry["accession"]
    bridge = {
        "adjustments": [
            {
                "category": k,
                "annual_delta_usd": "0",
                "rationale": "Arithmetic test only; no economic assertion",
                "evidence_ids": [src["id"]],
            }
            for k in ("cash_taxes", "working_capital", "capital_expenditure", "net_borrowing")
        ],
        "valuation_shares": entry["diluted_shares"],
        "share_base_date": entry["period_end"],
        "share_evidence_ids": [src["id"]],
        "share_rationale": "Historical shares for arithmetic test only",
        "financing_and_claims": "Synthetic zero bridge, not an accepted investment forecast",
    }
    result = normalized_cash_flow(src, entry["period_end"], cutoff, bridge, {src["id"]: src})
    assert dec(result["bridged_equity_cash_flow_usd"]) == dec(entry["cfo_usd"]) - dec(
        entry["capex_usd"]
    ) - dec(sbc)
    original = next(c for c in old["cases"] if c["id"] == case["id"])["inputs"]["sources"][0]
    with pytest.raises(MissingAnnualFact):
        normalized_cash_flow(original, entry["period_end"], cutoff, bridge, {original["id"]: original})


def test_explicit_allocated_expense_is_not_an_automatic_canonical_alias(normalization):
    desk, source, bridge = normalization
    default = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    explicit = normalized_cash_flow(
        source,
        "2025-12-31",
        CUTOFF,
        {**bridge, "compensation_source_tag": "ShareBasedCompensation"},
        desk.sources,
    )
    assert default == explicit and default["bridged_equity_cash_flow_usd"] == "445"
    assert "compensation_source_tag" not in default["assumptions"]
    assert "compensation_source_tag" not in default
    facts = source["content"]["facts"]
    facts["AllocatedShareBasedCompensationExpense"] = {
        "USD": [{**facts["ShareBasedCompensation"]["USD"][0], "val": 75}]
    }
    source["content"]["fact_taxonomies"] = {"AllocatedShareBasedCompensationExpense": "us-gaap"}
    del facts["ShareBasedCompensation"]
    with pytest.raises(MissingAnnualFact, match="ShareBasedCompensation"):
        normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    bridge["compensation_source_tag"] = "AllocatedShareBasedCompensationExpense"
    with pytest.raises(ValueError, match="explicit future-service compensation_policy"):
        normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    bridge["compensation_policy"] = {
        "annual_cash_usd": "60",
        "rationale": "Conditional future replacement assumption, not disclosed historical expense",
        "legacy_award_treatment": "Legacy claims separately reserved in shares; no accounting expense double charge",
        "evidence_ids": ["assumptions"],
    }
    before = copy.deepcopy((source, bridge))
    result = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert result["compensation_source_tag"] == "AllocatedShareBasedCompensationExpense"
    assert result["compensation_source_taxonomy"] == "us-gaap"
    assert "not verified CFO addback" in result["compensation_measure_label"]
    assert result["sbc_fact"]["val"] == 75 and result["sbc_fact"]["unit"] == "USD"
    assert result["sbc_fact"]["accn"] == facts[CFO]["USD"][0]["accn"]
    assert result["future_compensation_cash_usd"] == "60"
    assert (
        result["bridged_equity_cash_flow_usd"] == "425"
    )  # 600 -100 -60 -15; no 75 expense/addback substitution.
    assert (source, bridge) == before


@pytest.mark.parametrize("fault", ["accession", "currency", "conflict", "period", "negative", "taxonomy"])
def test_allocated_expense_selection_keeps_original_annual_guards(normalization, fault):
    desk, source, bridge = normalization
    record = {**source["content"]["facts"][CFO]["USD"][0], "val": 75}
    selected = {"USD": [record]}
    source["content"]["facts"]["AllocatedShareBasedCompensationExpense"] = selected
    source["content"]["fact_taxonomies"] = {"AllocatedShareBasedCompensationExpense": "us-gaap"}
    bridge.update(
        compensation_source_tag="AllocatedShareBasedCompensationExpense",
        compensation_policy={
            "annual_cash_usd": "60",
            "rationale": "Conditional future cash cost",
            "legacy_award_treatment": "Separate outstanding claims",
            "evidence_ids": ["assumptions"],
        },
    )
    if fault == "accession":
        record["accn"] = "0000000001-26-000009"
    elif fault == "currency":
        selected["EUR"] = selected.pop("USD")
    elif fault == "conflict":
        selected["USD"].append({**record, "val": 76})
    elif fault == "period":
        record["start"] = "2025-10-01"
    elif fault == "taxonomy":
        source["content"]["fact_taxonomies"]["AllocatedShareBasedCompensationExpense"] = "custom"
    else:
        record["val"] = -1
    with pytest.raises(ValueError):
        normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)


def test_both_compensation_tags_are_distinct_and_never_summed(normalization):
    from trader.financials import cash_flow_diagnostics

    desk, source, bridge = normalization
    facts = source["content"]["facts"]
    facts["AllocatedShareBasedCompensationExpense"] = {
        "USD": [{**facts["ShareBasedCompensation"]["USD"][0], "val": 75}]
    }
    source["content"]["fact_taxonomies"] = {
        "ShareBasedCompensation": "us-gaap",
        "AllocatedShareBasedCompensationExpense": "us-gaap",
    }
    canonical = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert (
        canonical["sbc_expense_equivalent_usd"] == "40" and canonical["bridged_equity_cash_flow_usd"] == "445"
    )
    diagnostic = cash_flow_diagnostics(source, "2025-12-31", CUTOFF)
    assert diagnostic["sbc_deduction_sensitivity_usd"] == "460"
    assert diagnostic["allocated_compensation_expense_fact"]["val"] == 75
    assert diagnostic["facts"]["ShareBasedCompensation"]["val"] == 40
    assert diagnostic["facts"]["AllocatedShareBasedCompensationExpense"]["val"] == 75
    del facts["ShareBasedCompensation"]
    diagnostic = cash_flow_diagnostics(source, "2025-12-31", CUTOFF)
    assert diagnostic["sbc_deduction_sensitivity_usd"] is None
    assert "ShareBasedCompensation" in diagnostic["missing_facts"]
    assert diagnostic["allocated_compensation_expense_fact"]["val"] == 75


def test_existing_canonical_calculation_and_diagnostic_payloads_are_byte_stable(normalization):
    import hashlib

    from trader.financials import cash_flow_diagnostics
    from trader.models import dumps

    desk, source, bridge = normalization
    normalized = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    diagnostic = cash_flow_diagnostics(source, "2025-12-31", CUTOFF)
    # Pre-change canonical fixture payloads: preserve historical calculation IDs/replay.
    assert (
        hashlib.sha256(dumps(normalized).encode()).hexdigest()
        == "f6aaedcfc9991945ffcbd1aedf970cce49e892ef8a1aead5c933bec7bc22808d"
    )
    assert (
        hashlib.sha256(dumps(diagnostic).encode()).hexdigest()
        == "33a42573c3585cfc29a563dc80248940df98bc4552a5ccbb2333dd0bcbc8ac65"
    )
    assert "compensation_source_tag" not in normalized["assumptions"]
    assert "allocated_compensation_expense_fact" not in diagnostic


@pytest.mark.parametrize("amount,expected", [("-40", "405"), ("20", "465"), ("0", "445")])
def test_optional_financial_reinvestment_is_incremental_signed_and_once(normalization, amount, expected):
    desk, source, bridge = normalization
    before = copy.deepcopy((source, bridge))
    legacy = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    bridge["adjustments"].append(
        {
            "category": "financial_asset_reinvestment",
            "annual_delta_usd": amount,
            "rationale": "Consolidated net lending deployment/release outside the starting CFO/PPE baseline; reconcile operating reclassifications, no lease/funding overlap",
            "evidence_ids": ["assumptions"],
        }
    )
    result = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert result["bridge_version"] == 3
    assert result["financial_asset_reinvestment_usd"] == amount
    assert result["bridged_equity_cash_flow_usd"] == expected
    assert dec(result["adjustments_total_usd"]) == dec(legacy["adjustments_total_usd"]) + dec(amount)
    assert result["historical_provenance"] == legacy["historical_provenance"]
    bridge["adjustments"][3]["annual_delta_usd"] = "10"
    bridge["adjustments"][3]["rationale"] = "Separate net debt funding assumption, not lending income"
    with_funding = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert dec(with_funding["bridged_equity_cash_flow_usd"]) == dec(expected) + 10
    assert source == before[0]


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "missing_core",
        "unknown_category",
        "missing_evidence",
        "future_evidence",
        "blank_rationale",
        "nonfinite",
    ],
)
def test_financial_reinvestment_keeps_category_and_source_guards(normalization, fault):
    desk, source, bridge = normalization
    row = {
        "category": "financial_asset_reinvestment",
        "annual_delta_usd": "-40",
        "rationale": "Synthetic additional consolidated lending investment, outside baseline",
        "evidence_ids": ["finance-assumptions"],
    }
    desk.sources["finance-assumptions"] = {
        "id": "finance-assumptions",
        "source": "SEC EDGAR filing passage",
        "content": "Synthetic consolidated cash lending deployment, not source valuation endorsement",
    }
    bridge["adjustments"].append(row)
    if fault == "duplicate":
        bridge["adjustments"].append(copy.deepcopy(row))
    elif fault == "missing_core":
        bridge["adjustments"].pop(0)
    elif fault == "unknown_category":
        row["category"] = "finance_debt_income"
    elif fault == "missing_evidence":
        row["evidence_ids"] = ["unknown-evidence"]
    elif fault == "future_evidence":
        desk.sources["finance-assumptions"]["available_at"] = "2027-01-01T00:00:00Z"
    elif fault == "blank_rationale":
        row["rationale"] = " "
    else:
        row["annual_delta_usd"] = "NaN"
    with pytest.raises(ValueError):
        normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
