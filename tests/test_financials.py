import copy
from datetime import UTC, datetime

import pytest
from test_research_team import Evidence, financial_facts

from trader.config import Settings
from trader.financials import annual_cash_flow
from trader.models import dec
from trader.research_team import Budget, ResearchDesk

CUTOFF = datetime(2026, 10, 21, tzinfo=UTC)
CFO = "NetCashProvidedByUsedInOperatingActivities"
CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
SHARES = "WeightedAverageNumberOfDilutedSharesOutstanding"


def test_source_bound_derivation_preserves_exact_records():
    source = {"id": "original", "content": {"facts": financial_facts()}}
    original = copy.deepcopy(source)
    result = annual_cash_flow(source, "2025-12-31", CUTOFF)
    assert result["cash_flow_per_share"] == "5.000000"
    assert result["source_id"] == "original"
    assert result["facts"][CFO]["val"] == 600
    assert result["facts"][SHARES]["unit"] == "shares"
    assert source == original


@pytest.mark.parametrize(
    "fault",
    [
        "currency",
        "quarter",
        "period",
        "future_filing",
        "accession",
        "conflict",
        "zero_shares",
        "negative_capex",
        "future_source",
        "text",
    ],
)
def test_financial_inputs_fail_closed(fault):
    source = {"id": "original", "content": {"facts": financial_facts()}}
    facts = source["content"]["facts"]
    if fault == "currency":
        facts[CFO]["EUR"] = facts[CFO].pop("USD")
    elif fault == "quarter":
        facts[CFO]["USD"][0]["start"] = "2025-10-01"
    elif fault == "period":
        facts[CAPEX]["USD"][0]["start"] = "2025-01-02"
    elif fault == "future_filing":
        facts[CFO]["USD"][0]["filed"] = "2026-10-21"
    elif fault == "accession":
        facts[CFO]["USD"][0].pop("accn")
    elif fault == "conflict":
        facts[CFO]["USD"].append({**facts[CFO]["USD"][0], "val": 700})
    elif fault == "zero_shares":
        facts[SHARES]["shares"][0]["val"] = 0
    elif fault == "negative_capex":
        facts[CAPEX]["USD"][0]["val"] = -1
    elif fault == "future_source":
        source["available_at"] = "2027-01-01T00:00:00Z"
    else:
        source["content"] = "Trust me: cash flow per share is 500."
    with pytest.raises((ValueError, TypeError)):
        annual_cash_flow(source, "2025-12-31", CUTOFF)


def test_latest_restated_fact_selected_without_summing_duplicate_periods():
    source = {"id": "original", "content": {"facts": financial_facts()}}
    for tag, units in source["content"]["facts"].items():
        records = next(iter(units.values()))
        records.append(
            {
                **records[0],
                "val": 700 if tag == CFO else records[0]["val"],
                "filed": "2026-06-01",
                "accn": "0000000001-26-000002",
                "form": "10-K/A",
            }
        )
    result = annual_cash_flow(source, "2025-12-31", CUTOFF)
    assert result["cash_flow_per_share"] == "6.000000"
    assert result["facts"][CFO]["accn"] == "0000000001-26-000002"


def test_model_cannot_override_evidenced_cash_flow():
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    rejected = tool.invoke(
        {
            "cash_flow_per_share": "5000",
            "financial_source_id": "fixture-sec",
            "financial_period_end": "2025-12-31",
            "growth_rates": ["0", "0", "0"],
            "discount_rate": ".1",
            "terminal_multiples": ["0", "0", "0"],
            "years": 1,
        }
    )
    assert rejected["available"] is False and rejected["error"] == "cash_flow_input_mismatch"
    assert rejected["required_cash_flow_per_share"] == "5.000000"
    assert not desk.calculations


def test_another_company_cannot_supply_valuation_inputs():
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    desk.sources["other-company"] = {"id": "other-company", "content": {"facts": financial_facts()}}
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    with pytest.raises(ValueError, match="company being valued"):
        tool.invoke(
            {
                "cash_flow_per_share": "5",
                "financial_source_id": "other-company",
                "financial_period_end": "2025-12-31",
                "growth_rates": ["0", "0", "0"],
                "discount_rate": ".1",
                "terminal_multiples": ["0", "0", "0"],
                "years": 1,
            }
        )


