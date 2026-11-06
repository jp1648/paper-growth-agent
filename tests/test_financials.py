import copy
from datetime import UTC, datetime

import pytest
from test_research_team import Evidence, financial_facts

from trader.config import Settings
from trader.financials import annual_cash_flow
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
    records = source["content"]["facts"][CFO]["USD"]
    records.append({**records[0], "val": 700, "filed": "2026-06-01", "accn": "amended", "form": "10-K/A"})
    result = annual_cash_flow(source, "2025-12-31", CUTOFF)
    assert result["cash_flow_per_share"] == "6.000000"
    assert result["facts"][CFO]["accn"] == "amended"


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
    with pytest.raises(ValueError, match="source-derived"):
        tool.invoke(
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
