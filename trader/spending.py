"""Durable estimated model-spend reservations, shared across runs and experiments."""

from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import dec, dumps, now


class DailyBudgetExceeded(RuntimeError):
    pass


def spending_status(store, limit, at=None):
    day = (at or now()).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    rows = store.db.execute(
        "SELECT body FROM documents WHERE scope='global' AND kind='research_spend' "
        "AND json_extract(body, '$.day')=?",
        (day,),
    ).fetchall()
    import json

    records = [json.loads(row[0]) for row in rows]
    used = sum((dec(r["usd"]) for r in records), dec(0))
    return {
        "day": day,
        "timezone": "America/New_York",
        "limit_usd": limit,
        "charged_or_reserved_usd": used,
        "remaining_usd": max(dec(0), limit - used),
        "unresolved_runs": sum(r["status"] != "settled" for r in records),
        "unbounded_unknown_runs": sum(
            r.get("unrestricted_research", False) and r["status"] != "settled" for r in records
        ),
    }


class DailyReservation:
    def __init__(self, store, cfg, run_key, *, unrestricted_research=False):
        self.store, self.key = store, str(uuid4())
        # Reserve before any API call; a crash keeps the full allowance charged to this day.
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            status = spending_status(store, cfg.research_daily_cost_usd)
            self.unrestricted_research = unrestricted_research
            self.allowance = (
                None if unrestricted_research else min(cfg.research_max_cost_usd, status["remaining_usd"])
            )
            if not unrestricted_research and (self.allowance <= 0 or status["unbounded_unknown_runs"]):
                raise DailyBudgetExceeded("Daily model budget exhausted; monitoring/reconciliation continue")
            self.record = {
                "run_key": run_key,
                "experiment_id": store.scope,
                "day": status["day"],
                "started_at": now(),
                "usd": dec(0) if unrestricted_research else self.allowance,
                "unrestricted_research": unrestricted_research,
                "unknown_total": unrestricted_research,
                "status": "reserved",
            }
            store.db.execute(
                "INSERT INTO documents(scope,kind,key,body) VALUES ('global','research_spend',?,?)",
                (self.key, dumps(self.record)),
            )

    def finish(self, estimated_cost, unknown=False):
        self.record.update(
            usd=max(self.allowance, estimated_cost)
            if unknown and not self.unrestricted_research
            else estimated_cost,
            unknown_total=unknown,
            status="unknown" if unknown else "settled",
            finished_at=now(),
        )
        self.store.put(self.key, self.record, "research_spend", scope="global")