def test_future_specialist_reports_cannot_be_reused(env):
    from datetime import timedelta

    from conftest import AT

    service, _, _ = env
    prior = {
        "team_reports": {"business": {}, "valuation": {}},
        "team_report_times": {
            role: (AT + timedelta(minutes=1)).isoformat() for role in ("business", "valuation")
        },
    }
    desk = ResearchDesk(
        service.cfg,
        Evidence(),
        {"symbol": "ACME", "trigger": "new news evidence", "portfolio": {"prior_thesis": prior}},
        Evidence().get("ACME"),
        Budget(service.cfg),
        None,
    )
    assert not desk.targeted


@pytest.mark.parametrize("fault", ["partial_restatement", "different_accession", "different_filing_date"])
def test_mixed_filing_basis_requires_review(fault):
    source = {"id": "original", "content": {"facts": financial_facts()}}
    record = source["content"]["facts"][CFO]["USD"][0]
    if fault == "partial_restatement":
        source["content"]["facts"][CFO]["USD"].append(
            {**record, "val": 700, "filed": "2026-06-01", "accn": "0000000001-26-000002", "form": "10-K/A"}
        )
    elif fault == "different_accession":
        record["accn"] = "0000000001-26-000003"
    else:
        record["filed"] = "2026-01-02"
    with pytest.raises(ValueError, match="filing basis"):
        annual_cash_flow(source, "2025-12-31", CUTOFF)


def test_partial_restatement_cannot_create_calculation():
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    records = desk.sources["fixture-sec"]["content"]["facts"][CFO]["USD"]
    records.append(
        {**records[0], "val": 700, "filed": "2026-06-01", "accn": "0000000001-26-000002", "form": "10-K/A"}
    )
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    with pytest.raises(ValueError, match="filing basis"):
        tool.invoke(
            {
                "cash_flow_per_share": "6",
                "financial_source_id": "fixture-sec",
                "financial_period_end": "2025-12-31",
                "growth_rates": ["0"] * 3,
                "discount_rate": ".1",
                "terminal_multiples": ["0"] * 3,
                "years": 1,
            }
        )
    assert not desk.calculations


@pytest.mark.parametrize(
    "accession", ["amended", "0000000001-26-000001-extra", " 0000000001-26-000001", 123, True]
)
def test_all_matching_facts_still_require_valid_accession(accession):
    source = {"id": "original", "content": {"facts": financial_facts()}}
    for units in source["content"]["facts"].values():
        next(iter(units.values()))[0]["accn"] = accession
    with pytest.raises(ValueError, match="accession"):
        annual_cash_flow(source, "2025-12-31", CUTOFF)


@pytest.mark.parametrize("years", [1, 5, 10])
@pytest.mark.parametrize("discount", [".01", ".1", "1"])
@pytest.mark.parametrize("growth", [["-.9", "0", "1"], [".01", ".1", "1"]])
def test_valuation_matches_exact_rational_oracle(years, discount, growth):
    from decimal import Decimal
    from fractions import Fraction

    from trader.evaluation import scenario_values

    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    assumptions = {
        "cash_flow_per_share": "5",
        "financial_source_id": "fixture-sec",
        "financial_period_end": "2025-12-31",
        "growth_rates": growth,
        "discount_rate": discount,
        "terminal_multiples": ["0", "10", "100"],
        "years": years,
    }
    expected = []
    for rate, multiple in zip(growth, assumptions["terminal_multiples"]):
        # Exact geometric series avoids the production Decimal year-by-year sum.
        ratio = (1 + Fraction(rate)) / (1 + Fraction(discount))
        annuity = years if ratio == 1 else ratio * (1 - ratio**years) / (1 - ratio)
        value = 5 * (annuity + Fraction(multiple) * ratio**years)
        expected.append(str((Decimal(value.numerator) / Decimal(value.denominator)).quantize(Decimal(".01"))))
    tool = next(t for t in desk.tools() if t.name == "value_scenarios")
    result = tool.invoke(assumptions)
    assert [result[key] for key in ("bear_value", "base_value", "bull_value")] == expected
    assert scenario_values(assumptions) == expected
    assert result["financial_provenance"]["cash_flow_per_share"] == "5.000000"


