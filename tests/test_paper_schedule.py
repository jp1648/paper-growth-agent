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
    assert attempts[5]["claim_from"] == "2026-11-27T10:00:00-05:00"
    assert attempts[5]["claim_before"] == "2026-11-27T12:30:00-05:00"
    assert result["policy"]["scheduled_denominator"] == 36
    assert result["policy"]["minimum_mechanical_completions"] == 33
    assert result["policy"]["minimum_independent_economic_acceptances"] == 30
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


def test_short_claiming_window_rejected(schedule_input):
    source, output, supplied = schedule_input
    supplied["calendar"][0]["close"] = "10:30"
    source.write_text(json.dumps(supplied))
    with pytest.raises(ValueError, match="too short"):
        build_file(source, output)
    assert not output.exists()


def test_slot_claim_is_durable_across_connections_and_not_overwritten(env):
    from trader.store import Store

    service, _, _ = env
    store = service.store
    receipt = {"at": "2026-11-09T15:00:00Z", "symbol": "AAPL"}
    assert store.claim_paper_slot("study", "slot", receipt)
    other = Store(store.path)
    try:
        assert not other.claim_paper_slot("study", "slot", {**receipt, "symbol": "MSFT"})
        assert len(other.items("paper_slot")) == 1
        assert other.items("paper_slot")[0]["symbol"] == "AAPL"
        assert len(other.events("paper_slot_claimed")) == 1
        # A later failed/unknown outcome does not make the slot reusable.
        key = other.db.execute("SELECT key FROM documents WHERE kind='paper_slot'").fetchone()[0]
        other.put(key, {**other.items("paper_slot")[0], "status": "unknown"}, "paper_slot")
        assert not other.claim_paper_slot("study", "slot", receipt)
    finally:
        other.db.close()


def test_concurrent_slot_claim_has_one_winner(env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from trader.store import Store

    service, _, _ = env
    ready = Barrier(2)

    def claim():
        store = Store(service.store.path)
        try:
            ready.wait(timeout=5)
            return store.claim_paper_slot("study", "slot", {"symbol": "AAPL"})
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(results) == [False, True]
    assert len(service.store.items("paper_slot")) == 1
    assert len(service.store.events("paper_slot_claimed")) == 1


def test_claim_rollback_preserves_atomic_receipt(env):
    import sqlite3

    service, _, _ = env
    store = service.store
    store.db.execute(
        "CREATE TRIGGER reject_claim BEFORE INSERT ON journal WHEN NEW.kind='paper_slot_claimed' BEGIN SELECT RAISE(ABORT, 'fixture journal failure'); END"
    )
    store.db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="journal failure"):
        store.claim_paper_slot("study", "slot", {})
    assert store.items("paper_slot") == []
    assert store.events("paper_slot_claimed") == []


@pytest.mark.parametrize("field", ["study_id", "slot_id", "status"])
def test_claim_receipt_cannot_replace_identity(env, field):
    with pytest.raises(ValueError, match="cannot override"):
        env[0].store.claim_paper_slot("study", "slot", {field: "replacement"})
    assert env[0].store.items("paper_slot") == []
