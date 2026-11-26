import copy

import pytest
from test_normalization import (
    _adjustment_variant,
    _batch_args,
)
from test_normalization import (
    normalization as _normalization,
)

from trader.financials import normalized_cash_flow, operating_forecast_bridge
from trader.models import dec


@pytest.fixture
def normalization():
    return _normalization.__wrapped__()


@pytest.fixture
def forecast(normalization):
    desk, _source, _bridge = normalization
    desk.sources["forecasts"] = {
        **desk.sources["assumptions"],
        "id": "forecasts",
        "published_at": "2026-01-01T00:00:00+00:00",
    }
    amount = lambda value: {
        "amount_usd": value,
        "rationale": "Chosen forecast anchored to synthetic current-period disclosure; not a verified future fact",
        "evidence_ids": ["forecasts"],
    }
    # 1000*.8 -200 opex =600 OP; +30 D&A +40 SBC -40 cash compensation
    # -10 interest +5 investment income -80 cash taxes -100 capex =445 equitycash.
    values = {
        "cash_interest_expense": "10",
        "cash_investment_income": "5",
        "cash_taxes": "80",
        "depreciation_amortization": "30",
        "sbc_addback": "40",
        "future_service_cash_compensation": "40",
        "working_capital_cash_flow": "0",
        "capital_expenditure": "100",
        "finance_lease_principal": "0",
        "net_borrowing": "0",
    }
    case = {
        "segments": [
            {
                "name": "Products",
                "revenue_usd": "1000",
                "gross_margin": ".8",
                "rationale": "Chosen forecast",
                "evidence_ids": ["forecasts"],
            }
        ],
        "operating_expense": amount("200"),
        "cash_rows": [{"category": k, **amount(v)} for k, v in values.items()],
        "compensation_policy": "gaap_sbc_added_back_then_future_service_cash_deducted_once",
        "interest_tax_policy": "interest_outside_operating_profit_cash_taxes_deducted_once",
        "legacy_award_policy": "legacy_claims_in_normalization_shares_not_future_service_cash",
        "cash_classification_policy": "working_capital_excludes_tax_interest_capex_and_borrowing_exclude_lease_principal",
    }
    return {
        "historical_period_end": "2025-12-31",
        "year": 1,
        "cases": [{"scenario": name, **copy.deepcopy(case)} for name in ["bear", "base", "bull"]],
    }


def call(normalization, forecast, **extra):
    desk, source, bridge = normalization
    return next(t for t in desk.tools() if t.name == "value_scenarios").invoke(
        {**_batch_args(source, bridge), "operating_forecast": forecast, **extra}
    )


def test_endpoint_is_replayable_and_visible_without_mutating_original(normalization, forecast):
    desk, _source, _bridge = normalization
    result = call(normalization, forecast)
    assert [r["recurring_equity_cash_usd"] for r in result["operating_forecast"]["cases"]] == ["445.0"] * 3
    assert result["operating_forecast"]["status"] == "conditional_endpoint_reconciled"
    assert result["inputs"]["operating_forecast"] == forecast
    saved = copy.deepcopy(desk.calculations)
    projection = desk.calculation_context()[result["calculation_id"]]
    assert projection["operating_forecast_context"]["status"] == "conditional_endpoint_reconciled"
    assert projection["inputs"]["operating_forecast"]["same_as"] == "operating_forecast.assumptions"
    assert desk.calculations == saved
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    assert tool.invoke(result["inputs"]) == result


def test_omission_preserves_old_calculation_and_reports_unverified_context(normalization):
    desk, source, bridge = normalization
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    result = tool.invoke(_batch_args(source, bridge))
    saved = copy.deepcopy(result)
    assert "operating_forecast" not in result and "operating_forecast" not in result["inputs"]
    assert tool.invoke({**result["inputs"], "operating_forecast": None}) == saved
    assert (
        desk.calculation_context()[result["calculation_id"]]["operating_forecast_context"]["status"]
        == "unverified_missing_operating_bridge"
    )
    assert desk.calculations[result["calculation_id"]] == saved


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "nan",
        "infinity",
        "negative",
        "blank",
        "year",
        "period",
        "order",
        "policy",
        "double_sbc",
        "gross_margin",
        "cost_addback",
    ],
)
def test_invalid_bridge_rejected_without_calculation(normalization, forecast, fault):
    case = forecast["cases"][0]
    if fault == "missing":
        case["cash_rows"].pop()
    elif fault == "duplicate":
        case["cash_rows"][-1] = copy.deepcopy(case["cash_rows"][0])
    elif fault in ["nan", "infinity", "negative"]:
        case["cash_rows"][0]["amount_usd"] = {"nan": "NaN", "infinity": "Infinity", "negative": "-1"}[fault]
    elif fault == "blank":
        case["cash_rows"][0]["rationale"] = " "
    elif fault == "year":
        forecast["year"] = 2
    elif fault == "period":
        forecast["historical_period_end"] = "2024-12-31"
    elif fault == "order":
        forecast["cases"][1]["scenario"] = "bear"
    elif fault == "policy":
        case["compensation_policy"] = "ignore SBC"
    elif fault == "double_sbc":
        next(r for r in case["cash_rows"] if r["category"] == "future_service_cash_compensation")[
            "amount_usd"
        ] = "80"
    elif fault == "gross_margin":
        case["segments"][0]["gross_margin"] = "1.1"
    else:
        next(r for r in case["cash_rows"] if r["category"] == "sbc_addback")["amount_usd"] = "10000"
    result = call(normalization, forecast)
    if isinstance(result, str):
        import json

        result = json.loads(result)
    assert result["available"] is False
    assert not normalization[0].calculations


