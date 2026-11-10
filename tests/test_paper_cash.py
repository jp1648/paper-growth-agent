import copy
import json
from decimal import Decimal

import pytest
from test_benchmark import ledger_input  # noqa: F401
from test_paper_observations import claim

from trader.paper_cash import interval_interest, replay


def example():
    days = ("2026-11-06", "2026-11-09", "2026-11-10")
    return {
        "version": 1,
        "series": "agent",
        "currency": "USD",
        "coverage_complete": True,
        "annual_rate": "0.05",
        "amendment_sha256": "a" * 64,
        "calendar_sha256": "b" * 64,
        "expected_sessions": list(days),
        "sessions": [
            {
                "date": day,
                "close": day + "T21:00:00Z",
                "cash": "1000.00",
                "locked_cash": "0",
                "events": [],
                "evidence_sha256": "c" * 64,
            }
            for day in days
        ],
    }


def test_weekend_feedback_opening_and_input_preserved():
    doc = example()
    original = copy.deepcopy(doc)
    result = replay(doc)
    assert doc == original
    rows = result["balances"]
    assert [r["interest"]["credit"] for r in rows] == ["0.00", "0.41", "0.14"]
    assert rows[1]["interest"]["calendar_days"] == 3
    assert rows[2]["interest"]["principal"] == "1000.41"
    assert rows[2]["modeled_cash"] == "1000.55"
    assert interval_interest("36.50", "0.05", "2026-11-09", "2026-11-10")["credit"] == "0.01"


def add_interest(doc):
    doc["sessions"][1]["events"] = [
        {
            "id": "interest1",
            "kind": "interest",
            "at": "2026-11-09T12:00:00Z",
            "delta_cash": "1.00",
            "cash_after": "1001.00",
            "locked_cash_after": "0",
            "evidence_sha256": "d" * 64,
        }
    ]
    for row in doc["sessions"][1:]:
        row["cash"] = "1001.00"


def test_booked_interest_removed_once_and_trade_cannot_use_future_credit():
    doc = example()
    add_interest(doc)
    result = replay(doc)
    assert result["balances"][-1]["modeled_cash"] == "1000.55"
    doc["sessions"][1]["events"].append(
        {
            "id": "trade1",
            "kind": "trade",
            "at": "2026-11-09T13:00:00Z",
            "delta_cash": "-1000.20",
            "cash_after": "0.80",
            "locked_cash_after": "0",
            "evidence_sha256": "e" * 64,
        }
    )
    doc["sessions"][1]["cash"] = "0.80"
    with pytest.raises(ValueError, match="unsupported financing"):
        replay(doc)


def test_interest_reversal_and_locked_cash_remain_reconciled():
    doc = example()
    add_interest(doc)
    doc["sessions"][2]["events"] = [
        {
            "id": "reversal",
            "kind": "interest",
            "at": "2026-11-10T12:00:00Z",
            "delta_cash": "-1.00",
            "cash_after": "1000.00",
            "locked_cash_after": "0",
            "evidence_sha256": "d" * 64,
        }
    ]
    doc["sessions"][2]["cash"] = "1000.00"
    assert replay(doc)["balances"][-1]["modeled_cash"] == "1000.55"
    doc["sessions"][1]["events"][0]["locked_cash_after"] = "1001.00"
    doc["sessions"][1]["locked_cash"] = "1001.00"
    with pytest.raises(ValueError, match="unsupported financing"):
        replay(doc)


@pytest.mark.parametrize("fault", ["duplicate", "flow", "unknown", "time", "cash", "all_cash"])
def test_reject_unreconciled_events(fault):
    doc = example()
    add_interest(doc)
    event = doc["sessions"][1]["events"][0]
    if fault == "duplicate":
        doc["sessions"][1]["events"].append(copy.deepcopy(event))
    elif fault in ("flow", "unknown"):
        event["kind"] = fault
    elif fault == "time":
        event["at"] = "2026-11-06T20:00:00Z"
    elif fault == "cash":
        event["cash_after"] = "1002.00"
    else:
        doc["series"] = "all_cash"
    with pytest.raises(ValueError):
        replay(doc)


