from __future__ import annotations

import fcntl
from datetime import datetime, timedelta
from decimal import InvalidOperation
from zoneinfo import ZoneInfo

from .alpaca import safe_error
from .models import dec, dt, news_time, now
from .observability import traced
from .reports import daily_report, portfolio_report
from .spending import spending_status


class Runner:
    def __init__(self, service, graph):
        self.service, self.graph = service, graph
        self.store, self.cfg = service.store, service.cfg

    def due(self, symbol, phase):
        thesis = self.store.get(symbol, "thesis")
        if phase in {"preopen", "weekly"} or not thesis:
            return True, phase
        reviewed = dt(thesis["reviewed_at"])
        if reviewed > now():
            return True, "future-dated thesis requires fresh review"
        if now() - reviewed < timedelta(minutes=self.cfg.event_debounce_minutes):
            return False, "debounced"
        decision = thesis["decision"]
        execution = thesis.get("execution", {})
        if execution.get("status") == "rejected" and any(
            "Market closed" in r for r in execution.get("reasons", [])
        ):
            return True, "preopen candidate needs fresh entry evaluation"
        if dt(decision["next_review"]) <= now():
            return True, "review deadline"
        news = self.service.data.news(symbol, reviewed.isoformat())
        # New external evidence can warrant research; price by itself only checks saved thresholds.
        if any(reviewed < news_time(a) <= now() for a in news):
            return True, "new news evidence"
        quote = self.service.data.quote(symbol)
        if (
            quote.delayed
            or not -5 <= (now() - quote.timestamp).total_seconds() <= self.cfg.quote_max_age_seconds
        ):
            return False, "stale or future-dated quote"
        move = abs(quote.ask / dec(thesis["reference_price"]) - 1)
        if move >= self.cfg.price_trigger:
            return True, "configured price-move threshold crossed"
        for value in (thesis["valuation"].get("base_value"), thesis["valuation"].get("bear_value")):
            try:
                target = dec(value)
            except (InvalidOperation, ValueError, TypeError):
                self.store.log("valuation_threshold_unavailable", {"symbol": symbol, "value": value})
                continue
            old = dec(thesis["reference_price"])
            if (old < target <= quote.ask) or (quote.ask <= target < old):
                return True, "valuation threshold crossed"
        snap = self.store.get("snapshot")
        for p in snap["positions"]:
            if (
                p["symbol"] == symbol
                and dec(p["market_value"]) > dec(snap["account"]["equity"]) * self.cfg.max_position
            ):
                return True, "entry concentration breached; reassess additions"
        return False, "no material trigger"

    @traced("runner.tick")
    def tick(self, event=False):
        service = self.service
        service.require_experiment()
        path = self.store.control_path(self.cfg.control_dir, service.account_id, ".run.lock")
        with path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "another finite run is active"}
            return self._tick(event)

    @traced("runner.maintain")
    def maintain(self):
        self.service.require_experiment()
        previous = self.store.get("heartbeat")
        current = now()
        if previous and current - dt(previous["at"]) > timedelta(seconds=self.cfg.poll_seconds * 3):
            self.store.log(
                "interruption",
                {
                    "last_seen": previous["at"],
                    "resumed_at": current,
                    "note": "No monitoring or trades are claimed for this gap",
                },
            )
        self.store.put("heartbeat", {"at": current})
        previous_limit = self.store.get("loss_limit") or {}
        already_blocked = previous_limit.get("latched") or not previous_limit.get("daily", {}).get(
            "valid", True
        )
        self.service.cancel_expired()
        snapshot = self.service.reconcile()
        limit = self.store.get("loss_limit") or {}
        if not already_blocked and (limit.get("latched") or not limit.get("daily", {}).get("valid", True)):
            self.service.cancel_expired()
            snapshot = self.service.reconcile()
        issues = self.service.position_review_issues(snapshot.positions, now())
        previous_review = self.store.get("position_review")
        if previous_review is None or previous_review.get("issues") != issues:
            self.store.log("position_review_changed", {"issues": issues})
        self.store.put("position_review", {"at": now(), "issues": issues})
        self.store.backup()
        try:
            from .notifications import notify_fills

            notify_fills(self.store)
        except Exception as error:  # noqa: BLE001 - notification failure must not stop monitoring
            self.store.log("telegram_notification_failed", {"error_type": type(error).__name__})
        # Dashboard export is optional and cannot interrupt cancellation/reconciliation.
        try:
            from .dashboard import publish_snapshot

            publish_snapshot(self.service)
        except Exception as error:  # noqa: BLE001 - telemetry must not change trading behavior
            self.store.log("dashboard_export_failed", {"error": safe_error(error)})
        return {"status": "reconciled", "at": now()}

    def _tick(self, event):
        self.maintain()
        current = now()
        clock = self.service.broker.clock()
        local = current.astimezone(ZoneInfo("America/New_York"))
        date = local.date().isoformat()
        days = self.service.broker.calendar(date, date)
        if not days:
            return {"status": "non-trading day; reconciled"}
        day = days[0]
        opening = datetime.fromisoformat(date + "T" + day["open"]).replace(tzinfo=local.tzinfo)
        closing = datetime.fromisoformat(date + "T" + day["close"]).replace(tzinfo=local.tzinfo)
        phase = None
        if opening - timedelta(minutes=self.cfg.preopen_minutes) <= local < opening:
            phase = "preopen"
        elif clock["is_open"] and opening <= local < closing:
            phase = "intraday"
        elif local >= closing + timedelta(minutes=16):
            phase = "postclose"
        if current >= dt(self.store.experiment["ends_at"]):
            for order in self.store.active_orders():
                self.service.request_cancel(order)
            if phase == "postclose":
                return daily_report(self.service)
            return {
                "status": "experiment ended; positions remain open",
                "report": portfolio_report(self.service),
            }
        if not phase:
            return {"status": "reconciled; outside research cadence"}
        if self.service.kill_path.exists() and phase != "postclose":
            return {"status": "paused; reconciliation continues"}
        slot = f"{date}:{phase}"
        if phase == "intraday":
            slot += ":" + str(int(current.timestamp()) // (self.cfg.trigger_minutes * 60))
        job = self.store.get(slot, "job")
        if job and job["status"] == "complete" and not (event and phase == "intraday"):
            return {"status": "cadence already handled"}
        if phase == "postclose":
            report = daily_report(self.service)
            if local.weekday() == 4 and not self.service.kill_path.exists():
                self.research_batch(date + ":weekly", "weekly")
                self.store.log(
                    "weekly_strategy_review",
                    {
                        "report": report,
                        "note": "Review behavior separately from returns; no automatic parameter optimization",
                    },
                )
            self.store.put(slot, {"status": "complete", "at": now()}, "job")
            return report
        result = self.research_batch(slot, phase)
        self.store.put(slot, {"status": "complete", "at": now()}, "job")
        return result

    @traced("runner.research_batch")
    def research_batch(self, slot, phase):
        snap = self.store.get("snapshot")
        symbols = set(self.cfg.universe) | {p["symbol"] for p in snap["positions"]}
        outcomes = []
        held = {p["symbol"] for p in snap["positions"]}

        def priority(symbol):
            attempted = self.store.get(symbol, "research_attempt") or {}
            return symbol not in held, attempted.get("at", ""), symbol

        for symbol in sorted(symbols, key=priority):
            key = slot + ":" + symbol
            job = self.store.get(key, "job")
            if job and job["status"] == "complete":
                continue
            try:
                thesis = self.store.get(symbol, "thesis")
                pending = (
                    phase == "intraday"
                    and thesis
                    and thesis.get("execution", {}).get("status") == "deferred"
                    and now() - dt(thesis["reviewed_at"]) < timedelta(hours=self.cfg.research_cache_hours)
                    and dt(thesis["decision"]["next_review"]) > now()
                    and not any(
                        news_time(a) > dt(thesis["reviewed_at"])
                        for a in self.service.data.news(symbol, thesis["reviewed_at"])
                    )
                )
                run_key = thesis["run_key"] if pending else key
                cached = self.store.get(run_key, "research_result")
                if (
                    not cached
                    and spending_status(self.store, self.cfg.research_daily_cost_usd)["remaining_usd"] <= 0
                ):
                    outcomes.append({"symbol": symbol, "status": "daily model budget exhausted"})
                    continue
                if (
                    job
                    and job["status"] == "failed"
                    and now() - dt(job["at"]) < timedelta(minutes=self.cfg.event_debounce_minutes)
                ):
                    outcomes.append({"symbol": symbol, "status": "failed research cooldown"})
                    continue
                due, reason = self.due(symbol, phase)
                if pending:
                    due, reason = True, "evaluate deferred decision with fresh execution data"
                if not due and not cached:
                    outcomes.append({"symbol": symbol, "status": reason})
                    continue
                if job and job["status"] == "running":
                    self.store.log(
                        "interruption", {"run_key": key, "note": "Recovering interrupted finite graph"}
                    )
                self.store.put(key, {"status": "running", "at": now()}, "job")
                if not cached:
                    self.store.put(symbol, {"at": now()}, "research_attempt")
                state = self.graph.invoke(
                    {
                        "symbol": symbol,
                        "run_key": run_key,
                        "research_only": phase in {"preopen", "weekly"},
                        "trigger": reason,
                        "fresh": phase in {"preopen", "weekly"} or reason == "new news evidence",
                    }
                )
                self.store.put(key, {"status": "complete", "at": now()}, "job")
                d = state["decision"]
                outcomes.append(
                    {
                        "symbol": symbol,
                        "action": d["action"],
                        "confidence": d["confidence"],
                        "execution": state["execution"]["status"],
                        "trigger": reason,
                    }
                )
            except Exception as error:  # noqa: BLE001 - persist failure at the execution boundary
                self.store.put(key, {"status": "failed", "at": now()}, "job")
                self.store.log("error", {"run_key": key, "error": safe_error(error)})
                outcomes.append({"symbol": symbol, "error": safe_error(error)})
        # Ranking is an explicit qualitative model score, not a promise of expected return.
        outcomes.sort(key=lambda x: dec(x.get("confidence", "0")), reverse=True)
        self.store.log("opportunity_ranking", {"slot": slot, "outcomes": outcomes})
        return {"slot": slot, "outcomes": outcomes}
