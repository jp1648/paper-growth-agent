"""Explicit original source selection, using synthetic records rather than frozen cohorts."""

import copy
import json
from pathlib import Path

import pytest
from test_financials import CAPEX, CUTOFF
from test_normalization import normalization as _normalization

from trader.evaluation import score
from trader.financials import (
    MissingAnnualFact,
    annual_cash_flow,
    capital_expenditure_selection,
    cash_flow_diagnostics,
    normalized_cash_flow,
)

ALTERNATIVE = "PaymentsToAcquireProductiveAssets"


@pytest.fixture
def selected():
    desk, source, bridge = _normalization.__wrapped__()
    source["content"]["facts"][ALTERNATIVE] = source["content"]["facts"].pop(CAPEX)
    source["content"]["fact_taxonomies"] = {ALTERNATIVE: "us-gaap"}
    passage = {
        "id": "capex-passage",
        "source": "SEC EDGAR filing passage",
        "form": "10-K",
        "url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/fixture.htm",
        "published_at": "2026-01-01T00:00:00Z",
        "content": "Synthetic original cash-flow table: gross cash purchases of property and equipment100. Sale proceeds10, business acquisitions20 and noncash lease additions30 are separate. Not issuer evidence.",
    }
    desk.sources[passage["id"]] = passage
    desk.sources[source["id"]] = copy.deepcopy(source)
    selection = {
        "tag": ALTERNATIVE,
        "rationale": "Synthetic original row is gross cash PPE purchases; do not net separate proceeds or include acquisitions/noncash leases",
        "evidence_ids": [passage["id"]],
    }
    return desk, source, bridge, selection


def test_default_missing_canonical_is_never_an_automatic_alias(selected):
    desk, source, _, selection = selected
    before = copy.deepcopy(source)
    with pytest.raises(MissingAnnualFact, match=CAPEX):
        annual_cash_flow(source, "2025-12-31", CUTOFF)
    proxy = annual_cash_flow(source, "2025-12-31", CUTOFF, selection, desk.sources)
    assert proxy["facts"][ALTERNATIVE]["val"] == 100 and CAPEX not in proxy["facts"]
    assert proxy["cash_flow_per_share"] == "5.000000"
    assert proxy["capital_expenditure_source"] == selection
    assert source == before
    diagnostic = cash_flow_diagnostics(source, "2025-12-31", CUTOFF, selection, desk.sources)
    assert diagnostic["cfo_less_capex_usd"] == "500"
    assert diagnostic["capital_expenditure_source"] == selection


@pytest.mark.parametrize(
    "fault",
    [
        "taxonomy",
        "unknown",
        "empty",
        "other_accession",
        "form",
        "future",
        "derived",
        "blank_rationale",
        "currency",
        "quarter",
        "mixed_accession",
        "negative",
        "unknown_tag",
    ],
)
def test_alternative_requires_same_original_filing_and_supported_annual_basis(selected, fault):
    desk, source, _, selection = selected
    passage = desk.sources["capex-passage"]
    fact = source["content"]["facts"][ALTERNATIVE]["USD"][0]
    if fault == "taxonomy":
        source["content"]["fact_taxonomies"][ALTERNATIVE] = "custom"
    elif fault == "unknown":
        selection["evidence_ids"] = ["invented"]
    elif fault == "empty":
        passage["content"] = " "
    elif fault == "other_accession":
        passage["url"] = passage["url"].replace("000000000126000001", "000000000126000002")
    elif fault == "form":
        passage["form"] = "10-Q"
    elif fault == "future":
        passage["available_at"] = "2999-01-01T00:00:00Z"
    elif fault == "derived":
        passage["source"] = "derived calculation"
    elif fault == "blank_rationale":
        selection["rationale"] = " "
    elif fault == "currency":
        source["content"]["facts"][ALTERNATIVE] = {"EUR": [fact]}
    elif fault == "quarter":
        fact.update(start="2025-10-01", form="10-Q")
    elif fault == "mixed_accession":
        fact["accn"] = "0000000001-26-000002"
    elif fault == "negative":
        fact["val"] = -100
    else:
        selection["tag"] = "AnyCapitalNumber"
    with pytest.raises((ValueError, TypeError)):
        annual_cash_flow(source, "2025-12-31", CUTOFF, selection, desk.sources)


def test_both_tags_require_explicit_choice_and_do_not_net_unrelated_amounts(selected):
    desk, source, _, selection = selected
    source["content"]["facts"][CAPEX] = {
        "USD": [{**source["content"]["facts"][ALTERNATIVE]["USD"][0], "val": 90}]
    }
    canonical = annual_cash_flow(source, "2025-12-31", CUTOFF)
    alternative = annual_cash_flow(source, "2025-12-31", CUTOFF, selection, desk.sources)
    assert canonical["cash_flow_per_share"] == "5.100000"
    assert alternative["cash_flow_per_share"] == "5.000000"
    assert "capital_expenditure_source" not in canonical


def test_bridge_selection_version_and_default_serialization(selected):
    desk, source, bridge, selection = selected
    canonical_bridge = copy.deepcopy(bridge)
    canonical_source = copy.deepcopy(source)
    canonical_source["content"]["facts"][CAPEX] = canonical_source["content"]["facts"].pop(ALTERNATIVE)
    canonical = normalized_cash_flow(canonical_source, "2025-12-31", CUTOFF, canonical_bridge, desk.sources)
    explicit_none = normalized_cash_flow(
        canonical_source,
        "2025-12-31",
        CUTOFF,
        {**canonical_bridge, "capital_expenditure_source": None},
        desk.sources,
    )
    assert canonical == explicit_none and "capital_expenditure_source" not in canonical["assumptions"]
    bridge["capital_expenditure_source"] = selection
    alternative = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    assert alternative["bridge_version"] == 5
    assert alternative["historical_cfo_less_capex_usd"] == canonical["historical_cfo_less_capex_usd"]
    assert alternative["cash_flow_per_share"] == canonical["cash_flow_per_share"]
    assert "capex-passage" in alternative["evidence_ids"]
    assert capital_expenditure_selection(None, bridge) == selection
    with pytest.raises(ValueError, match="Conflicting"):
        capital_expenditure_selection(selection, canonical_bridge)


