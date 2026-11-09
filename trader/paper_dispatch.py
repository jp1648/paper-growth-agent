"""Dispatch an explicitly approved study; absence of approval never enables it."""

import hashlib
from datetime import timedelta
from pathlib import Path

from .models import dec, dt, dumps, now
from .spending import spending_status


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def source_fingerprint():
    return fingerprint(
        {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))
        }
    )


def funding_gate(store, cfg, current):
    funding = store.get("confirmed", "research_funding", scope="global")
    if not funding or funding.get("basis") != "provider_confirmed_available_credit":
        return "fresh provider credit evidence required"
    if not funding.get("evidence_sha256") or not funding.get("verified_by"):
        return "funding evidence identity missing"
    if not timedelta(0) <= current - dt(funding["observed_at"]) <= timedelta(minutes=30):
        return "funding evidence stale or future-dated"
    if dt(funding["expires_at"]) <= current + timedelta(seconds=cfg.research_max_seconds):
        return "funding expires before bounded run can finish"
    records = list(
        store.db.execute(
            "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' ORDER BY key"
        )
    )
    if fingerprint(records) != funding["spending_sha256"]:
        return "spending changed since funding verification"
    import json

    if any(json.loads(body).get("status") != "settled" for _, body in records):
        return "unresolved model charge"
    if dec(funding["available_usd"]) - dec(funding["buffer_usd"]) < cfg.research_max_cost_usd:
        return "confirmed credit cannot fund full capped run and buffer"
    if dec(funding["buffer_usd"]) < 10:
        return "funding buffer below reviewed minimum"
    if (
        spending_status(store, cfg.research_daily_cost_usd, current)["remaining_usd"]
        < cfg.research_max_cost_usd
    ):
        return "daily allowance cannot fund full capped run"
    return None