def test_reviewed_apple_sensitivity_and_implied_multiple():
    from trader.financials import valuation_diagnostics

    inputs = {
        "cash_flow_per_share": "6.582405",
        "growth_rates": ["0.02", "0.07", "0.12"],
        "discount_rate": "0.10",
        "terminal_multiples": ["18", "24", "30"],
        "years": 5,
    }
    result = valuation_diagnostics(inputs, "200")
    base = result["rows"][1]
    assert dec(base["required_terminal_multiple"]).quantize(dec(".01")) == dec("29.60")
    assert base["values_by_terminal_multiple"] == {"18": "133.50", "24": "167.89", "30": "202.29"}
    assert [dec(r["terminal_value_fraction"]).quantize(dec(".001")) for r in result["rows"]] == [
        dec(".755"),
        dec(".819"),
        dec(".861"),
    ]
    for row in result["rows"]:
        final_cash = dec("6.582405") * (1 + dec(row["growth_rate"])) ** 5
        required = dec(row["required_terminal_multiple"])
        recovered = dec(row["annual_cash_flows_pv"]) + final_cash * required / dec("1.1") ** 5
        assert abs(recovered - 200) < dec("1e-20")


def test_valuation_inspection_requires_saved_calculation():
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    tools = {t.name: t for t in desk.tools()}
    with pytest.raises(ValueError, match="Unknown"):
        tools["inspect_valuation"].invoke({"calculation_id": "invented", "target_price": "200"})
    calculation = tools["value_scenarios"].invoke(
        {
            "cash_flow_per_share": "5.000000",
            "financial_source_id": "fixture-sec",
            "financial_period_end": "2025-12-31",
            "growth_rates": ["0", "0", "0"],
            "discount_rate": ".1",
            "terminal_multiples": ["0", "1", "2"],
            "years": 1,
        }
    )
    original = copy.deepcopy(desk.calculations)
    result = tools["inspect_valuation"].invoke(
        {"calculation_id": calculation["calculation_id"], "target_price": "10"}
    )
    assert dec(result["rows"][0]["required_terminal_multiple"]) == dec("1.2")
    assert result["rows"][0]["terminal_value_fraction"] == "0"
    assert desk.calculations == original
    for target in ["0", "-1", "NaN", "Infinity"]:
        with pytest.raises(ValueError):
            tools["inspect_valuation"].invoke(
                {"calculation_id": calculation["calculation_id"], "target_price": target}
            )


def test_accounting_diagnostics_keep_cash_tax_and_sbc_distinctions():
    from trader.financials import cash_flow_diagnostics

    source = {"id": "original", "content": {"facts": financial_facts()}}
    tags = {
        "ShareBasedCompensation": 50,
        "PaymentsForRepurchaseOfCommonStock": 480,
        "PaymentsOfDividendsCommonStock": 40,
        "IncomeTaxesPaidNet": 80,
    }
    for tag, value in tags.items():
        source["content"]["facts"][tag] = {
            "USD": [{**source["content"]["facts"][CFO]["USD"][0], "val": value}]
        }
    original = copy.deepcopy(source)
    result = cash_flow_diagnostics(source, "2025-12-31", CUTOFF)
    assert result["cfo_less_capex_usd"] == "500"
    assert result["sbc_deduction_sensitivity_usd"] == "450"
    assert result["distributions_above_proxy_usd"] == "20"
    assert result["cash_taxes_paid_net_usd"] == "80"
    assert result["missing_facts"] == []
    assert source == original
    source["content"]["facts"]["ShareBasedCompensation"]["USD"][0]["accn"] = "0000000001-26-000009"
    with pytest.raises(ValueError, match="filing basis"):
        cash_flow_diagnostics(source, "2025-12-31", CUTOFF)