def test_real_tools_replay_and_conflicting_bridge_fail_closed(selected):
    desk, source, bridge, selection = selected
    tools = {t.name: t for t in desk.tools()}
    base = {"financial_source_id": source["id"], "financial_period_end": "2025-12-31"}
    missing = tools["inspect_cash_flow"].invoke(base)
    assert missing["available"] is False
    direct = tools["inspect_cash_flow"].invoke({**base, "capital_expenditure_source": selection})
    assert direct["cfo_less_capex_usd"] == "500"
    bridge["capital_expenditure_source"] = selection
    inspected = tools["inspect_cash_flow"].invoke({**base, "normalization_bridge": bridge})
    args = {
        **base,
        "normalization_bridge": bridge,
        "cash_flow_per_share": inspected["cash_flow_per_share"],
        "growth_rates": ["0"] * 3,
        "discount_rate": ".1",
        "terminal_multiples": ["0"] * 3,
        "years": 1,
    }
    calculation = tools["value_scenarios"].invoke(args)
    assert calculation["normalization_bridge"]["bridge_version"] == 5
    assert calculation["inputs"]["capital_expenditure_source"] == selection
    assert tools["value_scenarios"].invoke(args) == calculation
    with pytest.raises(ValueError, match="Conflicting"):
        tools["value_scenarios"].invoke(
            {**args, "capital_expenditure_source": {**selection, "rationale": "Different policy"}}
        )
    # New selection replays under unchanged financial-provenance criteria; no numeric rubric waiver.
    case = json.loads(Path("evaluations/baseline-v1.json").read_text())["cases"][0]
    candidate = json.loads(Path("tests/fixtures/evaluation/recording.json").read_text())["cases"][case["id"]]
    case["inputs"]["sources"] = list(desk.sources.values())
    case["expected"].update(required_evidence_ids=[source["id"]], cash_flow_per_share="5.000000")
    candidate["sources"] = []
    for report in [candidate["decision"], *candidate["reports"].values()]:
        report["evidence_ids"] = [source["id"], "assumptions", "capex-passage"]
    valuation = candidate["reports"]["valuation"]
    valuation.update(
        {key: calculation[key] for key in ("calculation_id", "bear_value", "base_value", "bull_value")}
    )
    candidate["calculations"] = {calculation["calculation_id"]: calculation}
    assert score(case, candidate)["acceptance"] == "pending_human_review"
    calculation["inputs"]["capital_expenditure_source"]["rationale"] = "Tampered selector"
    assert "unverified_financial_provenance" in score(case, candidate)["failures"]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sec.gov:444/Archives/edgar/data/1/000000000126000001/fixture.htm",
        "https://www.sec.gov:notaport/Archives/edgar/data/1/000000000126000001/fixture.htm",
        "https://www.sec.gov/not-an-archive/000000000126000001/fixture.htm",
        "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/../fixture.htm",
    ],
)
def test_registered_url_must_be_exact_sec_archive_origin(selected, url):
    desk, source, _, selection = selected
    desk.sources["capex-passage"]["url"] = url
    with pytest.raises(ValueError):
        annual_cash_flow(source, "2025-12-31", CUTOFF, selection, desk.sources)


def test_direct_selector_requires_report_citation_not_only_registry_presence(selected):
    from trader.research_team import Valuation

    desk, source, _, selection = selected
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    calc = tool.invoke(
        {
            "financial_source_id": source["id"],
            "financial_period_end": "2025-12-31",
            "capital_expenditure_source": selection,
            "cash_flow_per_share": "5.000000",
            "growth_rates": ["0"] * 3,
            "discount_rate": ".1",
            "terminal_multiples": ["0"] * 3,
            "years": 1,
        }
    )
    report = {
        **{k: calc[k] for k in ["calculation_id", "bear_value", "base_value", "bull_value"]},
        "method": "Synthetic conditional cash flow",
        "assumptions": [],
        "sensitivity": "Unknown",
        "margin_of_safety": "Unknown",
        "evidence_ids": [source["id"]],
    }
    with pytest.raises(ValueError, match="capital-expenditure|Capital-expenditure"):
        desk.validate(Valuation, report)
    report["evidence_ids"].append("capex-passage")
    assert desk.validate(Valuation, report)["calculation_id"] == calc["calculation_id"]
    case = json.loads(Path("evaluations/baseline-v1.json").read_text())["cases"][0]
    candidate = json.loads(Path("tests/fixtures/evaluation/recording.json").read_text())["cases"][case["id"]]
    case["inputs"]["sources"] = list(desk.sources.values())
    case["expected"].update(required_evidence_ids=[source["id"]], cash_flow_per_share="5.000000")
    candidate["sources"] = []
    for r in [candidate["decision"], *candidate["reports"].values()]:
        r["evidence_ids"] = [source["id"]]
    candidate["reports"]["valuation"].update(
        {k: calc[k] for k in ["calculation_id", "bear_value", "base_value", "bull_value"]}
    )
    candidate["calculations"] = {calc["calculation_id"]: calc}
    assert "unverified_capital_expenditure_evidence" in score(case, candidate)["failures"]
    candidate["reports"]["valuation"]["evidence_ids"].append("capex-passage")
    assert "unverified_capital_expenditure_evidence" not in score(case, candidate)["failures"]
