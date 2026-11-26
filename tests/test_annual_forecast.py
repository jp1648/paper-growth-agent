import copy
import json
from fractions import Fraction

import pytest
from test_normalization import _batch_args
from test_operating_forecast import normalization as _normalization

from trader.evaluation import scenario_values
from trader.financials import valuation_components, valuation_diagnostics
from trader.models import dec


@pytest.fixture
def normalization():
    return _normalization.__wrapped__()


@pytest.fixture
def path(normalization):
    desk, _, _ = normalization
    desk.sources["annual-policy"] = {
        **desk.sources["assumptions"],
        "id": "annual-policy",
        "published_at": "2026-01-01T00:00:00+00:00",
    }

    def amount(value):
        return {
            "amount_usd": value,
            "rationale": "Disclosed synthetic line; separately reviewed forward policy",
            "evidence_ids": ["annual-policy"],
        }

    def vector(values):
        return {
            "amounts_usd": [*values, values[-1]],
            "rationale": "Chosen independently sourced annual policy, not a residual",
            "evidence_ids": ["annual-policy"],
        }

    historical = {
        name: amount(value)
        for name, value in {
            "operating_profit": "600",
            "net_other_income": "5",
            "tax_provision": "80",
            "depreciation_amortization": "30",
            "sbc_addback": "40",
        }.items()
    }
    line = {"caption": "Other operating liabilities", "kind": "disclosed_operating_asset_liability_change"}
    historical["reconciliation_lines"] = [{**line, **amount("5")}]
    cash = {
        "net_other_income": "5",
        "tax_provision": "80",
        "depreciation_amortization": "30",
        "sbc_addback": "0",
        "future_service_cash_compensation": "0",
        "capital_expenditure": "100",
        "finance_lease_principal": "0",
        "net_borrowing": "0",
        "noncommon_equity_cash_flows": "0",
        "other_investing_cash_flow": "-5",
    }
    case = {
        "segments": [
            {
                "name": "Products",
                "revenues_usd": ["1000", "1100", "1122"],
                "gross_margins": [".8", ".8", ".8"],
                "rationale": "Chosen independently anchored revenue and margin",
                "evidence_ids": ["annual-policy"],
            }
        ],
        "operating_expense": vector(["200", "200"]),
        "cash_rows": [{"category": category, **vector([value, value])} for category, value in cash.items()],
        "reconciliation_lines": [{**line, **vector(["5", "5"])}],
        "compensation_policy": "retain_gaap_sbc_no_cash_replacement",
        "terminal_growth_rate": ".02",
        "terminal_rationale": "Persist final recurring drivers, capex, lease and funding policy; not verified perpetuity",
        "terminal_evidence_ids": ["annual-policy"],
    }
    data = {
        "terminal_cash_convention": "forward_year_cash_no_extra_growth",
        "historical_period_end": "2025-12-31",
        "historical_reconciliation": historical,
        "cash_classification_policy": "gaap_provision_tax_and_disclosed_cfo_reconciliation_no_second_cash_tax_or_interest_deduction",
        "cases": [{"scenario": scenario, **copy.deepcopy(case)} for scenario in ["bear", "base", "bull"]],
    }
    for case, growth in zip(data["cases"], [".02", ".03", ".04"], strict=True):
        case["terminal_growth_rate"] = growth
    return data


def call(normalization, path, **extra):
    desk, source, bridge = normalization
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    return tool.invoke(
        {
            **_batch_args(source, bridge),
            "growth_rates": [],
            "years": 2,
            "terminal_multiples": [
                str(dec(1) / (dec(".1") - dec(case["terminal_growth_rate"]))) for case in path["cases"]
            ],
            "annual_operating_forecast": path,
            **extra,
        }
    )


