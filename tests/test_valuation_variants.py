import copy
import hashlib
import json

import pytest
from test_supplied_forecast import setup_hypothesis

from trader.models import dec, dumps


def setup():
    desk, source, _ = setup_hypothesis()
    tools = {tool.name: tool for tool in desk.tools()}
    base = tools["evaluate_supplied_forecast"].invoke({"financial_source_id": source["id"]})
    return desk, tools, base["calculation_id"]


def patch(path, value):
    return {
        "path": path.split("/"),
        "value": value,
        "rationale": "Explicit conditional sourced policy stress, not a forecast or bound",
        "evidence_ids": ["fresh-policy"],
    }


def variant(value="110"):
    return {
        "label": "capital-use stress",
        "patches": [
            patch(f"annual_operating_forecast/cases/{i}/cash_rows/5/amounts_usd", [value] * 3)
            for i in range(3)
        ],
    }


def test_variant_canonical_math_lineage_and_parent_immutable():
    desk, tools, base = setup()
    before = copy.deepcopy((desk.calculations, desk.sources, desk.context, desk.reports))
    args = {"calculation_id": base, "variants": [variant(), variant("120")]}
    result = tools["evaluate_valuation_variants"].invoke(args)
    assert len(result["variants"]) == 2
    assert len(dumps(result)) < 2000
    for item in result["variants"]:
        child = desk.calculations[item["calculation_id"]]
        lineage = child["variation_provenance"]
        assert lineage["parent_calculation_id"] == base
        assert (
            lineage["parent_inputs_sha256"]
            == hashlib.sha256(dumps(before[0][base]["inputs"]).encode()).hexdigest()
        )
        assert len(lineage["patches"]) == 3
        replay = tools["value_scenarios"].invoke(child["inputs"])
        assert replay["calculation_id"] == lineage["canonical_calculation_id"]
        assert replay["base_value"] == child["base_value"]
        assert dec(child["base_value"]) < dec(before[0][base]["base_value"])
        assert (
            child["inputs"]["annual_operating_forecast"]["historical_reconciliation"]
            == before[0][base]["inputs"]["annual_operating_forecast"]["historical_reconciliation"]
        )
    assert desk.calculations[base] == before[0][base]
    assert (desk.sources, desk.context, desk.reports) == before[1:]
    saved = copy.deepcopy(desk.calculations)
    assert tools["evaluate_valuation_variants"].invoke(args) == result
    assert desk.calculations == saved


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "negative",
        "leadingzero",
        "bool",
        "nan",
        "inf",
        "resize",
        "duplicate",
        "numericduplicate",
        "noop",
        "source",
        "historical",
        "foreign",
        "future",
        "missing",
        "count",
        "missingbase",
        "latekernel",
    ],
)
def test_variants_fail_closed_and_batch_rolls_back(fault):
    desk, tools, base = setup()
    args = {"calculation_id": base, "variants": [variant()]}
    p = args["variants"][0]["patches"][0]
    if fault == "unknown":
        p["path"][-1] = "not-a-field"
    if fault == "negative":
        p["path"][2] = "-1"
    if fault == "leadingzero":
        p["path"][2] = "00"
    if fault == "bool":
        p["value"] = [True] * 3
    if fault == "nan":
        p["value"] = ["NaN"] * 3
    if fault == "inf":
        p["value"] = ["Infinity"] * 3
    if fault == "resize":
        p["value"] = ["110"] * 4
    if fault == "duplicate":
        args["variants"][0]["patches"].append(copy.deepcopy(p))
    if fault == "numericduplicate":
        args["variants"].append({**variant(), "label": "same numeric policy different label"})
    if fault == "noop":
        p["value"] = ["100.0"] * 3
    if fault == "source":
        p["path"] = ["financial_source_id"]
        p["value"] = "rebound"
    if fault == "historical":
        p["path"] = [
            "annual_operating_forecast",
            "historical_reconciliation",
            "operating_profit",
            "amount_usd",
        ]
        p["value"] = "900"
    if fault == "foreign":
        desk.sources["fresh-policy"]["url"] = desk.sources["fresh-policy"]["url"].replace("/1/", "/999/")
    if fault == "future":
        desk.sources["fresh-policy"]["published_at"] = "2099-01-01T00:00:00+00:00"
    if fault == "missing":
        p["evidence_ids"] = ["missing-original"]
    if fault == "count":
        args["variants"] *= 7
    if fault == "missingbase":
        args["calculation_id"] = "missing"
    if fault == "latekernel":
        args["variants"].append(
            {
                "label": "bad second",
                "patches": [patch("annual_operating_forecast/cases/0/segments/0/gross_margins", ["2"] * 3)],
            }
        )
    before = copy.deepcopy(desk.calculations)
    result = tools["evaluate_valuation_variants"].invoke(args)
    if isinstance(result, str):
        result = json.loads(result)
    assert result["available"] is False, result
    assert result["calculation_created"] is False
    assert desk.calculations == before


