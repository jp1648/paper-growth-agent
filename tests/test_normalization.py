import copy
import json
from pathlib import Path

import pytest
from test_financials import CFO, CUTOFF
from test_research_team import Evidence

from trader.config import Settings
from trader.evaluation import score
from trader.financials import MissingAnnualFact, normalized_cash_flow
from trader.research_team import Budget, ResearchDesk, Valuation


@pytest.fixture
def normalization():
    provider = Evidence()
    bundle = provider.get("ACME")
    source = bundle["sources"][0]
    facts = source["content"]["facts"]
    facts["StockBasedCompensation"] = {"USD": [{**facts[CFO]["USD"][0], "val": 40}]}
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
        del facts["StockBasedCompensation"]
    elif fault == "mixed_sbc":
        facts["StockBasedCompensation"]["USD"][0]["accn"] = "0000000001-26-000002"
    elif fault == "negative_sbc":
        facts["StockBasedCompensation"]["USD"][0]["val"] = -1
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


def test_tools_and_frozen_replay_recompute_bridge_and_require_all_citations(normalization):
    desk, source, bridge = normalization
    tools = {t.name: t for t in desk.tools()}
    args = {
        "financial_source_id": source["id"],
        "financial_period_end": "2025-12-31",
        "normalization_bridge": bridge,
    }
    inspected = tools["inspect_cash_flow"].invoke(args)
    assert inspected["cash_flow_per_share"] == "5.562500"
    args.update(
        cash_flow_per_share=inspected["cash_flow_per_share"],
        growth_rates=["0"] * 3,
        discount_rate=".1",
        terminal_multiples=["0"] * 3,
        years=1,
    )
    calc = tools["value_scenarios"].invoke(args)
    assert calc["base_value"] == "5.06"  # 5.5625 / 1.1, rounded to cents
    assert calc["normalization_bridge"] == inspected
    with pytest.raises(ValueError, match="source-derived"):
        tools["value_scenarios"].invoke(dict(args, cash_flow_per_share="5"))
    assert len(desk.calculations) == 1

    case = json.loads(Path("evaluations/baseline-v1.json").read_text())["cases"][0]
    candidate = json.loads(Path("tests/fixtures/evaluation/recording.json").read_text())["cases"][case["id"]]
    case["inputs"]["sources"] = list(desk.sources.values())
    case["expected"].update(required_evidence_ids=[source["id"]], cash_flow_per_share="5.562500")
    candidate["sources"] = []
    for report in [candidate["decision"], *candidate["reports"].values()]:
        report["evidence_ids"] = [source["id"], "assumptions"]
    valuation = candidate["reports"]["valuation"]
    valuation.update({key: calc[key] for key in ("calculation_id", "bear_value", "base_value", "bull_value")})
    candidate["calculations"] = {calc["calculation_id"]: calc}
    assert score(case, candidate)["acceptance"] == "pending_human_review"
    assert desk.validate(Valuation, valuation)["base_value"] == "5.06"
    valuation["evidence_ids"] = [source["id"]]
    with pytest.raises(ValueError, match="bridge evidence"):
        desk.validate(Valuation, valuation)
    assert "unverified_normalization_evidence" in score(case, candidate)["failures"]
    valuation["evidence_ids"].append("assumptions")
    calc["normalization_bridge"]["bridged_equity_cash_flow_usd"] = "999"
    assert "unverified_financial_provenance" in score(case, candidate)["failures"]


def test_missing_sbc_tools_disclose_gap_without_creating_calculation(normalization):
    desk, source, bridge = normalization
    del source["content"]["facts"]["StockBasedCompensation"]
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
