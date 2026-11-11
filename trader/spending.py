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
    # Testing costs remain in gross reports and provider allocations, not ordinary daily admission.
    settled_testing = sum(
        (
            dec(r["usd"])
            for r in records
            if r.get("testing_budget_override") is True
            and r.get("unrestricted_research") is False
            and r.get("status") == "settled"
            and r.get("unknown_total") is False
        ),
        dec(0),
    )
    ordinary_used = used - settled_testing
    testing_records = [
        json.loads(row[0])
        for row in store.db.execute(
            "SELECT body FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_type(body, '$.testing_budget_override')='true'"
        )
    ]
    unresolved_testing = sum(
        r.get("status") != "settled" or r.get("unknown_total") is not False for r in testing_records
    )
    unknown_usage = store.db.execute(
        "SELECT count(*) FROM documents WHERE scope='global' AND kind='research_spend' "
        "AND (coalesce(json_extract(body, '$.status'), 'unknown') NOT IN ('settled','reserved') "
        "OR coalesce(json_extract(body, '$.unknown_total'), 0)!=0)"
    ).fetchone()[0]
    # Midnight resets the allowance, not an uncapped liability from an earlier run.
    unbounded = store.db.execute(
        "SELECT count(*) FROM documents WHERE scope='global' AND kind='research_spend' "
        "AND json_extract(body, '$.unrestricted_research')=1 "
        "AND coalesce(json_extract(body, '$.status'), 'unknown')!='settled'"
    ).fetchone()[0]
    return {
        "day": day,
        "timezone": "America/New_York",
        "limit_usd": limit,
        "charged_or_reserved_usd": used,
        "remaining_usd": max(dec(0), limit - used),
        "settled_testing_usd": settled_testing,
        "ordinary_charged_or_reserved_usd": ordinary_used,
        "ordinary_remaining_usd": max(dec(0), limit - ordinary_used),
        "unresolved_testing_runs": unresolved_testing,
        "unknown_usage_runs": unknown_usage,
        "unresolved_runs": sum(r["status"] != "settled" for r in records),
        "unbounded_unknown_runs": unbounded,
    }


def research_cost_summary(store):
    """Run-ledger amounts, including evaluations, without re-adding per-call journal costs."""
    import json

    records = [
        json.loads(row[0])
        for row in store.db.execute(
            "SELECT body FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_extract(body, '$.experiment_id')=?",
            (store.scope,),
        )
    ]
    summary = {
        "provider_reconciled_usd": dec(0),
        "estimated_usd": dec(0),
        "unresolved_recorded_usd": dec(0),
        "settled_runs": 0,
        "unresolved_runs": 0,
        "unbounded_unknown_runs": 0,
    }
    for record in records:
        if record.get("status") == "settled":
            summary["settled_runs"] += 1
            key = (
                "provider_reconciled_usd"
                if record.get("cost_basis") == "provider_export_reconciled"
                else "estimated_usd"
            )
        else:
            summary["unresolved_runs"] += 1
            summary["unbounded_unknown_runs"] += bool(record.get("unrestricted_research"))
            key = "unresolved_recorded_usd"
        summary[key] += dec(record["usd"])
    return summary


