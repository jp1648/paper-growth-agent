import importlib.util
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest


def test_monitor_reads_spend_and_reports_failed_health(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "always_on_monitor", Path(__file__).parents[1] / "deploy/always-on-monitor.py"
    )
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    (tmp_path / "state").mkdir()
    (tmp_path / "config.toml").write_text('research_daily_cost_usd="5"\n')
    day = monitor.dt.datetime.now(monitor.ZoneInfo("America/New_York")).date().isoformat()
    with sqlite3.connect(tmp_path / "state/ledger.sqlite") as db:
        db.execute("CREATE TABLE documents(scope,kind,body)")
        db.execute(
            "INSERT INTO documents VALUES(?,?,?)",
            ("global", "research_spend", json.dumps({"day": day, "usd": "4"})),
        )
    monkeypatch.setattr(monitor.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    values = monitor.measurements(tmp_path)
    assert values["outbox_oldest_age_seconds"] == 1e12
    assert values["healthy"] == 1
    assert values["daily_model_spend_ratio"] == 0.8
    assert 0 <= values["disk_used_ratio"] <= 1
    monkeypatch.setattr(
        monitor.subprocess, "run", Mock(side_effect=monitor.subprocess.TimeoutExpired("docker", 45))
    )
    assert monitor.measurements(tmp_path)["healthy"] == 0


@pytest.mark.parametrize("state", ["empty", "stale", "missing", "corrupt", "invalid_timestamp"])
def test_outbox_age_fails_closed_without_reading_event_contents(tmp_path, state):
    spec = importlib.util.spec_from_file_location(
        "always_on_monitor", Path(__file__).parents[1] / "deploy/always-on-monitor.py"
    )
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    database = tmp_path / "outbox.sqlite"
    if state == "corrupt":
        database.write_bytes(b"not a database")
    elif state != "missing":
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE outbox(id,body)")
            if state != "empty":
                timestamp = (
                    (monitor.dt.datetime.now(monitor.dt.UTC) - monitor.dt.timedelta(seconds=900)).isoformat()
                    if state == "stale"
                    else "bad"
                )
                db.execute("INSERT INTO outbox VALUES (?,?)", ("event", json.dumps({"at": timestamp})))
                if state == "stale":
                    db.execute(
                        "INSERT INTO outbox VALUES (?,?)",
                        ("newer", json.dumps({"at": monitor.dt.datetime.now(monitor.dt.UTC).isoformat()})),
                    )
    age = monitor.outbox_oldest_age(database)
    if state == "empty":
        assert age == 0
    elif state == "stale":
        assert 899 <= age <= 905
    else:
        assert age == 1e12
    if state == "missing":
        assert not database.exists()


@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_unreadable_ledger_preserves_other_health_metrics(tmp_path, monkeypatch, state):
    spec = importlib.util.spec_from_file_location(
        "always_on_monitor", Path(__file__).parents[1] / "deploy/always-on-monitor.py"
    )
    monitor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(monitor)
    (tmp_path / "state").mkdir()
    (tmp_path / "config.toml").write_text('research_daily_cost_usd="5"\n')
    database = tmp_path / "state/ledger.sqlite"
    if state == "corrupt":
        database.write_bytes(b"not a database")
    with sqlite3.connect(tmp_path / "state/telemetry-outbox.sqlite") as db:
        db.execute("CREATE TABLE outbox(id,body)")
    monkeypatch.setattr(monitor.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    values = monitor.measurements(tmp_path)
    assert values["healthy"] == 0
    assert 0 <= values["disk_used_ratio"] <= 1
    assert values["outbox_oldest_age_seconds"] == 0
    assert values["daily_model_spend_ratio"] == 1e12
    if state == "missing":
        assert not database.exists()
