import copy
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from trader.cli import main
from trader.paper_analysis import paired_uncertainty
from trader.paper_observations import build_observations


def claim(equity="100", receivable="0", cost="0"):
    return {
        "reported_equity": equity,
        "net_receivables_not_in_equity": receivable,
        "complete": True,
        "evidence": "synthetic-nav",
        "cumulative_external_costs": {
            category: {
                "amount": cost if category == "model" else "0",
                "status": "final",
                "evidence": category,
            }
            for category in ("model", "data", "infrastructure", "other")
        },
    }


@pytest.fixture
def document():
    sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
    return {
        "version": 1,
        "currency": "USD",
        "benchmark": "SPY",
        "flow_policy": "no_external_flows",
        "source_manifest_sha256": "a" * 64,
        "benchmark_method": "Synthetic only",
        "cost_allocation_method": "Synthetic only",
        "opening": {"date": "2026-01-01", "portfolio": claim(), "benchmark": claim()},
        "expected_sessions": sessions,
        "observations": [
            {
                "date": day,
                "portfolio": claim(),
                "benchmark": claim(),
                "external_deposits": "0",
                "external_withdrawals": "0",
                "flow_evidence": "synthetic-flows",
            }
            for day in sessions
        ],
    }


def test_dividend_receivable_payment_and_cost_charged_once(document):
    rows = document["observations"]
    # $2 price drop: $1.80 net entitlement offsets all but $0.20 withholding.
    rows[0]["portfolio"] = claim("98", "1.8", "1")
    rows[1]["portfolio"] = claim("99.8", "0", "1")  # payment transfers the receivable
    rows[2]["portfolio"] = claim("109.8", "0", "2")
    original = copy.deepcopy(document)
    result = build_observations(document)
    assert document == original
    assert result["balances"][0]["portfolio"] == {"gross_nav": "99.8", "net_nav": "98.8"}
    returns = result["observations"]
    assert Decimal(returns[0]["portfolio_return"]) == Decimal("-.012")
    assert Decimal(returns[1]["portfolio_return"]) == 0
    wealth = Decimal(1)
    for row in returns:
        wealth *= 1 + Decimal(row["portfolio_return"])
        assert Decimal(row["benchmark_return"]) == 0
    assert float(wealth) == pytest.approx(1.078)
    assert Decimal(returns[0]["portfolio_gross_return"]) == Decimal("-.002")


def test_opening_entitlement_is_not_free_return_and_benchmark_costs_apply(document):
    document["opening"]["benchmark"] = claim("98", "2")
    for row in document["observations"]:
        row["benchmark"] = claim("100", "0", "0.1")
    result = build_observations(document)
    assert Decimal(result["observations"][0]["benchmark_return"]) == Decimal("-.001")
    assert all(Decimal(row["benchmark_return"]) == 0 for row in result["observations"][1:])


@pytest.mark.parametrize(
    "fault",
    [
        "offsetting_flows",
        "unknown_receivable",
        "missing_category",
        "estimate",
        "unknown_cost",
        "incomplete",
        "cost_reversal",
        "zero_nav",
        "opening_cost",
        "missing_session",
        "flow_evidence",
        "nan",
        "boolean",
        "price_basis",
    ],
)
def test_incomplete_or_inconsistent_claims_fail_closed(document, fault):
    row = document["observations"][0]
    nav = row["portfolio"]
    if fault == "offsetting_flows":
        row.update(external_deposits="10", external_withdrawals="10")
    elif fault == "unknown_receivable":
        nav["net_receivables_not_in_equity"] = None
    elif fault == "missing_category":
        del nav["cumulative_external_costs"]["infrastructure"]
    elif fault == "estimate":
        nav["cumulative_external_costs"]["model"]["status"] = "estimated"
    elif fault == "unknown_cost":
        nav["cumulative_external_costs"]["model"]["amount"] = None
    elif fault == "incomplete":
        nav["complete"] = False
    elif fault == "cost_reversal":
        nav["cumulative_external_costs"]["model"]["amount"] = "1"
    elif fault == "zero_nav":
        nav["reported_equity"] = "0"
    elif fault == "opening_cost":
        document["opening"]["portfolio"] = claim(cost="1")
    elif fault == "missing_session":
        document["observations"].pop()
    elif fault == "flow_evidence":
        row["flow_evidence"] = ""
    elif fault == "nan":
        nav["reported_equity"] = "NaN"
    elif fault == "boolean":
        nav["reported_equity"] = True
    else:
        document["flow_policy"] = "end_of_day_approximation"
    with pytest.raises((ValueError, TypeError)):
        build_observations(document)


