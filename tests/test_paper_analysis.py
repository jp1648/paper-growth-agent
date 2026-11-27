"""Independent arithmetic and pairing checks for offline uncertainty diagnostics."""

import json
from datetime import date, timedelta

import pytest

from trader.cli import main
from trader.paper_analysis import paired_uncertainty


@pytest.fixture
def returns():
    sessions = [(date(2026, 1, 1) + timedelta(days=2 * i)).isoformat() for i in range(60)]
    return {
        "version": 1,
        "currency": "USD",
        "benchmark": "SPY",
        "return_basis": "flow_adjusted_net_total_return",
        "source_manifest_sha256": "a" * 64,
        "benchmark_method": "Synthetic fixture only",
        "cost_allocation_method": "Synthetic fixture only",
        "flow_method": "Synthetic fixture only",
        "expected_sessions": sessions,
        "observations": [{"date": s, "portfolio_return": "0.001", "benchmark_return": "0"} for s in sessions],
    }


@pytest.mark.parametrize("count", [60, 61])
def test_constant_known_excess_and_cli_without_config_or_credentials(returns, tmp_path, count):
    if count == 61:
        extra = (date.fromisoformat(returns["expected_sessions"][-1]) + timedelta(days=2)).isoformat()
        returns["expected_sessions"].append(extra)
        returns["observations"].append({"date": extra, "portfolio_return": "0.001", "benchmark_return": "0"})
    source, output = tmp_path / "returns.json", tmp_path / "analysis.json"
    source.write_text(json.dumps(returns))
    assert (
        main(
            [
                "--config",
                "nonexistent.toml",
                "analyze-paper-returns",
                "--input",
                str(source),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    expected = 1.001**count - 1
    assert result["excess_return"] == pytest.approx(expected, abs=1e-12)
    for interval in result["intervals"]:
        assert interval["lower_95"] == pytest.approx(expected, abs=1e-12)
        assert interval["upper_95"] == pytest.approx(expected, abs=1e-12)
    assert result["status"] == "diagnostic_only" and len(result["input_sha256"]) == 64
    prior = output.read_bytes()
    with pytest.raises(FileExistsError):
        main(["analyze-paper-returns", "--input", str(source), "--output", str(output)])
    assert output.read_bytes() == prior


def test_pairing_keeps_identical_volatile_series_zero(returns):
    for i, row in enumerate(returns["observations"]):
        row.update(portfolio_return=0.04 if i % 2 else -0.03, benchmark_return=0.04 if i % 2 else -0.03)
    result = paired_uncertainty(returns)
    assert result["excess_return"] == 0
    assert all(x["lower_95"] == x["upper_95"] == 0 for x in result["intervals"])


def test_compounds_each_side_not_daily_excess_and_is_reproducible(returns):
    for i, row in enumerate(returns["observations"]):
        row.update(portfolio_return=0.1 if i % 2 else -0.1, benchmark_return=0.01)
    first = paired_uncertainty(returns)
    assert first == paired_uncertainty(returns)
    assert first["portfolio_return"] == pytest.approx(0.99**30 - 1)
    assert first["excess_return"] == pytest.approx(0.99**30 - 1.01**60)
    five, ten = first["intervals"]
    assert five["upper_95"] > five["lower_95"]
    assert ten["upper_95"] == pytest.approx(ten["lower_95"], abs=1e-12)
    assert all(x["lower_95"] <= x["upper_95"] for x in first["intervals"])


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "-1", "-1.1"])
def test_invalid_or_missing_returns_are_not_zero(returns, value):
    returns["observations"][0]["portfolio_return"] = value
    with pytest.raises((TypeError, ValueError)):
        paired_uncertainty(returns)


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "order", "short", "weeks", "price", "benchmark", "provenance"]
)
def test_incomplete_or_noncomparable_input_is_rejected(returns, fault):
    if fault == "missing":
        returns["observations"].pop()
    elif fault == "duplicate":
        returns["expected_sessions"][1] = returns["expected_sessions"][0]
    elif fault == "order":
        returns["observations"].reverse()
    elif fault == "short":
        returns["expected_sessions"].pop()
    elif fault == "weeks":
        returns["expected_sessions"] = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]
    elif fault == "price":
        returns["return_basis"] = "price_return"
    elif fault == "benchmark":
        returns["benchmark"] = "QQQ"
    else:
        returns.pop("cost_allocation_method")
    with pytest.raises(ValueError):
        paired_uncertainty(returns)


def test_drawdown_includes_opening_loss_despite_full_recovery(returns):
    for row in returns["observations"]:
        row.update(portfolio_return="0", benchmark_return="0")
    returns["observations"][0].update(portfolio_return="-0.2", benchmark_return="-0.1")
    returns["observations"][1]["portfolio_return"] = "0.25"
    result = paired_uncertainty(returns)
    assert result["portfolio_return"] == pytest.approx(0)
    assert result["portfolio_max_drawdown"] == pytest.approx(0.2)
    assert result["benchmark_max_drawdown"] == pytest.approx(0.1)
    assert "not intraday" in result["drawdown_method"]


def test_drawdown_uses_running_peak_and_compounded_losses(returns):
    for row in returns["observations"]:
        row.update(portfolio_return="0", benchmark_return="0.01")
    # Wealth: 1 -> 2 -> 1.5 -> 1.2 -> 3 -> 2.7. Worst loss is 40%, not 30% or 25%.
    for row, value in zip(returns["observations"], ["1", "-0.25", "-0.2", "1.5", "-0.1"]):
        row["portfolio_return"] = value
    result = paired_uncertainty(returns)
    assert result["portfolio_return"] == pytest.approx(1.7)
    assert result["portfolio_max_drawdown"] == pytest.approx(0.4)
    assert result["benchmark_max_drawdown"] == 0
