"""Dispatch an explicitly approved study; absence of approval never enables it."""

import hashlib
import json
import re
from datetime import timedelta
from decimal import DecimalException
from pathlib import Path
from zoneinfo import ZoneInfo

from .lifecycle import effective_end
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


def funding_gate(store, cfg, current, *, reservation_key=None):
    try:
        return _funding_gate(store, cfg, current, reservation_key=reservation_key)
    except (KeyError, TypeError, ValueError, AttributeError, DecimalException):
        return "malformed funding evidence or spending ledger"


def _funding_gate(store, cfg, current, *, reservation_key=None):
    funding = store.get("confirmed", "research_funding", scope="global")
    if not funding or funding.get("basis") != "provider_confirmed_available_credit":
        return "fresh provider credit evidence required"
    if not funding.get("evidence_sha256") or not funding.get("verified_by"):
        return "funding evidence identity missing"
    allocation = funding.get("allocation")
    if allocation is None and not timedelta(0) <= current - dt(funding["observed_at"]) <= timedelta(
        minutes=30
    ):
        return "funding evidence stale or future-dated"
    if dt(funding["expires_at"]) <= current + timedelta(seconds=cfg.research_max_seconds):
        return "funding expires before bounded run can finish"
    records = list(
        store.db.execute(
            "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' ORDER BY key"
        )
    )
    reserved = dec(0)
    if reservation_key is not None:
        record = json.loads(dict(records).get(reservation_key, "{}"))
        if (
            allocation is None
            or record.get("durable") is not True
            or record.get("experiment_id") != store.scope
            or record.get("config_sha256") != fingerprint(cfg)
            or record.get("status") != "reserved"
            or record.get("unknown_total") is not False
            or record.get("day") != current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        ):
            return "durable funding reservation is invalid or requires renewed authority"
        reserved = dec(record["usd"])
        if reserved != dec(record["durable_allowance"]) or not 0 < reserved <= cfg.research_max_cost_usd:
            return "durable funding reservation amount changed"
    if allocation is None and fingerprint(records) != funding["spending_sha256"]:
        return "spending changed since funding verification"
    allowed_status = {"settled", "reserved"} if allocation is not None else {"settled"}
    if any(
        json.loads(body).get("status") not in allowed_status or json.loads(body).get("unknown_total")
        for _, body in records
    ):
        return "unresolved model charge"
    available = dec(funding["available_usd"]) - dec(funding["buffer_usd"])
    if allocation is not None:
        identities = [
            funding.get("verified_by"),
            funding.get("account_id"),
            funding.get("project_id"),
            allocation.get("approved_by"),
            allocation.get("account_id"),
            allocation.get("project_id"),
        ]
        evidence = [
            funding.get("evidence_sha256"),
            allocation.get("funding_evidence_sha256"),
            allocation.get("exclusive_account_use_evidence_sha256"),
        ]
        if any(not isinstance(v, str) or not v.strip() for v in identities) or any(
            not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None for v in evidence
        ):
            return "allocation evidence or identity malformed"
        observed, approved = dt(funding["observed_at"]), dt(allocation["approved_at"])
        valid_until = dt(allocation["valid_until"])
        if not observed <= approved <= min(current, observed + timedelta(minutes=30)):
            return "allocation was not approved against fresh provider evidence"
        if (
            not current + timedelta(seconds=cfg.research_max_seconds)
            < valid_until
            <= dt(funding["expires_at"])
        ):
            return "allocation cannot fund the entire bounded run"
        if (
            allocation.get("status") != "operator_approved"
            or not allocation.get("approved_by")
            or not allocation.get("exclusive_account_use_evidence_sha256")
            or allocation.get("funding_evidence_sha256") != funding["evidence_sha256"]
            or not funding.get("account_id")
            or not funding.get("project_id")
            or allocation.get("account_id") != funding["account_id"]
            or allocation.get("project_id") != funding["project_id"]
        ):
            return "allocation identity or exclusive-account approval missing"
        baseline = allocation["spending_baseline"]
        if fingerprint(sorted(baseline.items())) != funding["spending_sha256"]:
            return "allocation baseline differs from provider verification"
        current_records = dict(records)
        if any(
            current_records.get(key) != body or json.loads(body).get("status") != "settled"
            for key, body in baseline.items()
        ):
            return "allocation baseline changed or was not settled"
        allocated = dec(allocation["usd"])
        if not 0 < allocated <= available:
            return "allocation exceeds confirmed credit after buffer"
        subsequent = dec(0)
        # shortcut: subsequent charges rely on preserved ledger history; attest append-only writes before adding external ledger writers.
        for key, body in records:
            amount = dec(json.loads(body)["usd"])
            if amount < 0:
                return "negative model spending requires reconciliation"
            if key not in baseline:
                subsequent += amount
        available = allocated - subsequent
    required = reserved if reservation_key is not None else cfg.research_max_cost_usd
    if available + reserved < required:
        return "confirmed credit cannot fund full capped run and buffer"
    if dec(funding["buffer_usd"]) < 10:
        return "funding buffer below reviewed minimum"
    spending = spending_status(store, cfg.research_daily_cost_usd, current)
    if spending["unknown_usage_runs"]:
        return "unresolved model charge"
    if spending["unresolved_testing_runs"]:
        return "explicit testing usage remains unresolved"
    daily_available = cfg.research_daily_cost_usd - spending["ordinary_charged_or_reserved_usd"] + reserved
    if daily_available < required:
        return "daily allowance cannot fund full capped run"
    return None


