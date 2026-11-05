"""Read-only health/spend/disk checks; publish gauges with the VM service account."""

import datetime as dt
import json
import pathlib
import shutil
import sqlite3
import subprocess
import tomllib
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path("@TRADER_ROOT@")


def metadata(path):
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/" + path, headers={"Metadata-Flavor": "Google"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode()


def outbox_oldest_age(database):
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
            oldest, total, valid = db.execute(
                "SELECT min(julianday(json_extract(body,'$.at'))), count(*), "
                "count(julianday(json_extract(body,'$.at'))) FROM outbox"
            ).fetchone()
        if total != valid:
            return 1e12
        if not total:
            return 0.0
        now = dt.datetime.now(dt.UTC).timestamp()
        return max(0.0, now - (oldest - 2440587.5) * 86400)
    except (OSError, sqlite3.Error):
        # Unknown export state must exceed the staleness alert threshold.
        return 1e12


def measurements(root=ROOT):
    with (root / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)
    # A failed health command must still publish zero so a live broken process alerts.
    try:
        result = subprocess.run(
            [
                "/usr/bin/docker",
                "exec",
                "trader-executor",
                "/opt/venv/bin/python",
                "-m",
                "trader.health",
                "--config",
                "/run/config.toml",
            ],
            capture_output=True,
            timeout=45,
            check=False,
        )
        healthy = float(result.returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        healthy = 0.0
    values = {"healthy": healthy}
    disk = shutil.disk_usage(root / "state")
    values["disk_used_ratio"] = (disk.total - disk.free) / disk.total
    database = pathlib.Path(config.get("database", "state/ledger.sqlite"))
    if not database.is_absolute():
        database = root / database
    values["outbox_oldest_age_seconds"] = outbox_oldest_age(database.parent / "telemetry-outbox.sqlite")
    day = dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        used = db.execute(
            "SELECT coalesce(sum(CAST(json_extract(body,'$.usd') AS REAL)),0) "
            "FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_extract(body,'$.day')=?",
            (day,),
        ).fetchone()[0]
    limit = float(config["research_daily_cost_usd"])
    values["daily_model_spend_ratio"] = used / limit if limit else float(used > 0)
    return values


def main():
    project = metadata("project/project-id")
    token = json.loads(metadata("instance/service-accounts/default/token"))["access_token"]
    now = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    values = measurements()
    series = [
        {
            "metric": {"type": "custom.googleapis.com/trader/" + name},
            "resource": {"type": "global", "labels": {"project_id": project}},
            "metricKind": "GAUGE",
            "valueType": "DOUBLE",
            "points": [{"interval": {"endTime": now}, "value": {"doubleValue": value}}],
        }
        for name, value in values.items()
    ]
    request = urllib.request.Request(
        f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries",
        data=json.dumps({"timeSeries": series}).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15):
        pass
    print("Health, disk, outbox and estimated model spending metrics published")
    return 0 if values["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