def dispatch(runner, study, clock, calendar_day):
    service, store, cfg = runner.service, runner.store, runner.cfg
    current = now()
    if (
        study.get("status") != "operator_approved"
        or study.get("experiment_id") != store.scope
        or study.get("config_sha256") != fingerprint(cfg.model_dump(mode="json"))
        or study.get("source_sha256") != source_fingerprint()
        or not study.get("approved_by")
        or not study.get("acceptance_evidence_sha256")
        or dt(study["approved_at"]) > current
        or dt(study["approved_at"]) >= dt(study["schedule"]["expected_sessions"][0]["open"])
    ):
        raise ValueError("Study approval or frozen runtime binding invalid")
    schedule = study["schedule"]
    if study["schedule_sha256"] != fingerprint(schedule):
        raise ValueError("Approved schedule changed")
    if dt(store.experiment["ends_at"]) < dt(schedule["expected_sessions"][-1]["close"]):
        raise ValueError("Existing experiment cannot contain the full study window")
    if set(schedule["research_universe"]) != set(cfg.research_universe) or set(
        schedule["execution_universe"]
    ) != set(cfg.universe):
        raise ValueError("Study universe differs from runtime")
    opportunities = schedule["opportunities"]
    if len(opportunities) != 36 or len({s["id"] for s in opportunities}) != 36:
        raise ValueError("Require all 36 distinct approved slots")
    if cfg.research_max_cost_usd > 5 or cfg.research_daily_cost_usd > 6:
        raise ValueError("Study exceeds approved run/day ceilings")
    if current < dt(schedule["expected_sessions"][0]["open"]):
        return {"status": "study has not started"}
    if not timedelta(0) <= current - dt(clock["timestamp"]) <= timedelta(seconds=30):
        return {"status": "study blocked: broker clock stale or future"}
    outcomes = []

    def blocked(reason):
        for item in opportunities:
            if dt(item["claim_from"]) <= now() < dt(item["claim_before"]):
                previous = store.get(item["id"], "paper_slot_deferral")
                if not previous or previous["reason"] != reason:
                    receipt = {
                        "slot": item["id"],
                        "study_id": schedule["study_id"],
                        "reason": reason,
                        "at": now(),
                    }
                    store.put(item["id"], receipt, "paper_slot_deferral")
                    store.log("paper_slot_deferred", receipt)
        return {"status": reason, "outcomes": outcomes}

    for slot in opportunities:
        if current >= dt(slot["claim_before"]) and store.claim_paper_slot(
            schedule["study_id"],
            slot["id"],
            {
                "at": current,
                "symbol": slot["symbol"],
                "outcome": "missed",
                "last_deferral": store.get(slot["id"], "paper_slot_deferral"),
                "reason": "paused at observation"
                if service.kill_path.exists()
                else "claiming window elapsed",
            },
        ):
            outcomes.append({"slot": slot["id"], "status": "missed"})
    if current > dt(schedule["expected_sessions"][-1]["close"]):
        return {"status": "study observation window ended", "outcomes": outcomes}
    if service.kill_path.exists():
        return blocked("study paused; no research")
    if not clock["is_open"] or not calendar_day:
        return {"status": "study outside regular session", "outcomes": outcomes}
    selected = [s for s in opportunities if dt(s["claim_from"]) <= current < dt(s["claim_before"])]
    if len(selected) > 1:
        raise ValueError("Overlapping study slots")
    if selected:
        slot = selected[0]
        if slot["date"] != calendar_day["date"] or any(
            dt(slot[field]).strftime("%H:%M") != calendar_day[field][:5] for field in ("open", "close")
        ):
            return {"status": "study calendar changed; review required", "outcomes": outcomes}
    gate = funding_gate(store, cfg, current)
    if gate:
        return blocked(gate)
    # Held-position reviews retain priority and their own run keys and cost records.
    held = {p["symbol"] for p in store.get("snapshot")["positions"]}
    if held:
        safety = runner.research_batch(
            f"{schedule['study_id']}:safety:{int(current.timestamp()) // (cfg.trigger_minutes * 60)}",
            "intraday",
            symbols=held,
            study_id=schedule["study_id"],
        )
        outcomes.append({"safety": safety})
    if not selected:
        return {"status": "study monitoring; no scheduled slot", "outcomes": outcomes}
    slot = selected[0]
    current = now()
    if current >= dt(slot["claim_before"]) or service.kill_path.exists():
        receipt = {
            "slot": slot["id"],
            "study_id": schedule["study_id"],
            "reason": "safety review displaced scheduled window",
            "at": current,
        }
        store.put(slot["id"], receipt, "paper_slot_deferral")
        store.log("paper_slot_deferred", receipt)
        return {"status": "scheduled window closed or paused after safety review", "outcomes": outcomes}
    if service.position_review_issues(service.reconcile().positions, current):
        return blocked("scheduled research deferred for held-position safety")
    gate = funding_gate(store, cfg, current)
    if gate:
        return blocked(gate)
    clock = service.broker.clock()
    current = now()
    if not clock["is_open"] or not timedelta(0) <= current - dt(clock["timestamp"]) <= timedelta(seconds=30):
        return {"status": "study clock invalid before claim", "outcomes": outcomes}
    if not dt(slot["claim_from"]) <= current < dt(slot["claim_before"]):
        return {"status": "scheduled window closed before claim", "outcomes": outcomes}
    gate = funding_gate(store, cfg, current)
    if gate:
        return blocked(gate)
    key = dumps([schedule["study_id"], slot["id"]])
    if store.get(key, "paper_slot") is None and any(
        store.get(slot["id"], kind) is not None
        for kind in ("research_result", "graph_decision", "research_committed", "research_audit")
    ):
        raise ValueError("Scheduled run key already has research artifacts")
    if not store.claim_paper_slot(
        schedule["study_id"],
        slot["id"],
        {"at": current, "symbol": slot["symbol"], "schedule_sha256": study["schedule_sha256"]},
    ):
        return {"status": "scheduled slot already consumed", "outcomes": outcomes}
    # A failure after this durable claim stays consumed, including a lost model response.
    try:
        result = runner.graph.invoke(
            {
                "symbol": slot["symbol"],
                "run_key": slot["id"],
                "fresh": True,
                "research_only": slot["symbol"] in cfg.research_only_universe,
                "trigger": "approved prospective scheduled investigation",
                "study_id": schedule["study_id"],
                "study_stratum": "scheduled",
            }
        )
        store.log("paper_slot_completed", {"slot": slot["id"], "study_id": schedule["study_id"], "at": now()})
        return {
            "status": "scheduled research mechanically completed; independent review required",
            "result": result,
            "outcomes": outcomes,
        }
    except Exception:
        store.log("paper_slot_failed", {"slot": slot["id"], "study_id": schedule["study_id"], "at": now()})
        raise