def test_shares_rederive_historical_scalar_and_terminal_rules_still_apply():
    desk, tools, base = setup()
    shares = dec(desk.calculations[base]["inputs"]["normalization_bridge"]["valuation_shares"])
    result = tools["evaluate_valuation_variants"].invoke(
        {
            "calculation_id": base,
            "variants": [
                {
                    "label": "legacy shares",
                    "patches": [patch("normalization_bridge/valuation_shares", str(shares * 2))],
                }
            ],
        }
    )
    child = desk.calculations[result["variants"][0]["calculation_id"]]
    assert dec(child["base_value"]) == (dec(desk.calculations[base]["base_value"]) / 2).quantize(dec(".01"))
    assert child["inputs"]["normalization_bridge"]["share_evidence_ids"] == ["fresh-policy"]
    before = copy.deepcopy(desk.calculations)
    failure = tools["evaluate_valuation_variants"].invoke(
        {
            "calculation_id": base,
            "variants": [
                {
                    "label": "terminal mismatch",
                    "patches": [patch("annual_operating_forecast/cases/0/terminal_growth_rate", ".04")],
                }
            ],
        }
    )
    assert failure["available"] is False
    assert desk.calculations == before


def test_variant_batch_durable_replay_is_one_counted_tool_call(tmp_path):
    from types import SimpleNamespace

    from trader.research_checkpoint import ResearchCheckpoint
    from trader.research_journal import Journal
    from trader.research_team import Guard

    desk, tools, base = setup()
    invocation = {
        "name": "evaluate_valuation_variants",
        "id": "explicit-batch",
        "type": "tool_call",
        "args": {"calculation_id": base, "variants": [variant(), variant("120")]},
    }
    with Journal(tmp_path / "ops.sqlite", {"run": "variant"}).lease() as journal:
        checkpoint = ResearchCheckpoint(
            journal, tmp_path / "graph.sqlite", phase="initial", role="valuation", thread_id="variant"
        )
        try:
            checkpoint.attach_desk(desk)
            guard = Guard(desk.budget, "valuation", {"evaluate_valuation_variants"}, checkpoint=checkpoint)
            result = guard.wrap_tool_call(
                SimpleNamespace(tool_call=invocation),
                lambda req: tools["evaluate_valuation_variants"].invoke(req.tool_call),
            )
            saved = copy.deepcopy(desk.calculations)
            assert len(json.loads(result.content)["variants"]) == 2
            assert (
                guard.wrap_tool_call(
                    SimpleNamespace(tool_call=invocation),
                    lambda req: pytest.fail("replay sent new computation"),
                )
                == result
            )
            assert desk.calculations == saved and desk.budget.tools == 1
            assert journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
        finally:
            checkpoint.close()


def test_row_policy_conflict_is_bounded_and_same_labels_do_not_collapse_lineage():
    desk, tools, base = setup()
    original = copy.deepcopy(desk.calculations)
    v = {
        "label": "segment",
        "patches": [
            patch("annual_operating_forecast/cases/0/segments/0/revenues_usd", ["900", "1000", "1020"]),
            patch("annual_operating_forecast/cases/0/segments/0/gross_margins", [".7"] * 3),
        ],
    }
    v["patches"][1]["rationale"] = "Conflicting unexplained row policy"
    assert (
        tools["evaluate_valuation_variants"].invoke({"calculation_id": base, "variants": [v]})["available"]
        is False
    )
    assert desk.calculations == original
    a, b = variant(), variant("120")
    b["label"] = a["label"]
    result = tools["evaluate_valuation_variants"].invoke({"calculation_id": base, "variants": [a, b]})
    assert result["variants"][0]["calculation_id"] != result["variants"][1]["calculation_id"]