def test_cli_immutable_output_and_existing_uncertainty_pipeline(document, tmp_path):
    template = document["observations"][0]
    sessions = [(date(2026, 1, 2) + timedelta(days=2 * i)).isoformat() for i in range(60)]
    document["expected_sessions"] = sessions
    document["observations"] = [dict(copy.deepcopy(template), date=day) for day in sessions]
    source, output = tmp_path / "nav.json", tmp_path / "returns.json"
    source.write_text(json.dumps(document))
    args = [
        "--config",
        "absent.toml",
        "build-paper-observations",
        "--input",
        str(source),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    raw = output.read_bytes()
    result = json.loads(raw)
    assert len(result["input_sha256"]) == 64
    assert paired_uncertainty(result)["excess_return"] == 0
    with pytest.raises(FileExistsError):
        main(args)
    assert output.read_bytes() == raw


def test_fee_accrual_then_booked_cash_is_not_two_losses(document):
    document["version"] = 2
    for point in [document["opening"], *document["observations"]]:
        for side in ["portfolio", "benchmark"]:
            point[side]["accrued_liabilities_not_in_equity"] = {
                "amount": "0",
                "status": "final",
                "evidence": "synthetic-no-unbooked-liabilities",
            }
    document["observations"][0]["portfolio"]["accrued_liabilities_not_in_equity"] = {
        "amount": ".01",
        "status": "final",
        "evidence": "synthetic-fee-assessment",
    }
    for row in document["observations"][1:]:
        row["portfolio"]["reported_equity"] = "99.99"
    result = build_observations(document)
    assert result["input_version"] == 2
    assert all(row["portfolio"]["gross_nav"] == "99.99" for row in result["balances"])
    assert Decimal(result["observations"][0]["portfolio_return"]) == Decimal("-.0001")
    assert all(Decimal(row["portfolio_return"]) == 0 for row in result["observations"][1:])


@pytest.mark.parametrize("fault", ["missing", "unknown", "negative", "no_evidence"])
def test_new_observation_version_requires_supported_liability_including_zero(document, fault):
    document["version"] = 2
    for point in [document["opening"], *document["observations"]]:
        for side in ["portfolio", "benchmark"]:
            point[side]["accrued_liabilities_not_in_equity"] = {
                "amount": "0",
                "status": "final",
                "evidence": "synthetic",
            }
    claim = document["observations"][0]["portfolio"]
    liability = claim["accrued_liabilities_not_in_equity"]
    if fault == "missing":
        del claim["accrued_liabilities_not_in_equity"]
    elif fault == "unknown":
        liability["status"] = "unknown"
    elif fault == "negative":
        liability["amount"] = "-.01"
    else:
        liability["evidence"] = ""
    with pytest.raises((KeyError, ValueError)):
        build_observations(document)


def test_legacy_version_cannot_silently_discard_accrued_fee(document):
    document["observations"][0]["portfolio"]["accrued_liabilities_not_in_equity"] = {
        "amount": ".01",
        "status": "final",
        "evidence": "fee",
    }
    with pytest.raises(ValueError, match="version 2"):
        build_observations(document)


@pytest.mark.parametrize("symbol", ["SPY", "QQQ"])
def test_descriptive_returns_drawdown_and_primary_boundary(document, symbol):
    document["benchmark"] = symbol
    rows = document["observations"]
    rows[0]["portfolio"] = claim("110", cost="1")
    rows[1]["portfolio"] = claim("90", cost="2")
    rows[2]["portfolio"] = claim("105", cost="3")
    rows[0]["benchmark"] = claim("99")
    rows[1]["benchmark"] = claim("101")
    rows[2]["benchmark"] = claim("102")
    result = build_observations(document)
    metrics = result["performance"]
    assert Decimal(metrics["portfolio"]["gross_total_return"]) == Decimal(".05")
    assert Decimal(metrics["portfolio"]["net_total_return"]) == Decimal(".02")
    assert float(metrics["portfolio"]["gross_max_drawdown"]) == pytest.approx(20 / 110)
    assert float(metrics["portfolio"]["net_max_drawdown"]) == pytest.approx(21 / 109)
    assert Decimal(metrics["benchmark"]["net_max_drawdown"]) == Decimal(".01")
    assert Decimal(metrics["net_excess_return"]) == 0
    assert result["benchmark"] == symbol
    if symbol == "QQQ":
        assert result["comparison_role"] == "secondary_descriptive_only"
        with pytest.raises(ValueError, match="primary"):
            paired_uncertainty(result)
    else:
        assert result["comparison_role"] == "primary"
    document["benchmark"] = "CASH"
    with pytest.raises(ValueError, match="SPY or"):
        build_observations(document)