class DailyReservation:
    @classmethod
    def for_durable_run(cls, store, cfg, run_key):
        return cls(store, cfg, run_key, durable=True)

    def __init__(self, store, cfg, run_key, *, unrestricted_research=False, testing=False, durable=False):
        self.store, self.key = store, str(uuid4())
        # Reserve before any API call; a crash keeps the full allowance charged to this day.
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            if durable:
                from .paper_dispatch import fingerprint, funding_gate

                existing = store.db.execute(
                    "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' "
                    "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
                    (run_key, store.scope),
                ).fetchall()
                if existing:
                    if len(existing) != 1:
                        raise DailyBudgetExceeded("Durable run has conflicting spending records")
                    import json

                    key, body = existing[0]
                    record = json.loads(body)
                    if (
                        not record.get("durable")
                        or record.get("config_sha256") != fingerprint(cfg)
                        or record.get("status") != "reserved"
                        or record.get("unknown_total")
                    ):
                        raise DailyBudgetExceeded(
                            "Durable reservation needs reconciliation or is already settled"
                        )
                    self.key, self.record = key, record
                    self.allowance = dec(record["durable_allowance"])
                    self.unrestricted_research = False
                    status = spending_status(store, cfg.research_daily_cost_usd)
                    if record["day"] != status["day"]:
                        raise DailyBudgetExceeded("Durable run cannot renew its daily authority on resume")
                    if any(
                        status[name]
                        for name in (
                            "unknown_usage_runs",
                            "unresolved_testing_runs",
                            "unbounded_unknown_runs",
                        )
                    ):
                        raise DailyBudgetExceeded("Outstanding model charges require reconciliation")
                    reason = funding_gate(store, cfg, now(), reservation_key=key)
                    if reason:
                        raise DailyBudgetExceeded(reason)
                    return
                reason = funding_gate(store, cfg, now())
                if reason:
                    raise DailyBudgetExceeded(reason)
            if testing and (unrestricted_research or store.get("active", "paper_study") is not None):
                raise DailyBudgetExceeded("Testing overrides require bounded runs outside an active study")
            if (
                testing
                and store.db.execute(
                    "SELECT 1 FROM documents WHERE scope='global' AND kind='research_spend' "
                    "AND coalesce(json_extract(body, '$.status'), 'unknown')!='settled' LIMIT 1"
                ).fetchone()
            ):
                raise DailyBudgetExceeded("Settle outstanding model charges before another testing run")
            if store.get("active", "paper_study") is not None:
                from .paper_dispatch import funding_gate

                reason = funding_gate(store, cfg, now())
                if unrestricted_research or reason:
                    raise DailyBudgetExceeded(reason or "Unbounded research is forbidden during a study")
            status = spending_status(store, cfg.research_daily_cost_usd)
            self.unrestricted_research = unrestricted_research
            self.allowance = (
                None
                if unrestricted_research
                else cfg.research_max_cost_usd
                if testing
                else min(cfg.research_max_cost_usd, status["ordinary_remaining_usd"])
            )
            if status["unknown_usage_runs"]:
                raise DailyBudgetExceeded(
                    "Unknown model usage remains unresolved; settle outstanding model charges"
                )
            if status["unresolved_testing_runs"]:
                raise DailyBudgetExceeded(
                    "Explicit testing usage remains unresolved; settle outstanding model charges"
                )
            if not unrestricted_research and status["unbounded_unknown_runs"]:
                raise DailyBudgetExceeded(
                    "Unbounded model usage remains unresolved; reconcile provider charges"
                )
            if not unrestricted_research and self.allowance <= 0:
                raise DailyBudgetExceeded("Daily model budget exhausted; monitoring/reconciliation continue")
            self.record = {
                "run_key": run_key,
                "experiment_id": store.scope,
                "day": status["day"],
                "started_at": now(),
                "usd": dec(0) if unrestricted_research else self.allowance,
                "unrestricted_research": unrestricted_research,
                "testing_budget_override": testing,
                "normal_daily_limit_usd": cfg.research_daily_cost_usd,
                "unknown_total": unrestricted_research,
                "status": "reserved",
            }
            if durable:
                self.record.update(
                    durable=True, durable_allowance=self.allowance, config_sha256=fingerprint(cfg)
                )
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


def spend_breakdown(store, model_usage):
    rows = {}

    def record(source, amount):
        row = rows.setdefault(
            source, {"source": source, "known_usd": dec(0), "requests": 0, "unknown_requests": 0}
        )
        row["requests"] += 1
        if amount is None:
            row["unknown_requests"] += 1
        else:
            row["known_usd"] += dec(amount)

    for event in model_usage:
        record("OpenAI · " + event.get("model", "unspecified model"), event.get("estimated_usd"))
    x_results = {e["body"]["operation_id"]: e["body"] for e in store.events("x_result")}
    for event in store.events("x_attempt"):
        result = x_results.get(event["body"]["operation_id"], {})
        record("X API", result.get("cost_usd"))
    for _ in store.events("flovia_x_attempt"):
        record("Flovia (legacy)", None)
    for event in store.events("data_cost"):
        body = event["body"]
        record(body.get("source") or body.get("provider") or "Other data", body.get("usd"))
    for row in rows.values():
        if row["unknown_requests"] == row["requests"]:
            row["known_usd"] = None
    return sorted(rows.values(), key=lambda row: (-(row["known_usd"] or dec(0)), row["source"]))