@pytest.mark.parametrize("fault", ["unknown", "issuer", "future", "empty", "stale", "not_original", "http"])
def test_citation_gates(normalization, forecast, fault):
    source = normalization[0].sources["forecasts"]
    if fault == "unknown":
        forecast["cases"][0]["segments"][0]["evidence_ids"] = ["missing"]
    elif fault == "issuer":
        source["url"] = source["url"].replace("/data/1/", "/data/2/")
    elif fault == "future":
        source["published_at"] = "2027-01-01T00:00:00+00:00"
    elif fault == "empty":
        source["content"] = " "
    elif fault == "stale":
        source["published_at"] = "2024-01-01T00:00:00+00:00"
    elif fault == "not_original":
        source["original_source"] = False
    else:
        source["url"] = source["url"].replace("https:", "http:")
    result = call(normalization, forecast)
    if isinstance(result, str):
        import json

        result = json.loads(result)
    assert result["available"] is False
    assert not normalization[0].calculations


def test_no_bridge_or_batched_alternatives_allowed(normalization, forecast):
    assert (
        call(normalization, forecast, normalization_bridge=None)["error"]
        == "operating_forecast_configuration"
    )
    assert (
        call(
            normalization,
            forecast,
            normalization_alternatives=[_adjustment_variant(normalization[2], "cash_taxes", "11")],
        )["error"]
        == "operating_forecast_configuration"
    )


def test_finite_adjustment_not_in_recurring_endpoint(normalization, forecast):
    baseline = call(normalization, forecast)
    changed = call(
        normalization,
        forecast,
        finite_cash_adjustments=[
            {
                "year": 1,
                "amount_usd": "-80",
                "rationale": "Explicit finite incremental existing-claim cash, excluded recurring forecast",
                "evidence_ids": ["assumptions"],
            }
        ],
    )
    assert changed["operating_forecast"] == baseline["operating_forecast"]
    assert dec(baseline["base_value"]) - dec(changed["base_value"]) == dec(".91")


def test_rounding_tolerance_is_only_scalar_quantization(normalization, forecast):
    forecast["cases"][0]["operating_expense"]["amount_usd"] = "200.01"
    call(normalization, forecast)
    forecast["cases"][0]["operating_expense"]["amount_usd"] = "200.02"
    assert call(normalization, forecast)["error"] == "operating_forecast_invalid"


def test_actual_run_unexplained_operating_cash_residuals_are_rejected(normalization, forecast):
    from test_financials import CUTOFF

    desk, source, bridge = normalization
    normalized = normalized_cash_flow(source, "2025-12-31", CUTOFF, bridge, desk.sources)
    normalized["bridged_equity_cash_flow_usd"] = "122267000000"
    normalized["assumptions"]["valuation_shares"] = "14674664000"
    forecast["year"] = 5
    for case, (product, services, pm, sm, opex, tax) in zip(
        forecast["cases"],
        [
            ("370", "170", ".37", ".74", "100", ".17"),
            ("435", "217", ".39", ".76", "120", ".20"),
            ("500", "260", ".40", ".77", "140", ".20"),
        ],
        strict=True,
    ):
        segment = case["segments"][0]
        case["segments"] = [
            {**segment, "name": "Products", "revenue_usd": str(dec(product) * 10**9), "gross_margin": pm},
            {**segment, "name": "Services", "revenue_usd": str(dec(services) * 10**9), "gross_margin": sm},
        ]
        case["operating_expense"]["amount_usd"] = str(dec(opex) * 10**9)
        profit = (dec(product) * dec(pm) + dec(services) * dec(sm) - dec(opex)) * 10**9
        for row in case["cash_rows"]:
            row["amount_usd"] = str(profit * dec(tax)) if row["category"] == "cash_taxes" else "0"
    with pytest.raises(ValueError, match="does not reconcile"):
        operating_forecast_bridge(
            forecast,
            period_end="2025-12-31",
            years=5,
            growth_rates=[".02", ".07", ".11"],
            bridge=normalized,
            sources=desk.sources,
            cutoff=CUTOFF,
            issuer_cik="1",
        )


def test_report_must_cite_forecast_provenance(normalization, forecast):
    from trader.research_team import Valuation

    desk, source, _bridge = normalization
    result = call(normalization, forecast)
    report = {k: result[k] for k in ["calculation_id", "bear_value", "base_value", "bull_value"]}
    report.update(
        method="Conditional arithmetic",
        assumptions=["chosen"],
        sensitivity="pending",
        margin_of_safety="unknown",
        evidence_ids=[source["id"], "assumptions"],
    )
    with pytest.raises(ValueError, match="operating forecast evidence"):
        desk.validate(Valuation, report)
    report["evidence_ids"].append("forecasts")
    assert desk.validate(Valuation, report)["calculation_id"] == result["calculation_id"]


