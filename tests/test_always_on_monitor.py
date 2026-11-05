import importlib.util
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock


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
    assert values["healthy"] == 1
    assert values["daily_model_spend_ratio"] == 0.8
    assert 0 <= values["disk_used_ratio"] <= 1
    monkeypatch.setattr(
        monitor.subprocess, "run", Mock(side_effect=monitor.subprocess.TimeoutExpired("docker", 45))
    )
    assert monitor.measurements(tmp_path)["healthy"] == 0