def test_actual_annual_cash_discounting_terminal_replay_and_diagnostics(normalization, path):
    desk, _, _ = normalization
    original = copy.deepcopy(path)
    result = call(normalization, path)
    forecast = result["annual_operating_forecast"]
    assert forecast["historical_cfo_usd"] == "600"
    assert forecast["cases"][0]["annual_equity_cash_usd"] == ["455.0", "535.0"]
    assert forecast["cases"][0]["terminal_forward_cash_usd"] == "552.6"
    exact_values = [
        Fraction(455, 80) / Fraction(11, 10)
        + Fraction(535, 80) / Fraction(11, 10) ** 2
        + Fraction(5526, 800) / (Fraction(1, 10) - growth) / Fraction(11, 10) ** 2
        for growth in (Fraction(2, 100), Fraction(3, 100), Fraction(4, 100))
    ]
    expected = [
        str((dec(value.numerator) / dec(value.denominator)).quantize(dec(".01"))) for value in exact_values
    ]
    assert (
        [result[k] for k in ("bear_value", "base_value", "bull_value")]
        == expected
        == scenario_values(result["inputs"])
    )
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    assert tool.invoke(result["inputs"]) == result and path == original
    diagnostics = valuation_diagnostics(result["inputs"], "20")
    assert [r["scenario"] for r in diagnostics["rows"]] == ["bear", "base", "bull"]
    assert all("growth_rate" not in row for row in diagnostics["rows"])
    annual, terminal = valuation_components(result["inputs"], "base")
    assert dec(diagnostics["rows"][1]["required_terminal_multiple"]) == (20 - annual) / terminal
    projection = desk.calculation_context()[result["calculation_id"]]
    assert projection["annual_operating_forecast_context"]["status"] == "conditional_annual_path_reconciled"
    assert (
        projection["inputs"]["annual_operating_forecast"]["same_as"]
        == "annual_operating_forecast.assumptions"
    )
    # Normalization supplies the historical scalar/shares, not a second cash delta on the path.
    bridge = copy.deepcopy(result["inputs"]["normalization_bridge"])
    bridge["adjustments"][0]["annual_delta_usd"] = "15"
    assert call(normalization, path, normalization_bridge=bridge)["base_value"] == result["base_value"]


def test_finite_claim_only_changes_interim_cash_and_sbc_modes_are_explicit(normalization, path):
    baseline = call(normalization, path)
    finite = [
        {
            "year": 1,
            "amount_usd": "-88",
            "rationale": "One-time claim separate from recurring drivers",
            "evidence_ids": ["annual-policy"],
        }
    ]
    changed = call(normalization, path, finite_cash_adjustments=finite)
    assert dec(changed["base_value"]) == dec(baseline["base_value"]) - 1
    assert (
        valuation_components(changed["inputs"], "base")[1]
        == valuation_components(baseline["inputs"], "base")[1]
    )
    for case in path["cases"]:
        case["compensation_policy"] = "gaap_sbc_added_back_then_future_service_cash_deducted_once"
        for row in case["cash_rows"]:
            if row["category"] in {"sbc_addback", "future_service_cash_compensation"}:
                row["amounts_usd"] = ["40", "40", "40"]
    assert call(normalization, path)["base_value"] == baseline["base_value"]


@pytest.mark.parametrize(
    "fault",
    [
        "history",
        "length",
        "margin",
        "cashsign",
        "nan",
        "caption",
        "duplicate",
        "sbc",
        "terminal",
        "unknown",
        "future",
        "foreign",
        "schema",
        "growth",
        "endpoint",
        "batch",
    ],
)
def test_bad_annual_paths_are_bounded_feedback_and_atomic(normalization, path, fault):
    desk, _, _ = normalization
    extra = {}
    if fault == "history":
        path["historical_reconciliation"]["tax_provision"]["amount_usd"] = "79"
    elif fault == "length":
        path["cases"][0]["cash_rows"][0]["amounts_usd"].pop()
    elif fault == "margin":
        path["cases"][0]["segments"][0]["gross_margins"][0] = "1.1"
    elif fault == "cashsign":
        path["cases"][0]["cash_rows"][1]["amounts_usd"][0] = "-1"
    elif fault == "nan":
        path["cases"][0]["cash_rows"][0]["amounts_usd"][0] = "NaN"
    elif fault == "caption":
        path["cases"][0]["reconciliation_lines"][0]["caption"] = "Unexplained plug"
    elif fault == "duplicate":
        path["historical_reconciliation"]["reconciliation_lines"] *= 2
    elif fault == "sbc":
        path["cases"][0]["cash_rows"][3]["amounts_usd"][0] = "40"
    elif fault == "terminal":
        path["cases"][0]["terminal_rationale"] = " "
    elif fault == "unknown":
        path["cases"][0]["terminal_evidence_ids"] = ["missing"]
    elif fault == "future":
        desk.sources["annual-policy"]["published_at"] = "2099-01-01T00:00:00Z"
    elif fault == "foreign":
        desk.sources["annual-policy"]["url"] = (
            "https://www.sec.gov/Archives/edgar/data/2/000000000226000001/fixture.htm"
        )
    elif fault == "schema":
        path["cases"][0]["cash_rows"].pop()
    elif fault == "growth":
        extra["growth_rates"] = [".02", ".08", ".14"]
    elif fault == "endpoint":
        extra["operating_forecast"] = {}
    else:
        extra["normalization_alternatives"] = [
            {
                "kind": "compensation_policy",
                "label": "test",
                "replacement": {
                    "annual_cash_usd": "1",
                    "evidence_ids": ["annual-policy"],
                    "rationale": "test",
                    "legacy_award_treatment": "test",
                },
            }
        ]
    result = call(normalization, path, **extra)
    if isinstance(result, str):
        result = json.loads(result)
    assert result["available"] is False and not desk.calculations


