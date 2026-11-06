"""Read-only liveness check; never creates a ledger or calls a model/broker."""

import argparse
import json
import sqlite3
from pathlib import Path

from .config import Settings
from .models import dt, now


def check_health(cfg):
    uri = Path(cfg.database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:

        def read(scope, key):
            row = db.execute(
                "SELECT body FROM documents WHERE scope=? AND kind='state' AND key=?", (scope, key)
            ).fetchone()
            return json.loads(row[0]) if row else None

        experiment = read("global", "active")
        if not experiment:
            raise ValueError("No initialized experiment")
        heartbeat = read(experiment["id"], "heartbeat")
        recon = read(experiment["id"], "reconciliation")
        if not heartbeat or not recon or not recon.get("ok"):
            raise ValueError("Heartbeat or successful reconciliation missing")
        for item in (heartbeat, recon):
            age = (now() - dt(item["at"])).total_seconds()
            if not -5 <= age <= max(300, cfg.poll_seconds * 5):
                raise ValueError("Monitoring or reconciliation is stale")
        review = read(experiment["id"], "position_review")
        limit = read(experiment["id"], "loss_limit")
        if limit and limit.get("latched"):
            raise ValueError("Cumulative loss limit requires operator attention")
        if review and review.get("issues"):
            raise ValueError("Held-position review requires operator attention")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()
    try:
        check_health(Settings.load(args.config))
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Unhealthy: {type(error).__name__}")
        return 1
    print("Healthy: recent monitoring and successful reconciliation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