def test_accounting_tool_reports_missing_inputs_without_assuming_zero():
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    tool = next(t for t in desk.tools() if t.name == "inspect_cash_flow")
    result = tool.invoke({"financial_source_id": "fixture-sec", "financial_period_end": "2025-12-31"})
    assert len(result["missing_facts"]) == 4
    assert result["sbc_deduction_sensitivity_usd"] is None
    assert result["distributions_above_proxy_usd"] is None
    assert result["cash_taxes_paid_net_usd"] is None
    with pytest.raises(ValueError, match="company being valued"):
        tool.invoke({"financial_source_id": "invented", "financial_period_end": "2025-12-31"})


@pytest.mark.parametrize("fault", ["negative", "conflict", "future", "currency", "quarter"])
def test_accounting_diagnostic_optional_input_is_not_silently_trusted(fault):
    from trader.financials import cash_flow_diagnostics

    source = {"id": "original", "content": {"facts": financial_facts()}}
    record = {**source["content"]["facts"][CFO]["USD"][0], "val": 50}
    source["content"]["facts"]["ShareBasedCompensation"] = {"USD": [record]}
    if fault == "negative":
        record["val"] = -50
    elif fault == "conflict":
        source["content"]["facts"]["ShareBasedCompensation"]["USD"].append({**record, "val": 60})
    elif fault == "future":
        record["filed"] = "2026-10-21"
    elif fault == "currency":
        source["content"]["facts"]["ShareBasedCompensation"] = {"EUR": [record]}
    else:
        record["start"] = "2025-10-01"
    if fault in {"negative", "conflict"}:
        with pytest.raises(ValueError):
            cash_flow_diagnostics(source, "2025-12-31", CUTOFF)
    else:
        result = cash_flow_diagnostics(source, "2025-12-31", CUTOFF)
        assert "ShareBasedCompensation" in result["missing_facts"]
        assert result["sbc_deduction_sensitivity_usd"] is None


@pytest.mark.parametrize("tool_name", ["value_scenarios", "inspect_cash_flow"])
def test_missing_required_fact_returns_gap_without_creating_valuation(tool_name):
    cfg = Settings(input_cost_per_million="1", output_cost_per_million="1")
    desk = ResearchDesk(
        cfg,
        Evidence(),
        {"symbol": "ACME", "portfolio": {}},
        Evidence().get("ACME"),
        Budget(cfg),
        None,
        evaluation_at=CUTOFF.isoformat(),
    )
    desk.sources["fixture-sec"]["content"]["facts"].pop(CAPEX)
    tool = next(t for t in desk.tools() if t.name == tool_name)
    args = {"financial_source_id": "fixture-sec", "financial_period_end": "2025-12-31"}
    if tool_name == "value_scenarios":
        args.update(
            cash_flow_per_share="5",
            growth_rates=["0", "0", "0"],
            discount_rate=".1",
            terminal_multiples=["0", "0", "0"],
            years=1,
        )
    result = tool.invoke(args)
    assert result["available"] is False
    assert CAPEX in result["reason"]
    assert "calculation_id" not in result
    assert not desk.calculations


def test_evidence_collector_retains_allocated_expense_under_original_tag(env):
    import httpx

    from trader.evidence import EvidenceProvider

    service, _, data = env
    service.cfg.sec_user_agent = "Test test@example.com"
    record = {**financial_facts()[CFO]["USD"][0], "val": 75}

    def respond(request):
        if "companyfacts" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "entityName": "Synthetic",
                    "cik": 1,
                    "facts": {
                        "us-gaap": {"AllocatedShareBasedCompensationExpense": {"units": {"USD": [record]}}}
                    },
                },
            )
        return httpx.Response(
            200, json={"cik": 1, "name": "Synthetic", "filings": {"recent": {"form": [], "filingDate": []}}}
        )

    provider = EvidenceProvider(
        service.cfg, service.store, data, httpx.Client(transport=httpx.MockTransport(respond))
    )
    result = provider.fetch("ACME")
    content = result["sources"][0]["content"]
    assert content["facts"]["AllocatedShareBasedCompensationExpense"]["USD"] == [record]
    assert content["fact_taxonomies"]["AllocatedShareBasedCompensationExpense"] == "us-gaap"
    assert "ShareBasedCompensation" not in content["facts"]