def dispatch(runner, study, clock, calendar_day):
    from .research_client import ResearchPending

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
    extension = store.get("approved", "experiment_extension")
    if extension and (
        extension["study_id"] != study["schedule"]["study_id"]
        or extension["schedule_sha256"] != study["schedule_sha256"]
    ):
        raise ValueError("Experiment extension belongs to a different study or schedule")
    schedule = study["schedule"]
    if study["schedule_sha256"] != fingerprint(schedule):
        raise ValueError("Approved schedule changed")
    if dt(effective_end(store, cfg)) < dt(schedule["expected_sessions"][-1]["close"]):
        raise ValueError("Existing experiment cannot contain the full study window")
    if set(schedule["research_universe"]) != set(cfg.research_universe) or set(
        schedule["execution_universe"]
    ) != set(cfg.universe):
        raise ValueError("Study universe differs from runtime")
    opportunities = schedule["opportunities"]
    if len(opportunities) != 36 or len({s["id"] for s in opportunities}) != 36:
        raise ValueError("Require all 36 distinct approved slots")
    if cfg.research_max_cost_usd > 6 or cfg.research_daily_cost_usd > 6:
        raise ValueError("Study exceeds approved run/day ceilings")
    pending_slots = [
        record
        for record in store.items("paper_slot_pending")
        if record.get("status") in {"starting", "pending"}
        and record.get("study_id") == schedule["study_id"]
        and record.get("schedule_sha256") == study["schedule_sha256"]
    ]
    if pending_slots:
        if len(pending_slots) != 1:
            raise ValueError("Multiple pending scheduled slots require operator review")
        pending = pending_slots[0]
        slot_id, payload = pending["slot"], pending["state"]
        claim = store.get(dumps([schedule["study_id"], slot_id]), "paper_slot")
        if (
            not claim
            or claim.get("symbol") != payload.get("symbol")
            or claim.get("schedule_sha256") != study["schedule_sha256"]
            or payload.get("run_key") != slot_id
        ):
            raise ValueError("Pending scheduled research differs from original slot claim")
        try:
            result = runner.graph.invoke(payload)
        except ResearchPending as wait:
            return {"status": "scheduled research pending", "slot": slot_id, "stage": wait.stage}
        except Exception:
            store.put(slot_id, {**pending, "status": "blocked", "at": now()}, "paper_slot_pending")
            store.log("paper_slot_failed", {"slot": slot_id, "study_id": schedule["study_id"], "at": now()})
            raise
        store.put(slot_id, {**pending, "status": "complete", "at": now()}, "paper_slot_pending")
        store.log("paper_slot_completed", {"slot": slot_id, "study_id": schedule["study_id"], "at": now()})
        return {
            "status": "scheduled research mechanically completed; independent review required",
            "result": result,
        }
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
    payload = {
        "symbol": slot["symbol"],
        "run_key": slot["id"],
        "fresh": True,
        "research_only": slot["symbol"] in cfg.research_only_universe,
        "trigger": "approved prospective scheduled investigation",
        "study_id": schedule["study_id"],
        "study_stratum": "scheduled",
    }
    durable = getattr(runner.graph, "research_client", None) is not None
    pending = (
        {
            "status": "starting",
            "slot": slot["id"],
            "study_id": schedule["study_id"],
            "schedule_sha256": study["schedule_sha256"],
            "state": payload,
            "at": current,
        }
        if durable
        else None
    )
    if not store.claim_paper_slot(
        schedule["study_id"],
        slot["id"],
        {"at": current, "symbol": slot["symbol"], "schedule_sha256": study["schedule_sha256"]},
        pending=pending,
    ):
        return {"status": "scheduled slot already consumed", "outcomes": outcomes}
    # A failure after this durable claim stays consumed, including a lost model response.
    try:
        result = runner.graph.invoke(payload)
        if durable:
            store.put(slot["id"], {**pending, "status": "complete", "at": now()}, "paper_slot_pending")
        store.log("paper_slot_completed", {"slot": slot["id"], "study_id": schedule["study_id"], "at": now()})
        return {
            "status": "scheduled research mechanically completed; independent review required",
            "result": result,
            "outcomes": outcomes,
        }
    except ResearchPending as wait:
        store.put(
            slot["id"],
            {
                "status": "pending",
                "slot": slot["id"],
                "study_id": schedule["study_id"],
                "schedule_sha256": study["schedule_sha256"],
                "state": payload,
                "at": now(),
            },
            "paper_slot_pending",
        )
        return {"status": "scheduled research pending", "slot": slot["id"], "stage": wait.stage}
    except Exception:
        if durable:
            store.put(slot["id"], {**pending, "status": "blocked", "at": now()}, "paper_slot_pending")
        store.log("paper_slot_failed", {"slot": slot["id"], "study_id": schedule["study_id"], "at": now()})
        raise
