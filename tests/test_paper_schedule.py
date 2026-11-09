import copy
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import pytest

from trader.cli import main
from trader.paper_schedule import build_file


@pytest.fixture
def schedule_input(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.paper_schedule.now", lambda: datetime(2026, 11, 9, tzinfo=UTC))
    start = date(2026, 11, 16)
    # Synthetic weekdays, explicitly not an authenticated exchange calendar.
    calendar = [
        {"date": day.isoformat(), "open": "09:30", "close": "16:00"}
        for n in range(110)
        if (day := start + timedelta(days=n)).weekday() < 5
    ]
    return (
        tmp_path / "input.json",
        tmp_path / "output.json",
        {
            "study_id": "fixture-study",
            "start_session": start.isoformat(),
            "calendar": calendar,
        },
    )


def test_schedule_rotates_and_uses_later_window_minimum_without_activation(schedule_input):
    source, output, supplied = schedule_input
    # Four-session holiday week and early close; no weekend catchup.
    supplied["calendar"] = [r for r in supplied["calendar"] if r["date"] != "2026-11-26"]
    next(r for r in supplied["calendar"] if r["date"] == "2026-11-27")["close"] = "13:00"
    original = copy.deepcopy(supplied)
    source.write_text(json.dumps(supplied))
    result = build_file(source, output)
    assert supplied == original
    assert result["end_session"] == "2027-02-08"
    assert len(result["expected_sessions"]) == 60
    attempts = result["opportunities"]
    assert len(attempts) == len({a["id"] for a in attempts}) == len({a["date"] for a in attempts}) == 36
    assert Counter(a["symbol"] for a in attempts) == {"AAPL": 8, "MSFT": 7, "AMZN": 7, "PG": 7, "CAT": 7}
    assert Counter(a["week"] for a in attempts) == {i: 3 for i in range(1, 13)}
    assert [a["date"] for a in attempts[3:6]] == ["2026-11-23", "2026-11-25", "2026-11-27"]
    assert attempts[5]["close"] == "2026-11-27T13:00:00-05:00"
    assert result["execution_universe"] == ["AAPL", "MSFT", "AMZN"]
    assert result["status"] == "draft_not_activated" and result["activation_authorized"] is False
    assert json.loads(output.read_text()) == result
    with pytest.raises(FileExistsError):
        build_file(source, output)


def test_session_count_can_push_end_past_twelve_weeks(schedule_input):
    source, output, supplied = schedule_input
    excluded = {"2026-11-26", "2026-12-25", "2027-01-01"}
    supplied["calendar"] = [r for r in supplied["calendar"] if r["date"] not in excluded]
    source.write_text(json.dumps(supplied))
    result = build_file(source, output)
    assert len(result["expected_sessions"]) == 60
    assert result["end_session"] == "2027-02-10"
    assert len(result["opportunities"]) == 36


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "unordered",
        "short",
        "missing_start",
        "past",
        "bad_date",
        "weekend",
        "late_open",
        "bad_close",
        "timezone",
        "short_week",
        "identity",
        "too_large",
    ],
)
def test_invalid_schedule_never_creates_output(schedule_input, fault):
    source, output, supplied = schedule_input
    days = supplied["calendar"]
    if fault == "duplicate":
        days.insert(1, days[0])
    elif fault == "unordered":
        days[0], days[1] = days[1], days[0]
    elif fault == "short":
        supplied["calendar"] = days[:60]
    elif fault == "missing_start":
        supplied["start_session"] = "2026-11-15"
    elif fault == "past":
        supplied["start_session"] = "2026-11-08"
    elif fault == "bad_date":
        days[0]["date"] = "20261116"
    elif fault == "weekend":
        days[0]["date"] = "2026-11-15"
    elif fault == "late_open":
        days[0]["open"] = "10:00"
    elif fault == "bad_close":
        days[0]["close"] = "09:30"
    elif fault == "timezone":
        days[0]["open"] = "09:30+00:00"
    elif fault == "short_week":
        supplied["calendar"] = days[:2] + days[5:]
    elif fault == "identity":
        supplied["study_id"] = "../other"
    source.write_text("x" * 1_000_001 if fault == "too_large" else json.dumps(supplied))
    with pytest.raises(ValueError):
        build_file(source, output)
    assert not output.exists()


def test_cli_draft_does_not_load_broker_configuration(schedule_input, capsys):
    source, output, supplied = schedule_input
    source.write_text(json.dumps(supplied))
    assert (
        main(
            [
                "--config",
                "nonexistent.toml",
                "draft-paper-schedule",
                "--input",
                str(source),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["activation_authorized"] is False


def test_schedule_uses_new_york_offsets_across_dst(schedule_input):
    source, output, supplied = schedule_input
    shift = date(2027, 3, 1) - date(2026, 11, 16)
    supplied["start_session"] = "2027-03-01"
    for row in supplied["calendar"]:
        row["date"] = (date.fromisoformat(row["date"]) + shift).isoformat()
    source.write_text(json.dumps(supplied))
    result = build_file(source, output)
    assert result["opportunities"][0]["open"] == "2027-03-01T09:30:00-05:00"
    assert result["opportunities"][6]["open"] == "2027-03-15T09:30:00-04:00"