def test_invalid_annual_tool_feedback_completes_durable_operation(normalization, path, tmp_path):
    from types import SimpleNamespace

    from trader.research_checkpoint import ResearchCheckpoint
    from trader.research_journal import Journal
    from trader.research_team import Budget, Guard

    desk, source, bridge = normalization
    path["cases"][0]["cash_rows"][0]["amounts_usd"][0] = "invalid"
    invocation = {
        "name": "value_scenarios",
        "id": "bad-annual",
        "type": "tool_call",
        "args": {
            **_batch_args(source, bridge),
            "growth_rates": [],
            "years": 2,
            "terminal_multiples": ["12.5", "14.28571429", "16.66666667"],
            "annual_operating_forecast": path,
        },
    }
    for restarted in (False, True):
        desk.budget = Budget(desk.cfg)
        with Journal(tmp_path / "ops.sqlite", {"run": "annual-feedback"}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal,
                tmp_path / "graph.sqlite",
                phase="correction",
                role="valuation",
                thread_id="annual-feedback",
            )
            try:
                checkpoint.attach_desk(desk)
                tool = next(t for t in desk.tools() if t.name == "value_scenarios")
                guard = Guard(desk.budget, "valuation", {"value_scenarios"}, checkpoint=checkpoint)

                def handler(req, restarted=restarted, tool=tool):
                    assert not restarted
                    return tool.invoke(req.tool_call)

                result = guard.wrap_tool_call(SimpleNamespace(tool_call=invocation), handler)
                assert json.loads(result.content)["available"] is False
                assert journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
            finally:
                checkpoint.close()


def test_forward_terminal_no_extra_growth_or_extra_year_and_rounded_multiple(normalization, path):
    result = call(normalization, path, terminal_multiples=["12.50000000", "14.28571429", "16.66666667"])
    annual, terminal_unit = valuation_components(result["inputs"], "base")
    assert terminal_unit == dec("552.6") / 80 / dec("1.1") ** 2
    # The year-3 vector is a terminal reference, not a third interim payment or another grown amount.
    assert annual == dec(455) / 80 / dec("1.1") + dec(535) / 80 / dec("1.1") ** 2
    invalid = call(normalization, path, terminal_multiples=["12.75", "14.71428571", "17.33333333"])
    assert invalid["available"] is False and "no extra growth" in invalid["note"]
    too_low = call(normalization, path, terminal_multiples=["12.5", "14.2857142", "16.66666667"])
    assert too_low["available"] is False