@pytest.mark.parametrize("fault", ["mismatch", "schema", "amount", "revenue", "margin"])
def test_invalid_forecast_durable_tool_closes_and_replays(normalization, forecast, tmp_path, fault):
    import json
    from types import SimpleNamespace

    from trader.research_checkpoint import ResearchCheckpoint
    from trader.research_journal import Journal
    from trader.research_team import Budget, Guard

    desk, source, bridge = normalization
    if fault == "mismatch":
        forecast["cases"][0]["operating_expense"]["amount_usd"] = "250"
    elif fault == "schema":
        forecast["cases"][0]["cash_rows"].pop()
    elif fault == "amount":
        forecast["cases"][0]["cash_rows"][0]["amount_usd"] = "not-a-number"
    else:
        forecast["cases"][0]["segments"][0]["revenue_usd" if fault == "revenue" else "gross_margin"] = (
            "not-a-number"
        )
    invocation = {
        "name": "value_scenarios",
        "id": "invalid-operating-bridge",
        "type": "tool_call",
        "args": {**_batch_args(source, bridge), "operating_forecast": forecast},
    }
    for restarted in (False, True):
        desk.budget = Budget(desk.cfg)
        with Journal(tmp_path / "operations.sqlite", {"run": "forecast-feedback"}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal,
                tmp_path / "graph.sqlite",
                phase="correction",
                role="valuation",
                thread_id="forecast-feedback",
            )
            try:
                checkpoint.attach_desk(desk)
                tool = next(t for t in desk.tools() if t.name == "value_scenarios")
                guard = Guard(desk.budget, "valuation", {"value_scenarios"}, checkpoint=checkpoint)

                def handler(req, restarted=restarted, tool=tool):
                    if restarted:
                        pytest.fail("Completed feedback must replay without a second invocation")
                    return tool.invoke(req.tool_call)

                result = guard.wrap_tool_call(SimpleNamespace(tool_call=invocation), handler)
                assert json.loads(result.content)["available"] is False
                assert journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
                assert desk.budget.tools == guard.tool_calls == 1 and not desk.calculations
            finally:
                checkpoint.close()


def test_finalize_rejects_changed_saved_endpoint_provenance(normalization, forecast, monkeypatch):
    from trader.models import now
    from trader.research_team import ROLES

    desk, _source, _bridge = normalization
    result = call(normalization, forecast)
    audit = {
        "reports": {role: {} for role in ROLES},
        "calculations": {result["calculation_id"]: copy.deepcopy(result)},
    }
    audit["calculations"][result["calculation_id"]]["operating_forecast"]["cases"][0][
        "recurring_equity_cash_usd"
    ] = "999"
    with pytest.raises(ValueError, match="Saved calculation provenance or values differ"):
        desk.finalize(audit, {role: now().isoformat() for role in ROLES})


def test_successful_forecast_checkpoint_restore_and_recompute(normalization, forecast, tmp_path):
    import json
    from types import SimpleNamespace

    from trader.research_checkpoint import ResearchCheckpoint
    from trader.research_journal import Journal
    from trader.research_team import Budget, Guard

    desk, source, bridge = normalization
    invocation = {
        "name": "value_scenarios",
        "id": "valid-bridge",
        "type": "tool_call",
        "args": {**_batch_args(source, bridge), "operating_forecast": forecast},
    }
    original_sources = copy.deepcopy(desk.sources)
    saved = None
    for restarted in (False, True):
        desk.calculations = {}
        desk.budget = Budget(desk.cfg)
        with Journal(tmp_path / "ops.sqlite", {"run": "success"}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal, tmp_path / "graph.sqlite", phase="initial", role="valuation", thread_id="success"
            )
            try:
                checkpoint.attach_desk(desk)
                tool = next(t for t in desk.tools() if t.name == "value_scenarios")
                guard = Guard(desk.budget, "valuation", {"value_scenarios"}, checkpoint=checkpoint)

                def handler(req, restarted=restarted, tool=tool):
                    if restarted:
                        pytest.fail("completed tool must replay")
                    return tool.invoke(req.tool_call)

                actual = json.loads(
                    guard.wrap_tool_call(SimpleNamespace(tool_call=invocation), handler).content
                )
                if not restarted:
                    saved = copy.deepcopy(actual)
                assert actual == saved == desk.calculations[actual["calculation_id"]]
                assert desk.sources == original_sources
                assert tool.invoke(saved["inputs"]) == saved
                assert journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
            finally:
                checkpoint.close()


def test_forecast_context_bound_is_recoverable(normalization, forecast):
    # Multibyte text makes the byte limit meaningful independently of char limits.
    for case in forecast["cases"]:
        for row in case["cash_rows"] + [case["operating_expense"]]:
            row["rationale"] = "界" * 240
    result = call(normalization, forecast)
    assert result["error"] == "operating_forecast_invalid" and "16000 bytes" in result["note"]
    assert not normalization[0].calculations