@pytest.mark.parametrize("rate", ["0", "0.02", "0.05"])
def test_common_agent_allcash_formula(rate):
    doc = example()
    doc["annual_rate"] = rate
    agent = replay(doc)
    doc["series"] = "all_cash"
    assert replay(doc)["balances"] == agent["balances"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(coverage_complete=False),
        lambda d: d.update(annual_rate="0.03"),
        lambda d: d.update(amendment_sha256="bad"),
        lambda d: d["sessions"][1].update(cash="1001.00"),
        lambda d: d["sessions"][1].update(locked_cash="1.00"),
        lambda d: d["sessions"][1].update(cash="NaN"),
        lambda d: d["sessions"].reverse(),
    ],
)
def test_reject_incomplete_or_inconsistent_inputs(change):
    doc = example()
    change(doc)
    with pytest.raises((ValueError, TypeError)):
        replay(doc)


def test_cli(tmp_path, capsys):
    from trader.cli import main

    path = tmp_path / "cash.json"
    path.write_text(json.dumps(example()))
    assert main(["model-paper-cash", "--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "HYPOTHETICAL"


def test_interest_precision_and_boundaries():
    for amount in (True, "-1", "Infinity", "0.001", "1000000000001"):
        with pytest.raises((ValueError, TypeError)):
            interval_interest(amount, "0.05", "2026-11-09", "2026-11-10")
    assert Decimal(interval_interest("1000", "0", "2026-11-09", "2026-11-10")["credit"]) == 0


@pytest.fixture
def matrix_input(ledger_input):  # noqa: F811
    from trader.paper_cash import AMENDMENT_SHA256

    cash = example()
    cash["amendment_sha256"] = AMENDMENT_SHA256
    add_interest(cash)
    points = [
        {
            "date": r["date"],
            "portfolio": claim(r["cash"], cost="0" if i == 0 else "1"),
            "benchmark": claim("1000"),
            "external_deposits": "0",
            "external_withdrawals": "0",
            "flow_evidence": "fixture",
        }
        for i, r in enumerate(cash["sessions"])
    ]
    nav = {
        "version": 1,
        "currency": "USD",
        "benchmark": "SPY",
        "flow_policy": "no_external_flows",
        "source_manifest_sha256": "f" * 64,
        "benchmark_method": "fixture",
        "cost_allocation_method": "fixture",
        "opening": points[0],
        "observations": points[1:],
        "expected_sessions": cash["expected_sessions"][1:],
    }
    inventory = {
        "observations": [
            {"date": r["date"], "portfolio": {"cash": r["cash"], "marked_equity": r["cash"]}}
            for r in cash["sessions"]
        ]
    }
    for point in points:
        point["portfolio"]["cash_interest_liability_not_in_equity"] = {
            "amount": "0",
            "status": "final",
            "evidence_sha256": "d" * 64,
        }
        point["portfolio"]["cash_interest_receivable_not_in_equity"] = {
            "amount": "0",
            "status": "final",
            "evidence_sha256": "d" * 64,
        }
    ledger_input.update(calendar_sha256=cash["calendar_sha256"], expected_sessions=cash["expected_sessions"])
    ledger_input["sessions"] = ledger_input["sessions"][:3]
    for row, cash_row in zip(ledger_input["sessions"], cash["sessions"], strict=True):
        row.update(date=cash_row["date"], close=cash_row["close"], mark="100")
    return nav, cash, inventory, {s: deepcopy_ledger(ledger_input, s) for s in ("SPY", "QQQ")}


def deepcopy_ledger(ledger, symbol):
    result = copy.deepcopy(ledger)
    result["symbol"] = symbol
    return result


def test_matrix_uses_one_portfolio_all_rates_and_removes_interest_once(matrix_input):
    from trader.paper_cash import AMENDMENT_SHA256, compare_scenarios

    before = copy.deepcopy(matrix_input)
    result = compare_scenarios(*matrix_input, AMENDMENT_SHA256)
    assert matrix_input == before
    assert set(result["scenarios"]) == {"0", "0.02", "0.05"}
    for scenario in result["scenarios"].values():
        assert set(scenario["comparisons"]) == {"SPY", "QQQ", "CASH"}
        for symbol, comparison in scenario["comparisons"].items():
            report = comparison["returns"]
            assert report["benchmark"] == symbol
            expected = Decimal(scenario["agent_cash"]["balances"][-1]["modeled_cash"]) - 1
            assert Decimal(report["balances"][-1]["portfolio"]["net_nav"]) == expected
            assert report["comparison_role"] == (
                "primary" if symbol == "SPY" else "secondary_descriptive_only"
            )
    assert (
        result["scenarios"]["0"]["comparisons"]["CASH"]["returns"]["performance"]["net_excess_return"]
        == "-0.001"
    )


@pytest.mark.parametrize("fault", ["equity", "cash", "calendar", "capital", "close", "missing", "amendment"])
def test_matrix_rejects_substitution(matrix_input, fault):
    from trader.paper_cash import AMENDMENT_SHA256, compare_scenarios

    nav, cash, inventory, benchmarks = matrix_input
    if fault == "equity":
        nav["observations"][0]["portfolio"]["reported_equity"] = "1002"
    elif fault == "cash":
        inventory["observations"][1]["portfolio"]["cash"] = "1002"
    elif fault == "calendar":
        benchmarks["QQQ"]["calendar_sha256"] = "d" * 64
    elif fault == "capital":
        benchmarks["QQQ"]["opening_capital"] = "1001"
    elif fault == "close":
        benchmarks["QQQ"]["sessions"][1]["close"] = "2026-11-09T20:59:00Z"
    elif fault == "missing":
        del benchmarks["QQQ"]
    else:
        cash["amendment_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        compare_scenarios(*matrix_input, AMENDMENT_SHA256)


def test_matrix_removes_unposted_interest_but_preserves_other_claims(matrix_input):
    from trader.paper_cash import AMENDMENT_SHA256, compare_scenarios

    nav = matrix_input[0]
    for point in nav["observations"]:
        point["portfolio"]["net_receivables_not_in_equity"] = "12"
        point["portfolio"]["cash_interest_receivable_not_in_equity"]["amount"] = "10"
    result = compare_scenarios(*matrix_input, AMENDMENT_SHA256)
    nav0 = result["scenarios"]["0"]["comparisons"]["CASH"]["returns"]["balances"][-1]["portfolio"]
    assert nav0 == {"gross_nav": "1002.00", "net_nav": "1001.00"}
    nav["observations"][0]["portfolio"]["cash_interest_receivable_not_in_equity"]["status"] = "unknown"
    with pytest.raises(ValueError, match="Unknown actual cash-interest"):
        compare_scenarios(*matrix_input, AMENDMENT_SHA256)


def test_interest_withholding_removed_once_and_unrelated_liabilities_preserved(matrix_input):
    from trader.paper_cash import AMENDMENT_SHA256, compare_scenarios

    nav, cash, inventory, _ = matrix_input
    cash["sessions"][1]["events"].append(
        {
            "id": "tax",
            "kind": "interest_withholding",
            "at": "2026-11-09T13:00:00Z",
            "delta_cash": "-0.20",
            "cash_after": "1000.80",
            "locked_cash_after": "0",
            "evidence_sha256": "d" * 64,
        }
    )
    nav["version"] = 2
    for i, point in enumerate([nav["opening"], *nav["observations"]]):
        for side in ("portfolio", "benchmark"):
            point[side]["accrued_liabilities_not_in_equity"] = {
                "amount": "0",
                "status": "final",
                "evidence": "fixture",
            }
        if i:
            cash["sessions"][i]["cash"] = "1000.80"
            inventory["observations"][i]["portfolio"].update(cash="1000.80", marked_equity="1000.80")
            point["portfolio"]["reported_equity"] = "1000.80"
            point["portfolio"]["accrued_liabilities_not_in_equity"]["amount"] = "0.30"
            point["portfolio"]["cash_interest_liability_not_in_equity"]["amount"] = "0.10"
    result = compare_scenarios(*matrix_input, AMENDMENT_SHA256)
    values = result["scenarios"]["0"]["comparisons"]["CASH"]["returns"]["balances"][-1]["portfolio"]
    assert values == {"gross_nav": "999.80", "net_nav": "998.80"}
    nav["observations"][0]["portfolio"]["cash_interest_liability_not_in_equity"]["status"] = "unknown"
    with pytest.raises(ValueError, match="Unknown interest-linked"):
        compare_scenarios(*matrix_input, AMENDMENT_SHA256)