def test_all_saved_calculations_replay_for_finalization_and_tamper_fails():
    desk, tools, base = setup()
    result = tools["evaluate_valuation_variants"].invoke({"calculation_id": base, "variants": [variant()]})
    child_id = result["variants"][0]["calculation_id"]
    saved = copy.deepcopy(desk.calculations)
    desk.calculations.clear()
    desk.verify_calculations(saved)
    assert desk.calculations == saved
    for field in ["parent_inputs_sha256", "canonical_calculation_id", "label"]:
        tampered = copy.deepcopy(saved)
        tampered[child_id]["variation_provenance"][field] = "tampered"
        with pytest.raises(ValueError):
            desk.verify_calculations(tampered)
    tampered = copy.deepcopy(saved)
    tampered[child_id]["variation_provenance"]["patches"][0]["before"] = ["999"] * 3
    with pytest.raises(ValueError):
        desk.verify_calculations(tampered)


def test_variant_policy_citations_required_even_for_discount_multiple_changes():
    from trader.research_team import Valuation

    desk, tools, base = setup()
    desk.sources["new-stress-policy"] = {**desk.sources["fresh-policy"], "id": "new-stress-policy"}
    patches = [patch("discount_rate", ".12")] + [
        patch(f"terminal_multiples/{i}", str(dec(1) / (dec(".12") - dec(case["terminal_growth_rate"]))))
        for i, case in enumerate(desk.calculations[base]["inputs"]["annual_operating_forecast"]["cases"])
    ]
    for item in patches:
        item["evidence_ids"] = ["new-stress-policy"]
    result = tools["evaluate_valuation_variants"].invoke(
        {"calculation_id": base, "variants": [{"label": "returnstress", "patches": patches}]}
    )["variants"][0]
    report = {k: result[k] for k in ["calculation_id", "bear_value", "base_value", "bull_value"]}
    report.update(
        method="Conditional sensitivity",
        assumptions=["conditional"],
        sensitivity="discount",
        margin_of_safety="unknown",
        evidence_ids=list(set(desk.sources) - {"new-stress-policy"}),
    )
    with pytest.raises(ValueError, match="operating forecast evidence"):
        desk.validate(Valuation, report)
    report["evidence_ids"].append("new-stress-policy")
    assert desk.validate(Valuation, report)["calculation_id"] == result["calculation_id"]


def test_child_of_child_retains_ancestor_policy_support_and_replays():
    desk, tools, base = setup()
    desk.sources["ancestor-policy"] = {**desk.sources["fresh-policy"], "id": "ancestor-policy"}
    v = variant()
    for item in v["patches"]:
        item["evidence_ids"] = ["ancestor-policy"]
    first = tools["evaluate_valuation_variants"].invoke({"calculation_id": base, "variants": [v]})[
        "variants"
    ][0]["calculation_id"]
    second = tools["evaluate_valuation_variants"].invoke(
        {"calculation_id": first, "variants": [variant("120")]}
    )["variants"][0]["calculation_id"]
    assert desk.calculations[second]["variation_provenance"]["evidence_ids"] == [
        "ancestor-policy",
        "fresh-policy",
    ]
    saved = copy.deepcopy(desk.calculations)
    desk.calculations.clear()
    desk.verify_calculations(saved)
    assert desk.calculations == saved


@pytest.mark.parametrize("fault", ["value", "audit", "missing-parent", "cycle"])
@pytest.mark.parametrize("alias", [False, True])
def test_verification_never_uses_existing_child_as_oracle_and_restores_on_failure(fault, alias):
    desk, tools, base = setup()
    result = tools["evaluate_valuation_variants"].invoke({"calculation_id": base, "variants": [variant()]})
    child_id = result["variants"][0]["calculation_id"]
    child = desk.calculations[child_id]
    if fault == "value":
        child["base_value"] = "999999"
    if fault == "audit":
        child["variation_provenance"]["patches"][0]["before"] = ["999"] * 3
    if fault == "missing-parent":
        desk.calculations.pop(base)
    if fault == "cycle":
        child["variation_provenance"]["parent_calculation_id"] = child_id
    before = copy.deepcopy(desk.calculations)
    original_registry = desk.calculations
    with pytest.raises(ValueError):
        desk.verify_calculations(desk.calculations if alias else copy.deepcopy(desk.calculations))
    assert desk.calculations is original_registry
    assert desk.calculations == before


def test_verification_accepts_valid_input_alias_without_mutating_original_registry():
    desk, tools, base = setup()
    tools["evaluate_valuation_variants"].invoke({"calculation_id": base, "variants": [variant()]})
    original = desk.calculations
    saved = copy.deepcopy(original)
    desk.verify_calculations(original)
    assert desk.calculations == original == saved