def test_discount_grid_holds_actual_cash_vectors_and_forward_terminal_fixed(normalization, path):
    desk, _, _ = normalization
    result = call(normalization, path)
    before = copy.deepcopy(desk.calculations)
    inspect = next(tool for tool in desk.tools() if tool.name == "inspect_valuation")
    grid = inspect.invoke(
        {
            "calculation_id": result["calculation_id"],
            "target_price": "20",
            "discount_rates": [".08", ".10", ".12"],
        }
    )["discount_rate_terminal_multiple_grid"]
    assert len(grid) == 9
    for row in grid:
        rate = dec(row["discount_rate"])
        for multiple, value in row["values_by_terminal_multiple"].items():
            expected = (
                dec(455) / 80 / (1 + rate)
                + dec(535) / 80 / (1 + rate) ** 2
                + dec("552.6") / 80 / (1 + rate) ** 2 * dec(multiple)
            )
            assert value == str(expected.quantize(dec(".01")))
    assert desk.calculations == before
    assert (
        inspect.invoke(
            {"calculation_id": result["calculation_id"], "target_price": "20", "discount_rates": ["bad"]}
        )["available"]
        is False
    )


@pytest.mark.parametrize("operation", ["reversed", "bad-discount", "schema-discount"])
def test_expected_annual_order_and_inspection_errors_complete_durable_journal(
    normalization, path, tmp_path, operation
):
    from types import SimpleNamespace

    from trader.research_checkpoint import ResearchCheckpoint
    from trader.research_journal import Journal
    from trader.research_team import Budget, Guard

    desk, source, bridge = normalization
    if operation == "reversed":
        path["cases"][0]["segments"][0]["revenues_usd"] = ["10000", "10000", "10000"]
        invocation = {
            "name": "value_scenarios",
            "id": "reversed-annual",
            "type": "tool_call",
            "args": {
                **_batch_args(source, bridge),
                "growth_rates": [],
                "years": 2,
                "terminal_multiples": ["12.5", "14.28571429", "16.66666667"],
                "annual_operating_forecast": path,
            },
        }
    else:
        result = call(normalization, path)
        invocation = {
            "name": "inspect_valuation",
            "id": "bad-discount",
            "type": "tool_call",
            "args": {
                "calculation_id": result["calculation_id"],
                "target_price": "20",
                "discount_rates": "oops" if operation == "schema-discount" else ["bad"],
            },
        }
    for restarted in (False, True):
        desk.budget = Budget(desk.cfg)
        with Journal(tmp_path / "ops.sqlite", {"run": operation}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal, tmp_path / "graph.sqlite", phase="correction", role="valuation", thread_id=operation
            )
            try:
                checkpoint.attach_desk(desk)
                tool = next(t for t in desk.tools() if t.name == invocation["name"])
                guard = Guard(desk.budget, "valuation", {invocation["name"]}, checkpoint=checkpoint)

                def handler(req, restarted=restarted, tool=tool):
                    assert not restarted
                    return tool.invoke(req.tool_call)

                response = guard.wrap_tool_call(SimpleNamespace(tool_call=invocation), handler)
                assert json.loads(response.content)["available"] is False
                assert journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
            finally:
                checkpoint.close()


def test_annual_report_citations_and_saved_replay_are_enforced(normalization, path):
    from trader.models import now
    from trader.research_team import ROLES, Valuation

    desk, source, _ = normalization
    result = call(normalization, path)
    report = {key: result[key] for key in ["calculation_id", "bear_value", "base_value", "bull_value"]}
    report.update(
        method="Conditional annual driver arithmetic",
        assumptions=["chosen"],
        sensitivity="independent axes",
        margin_of_safety="unknown",
        evidence_ids=[source["id"], "assumptions"],
    )
    with pytest.raises(ValueError, match="operating forecast evidence"):
        desk.validate(Valuation, report)
    report["evidence_ids"].append("annual-policy")
    assert desk.validate(Valuation, report)["calculation_id"] == result["calculation_id"]
    audit = {
        "reports": {role: {} for role in ROLES},
        "calculations": {result["calculation_id"]: copy.deepcopy(result)},
    }
    audit["calculations"][result["calculation_id"]]["annual_operating_forecast"]["cases"][0][
        "terminal_forward_cash_usd"
    ] = "999"
    with pytest.raises(ValueError, match="Saved calculation provenance or values differ"):
        desk.finalize(audit, {role: now().isoformat() for role in ROLES})


@pytest.mark.parametrize("bad", ["not-a-number", "NaN", "Infinity"])
def test_invalid_annual_discount_is_bounded_feedback(normalization, path, bad):
    result = call(normalization, path, discount_rate=bad)
    assert result["available"] is False and result["calculation_created"] is False
