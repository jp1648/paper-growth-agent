import copy
import json
from decimal import Decimal

import pytest

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
