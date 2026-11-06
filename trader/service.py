from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import uuid4

import httpx

from .accounting import LedgerError, replay, reserved
from .alpaca import safe_error
from .models import TERMINAL, ZERO, Decision, dec, dt, dumps, now
from .observability import traced
from .risk import check


class Service:
    def __init__(self, settings, store, broker, data):
        self.cfg, self.store, self.broker, self.data = settings, store, broker, data
        self.account_id = store.experiment["account_id"] if store.experiment else broker.account()["id"]
        self.kill_path = store.control_path(settings.control_dir, self.account_id, ".paused")

    def lock(self):
        return self.store.serialized(self.cfg.control_dir, self.account_id)

    @traced("service.initialize")
    def initialize(self, new_experiment=False):
        if new_experiment:
            self.account_id = self.broker.account()["id"]
            self.kill_path = self.store.control_path(self.cfg.control_dir, self.account_id, ".paused")
        with self.lock():
            registry = self.store.control_path(self.cfg.control_dir, self.account_id, ".ledger")
            if registry.exists() and registry.read_text() != str(self.store.path):
                raise RuntimeError(
                    "This account is already bound to another ledger. Reuse it to preserve history."
                )
            if self.store.experiment and not new_experiment:
                raise RuntimeError(
                    "Experiment already exists; use explicit --new-experiment after preparing a fresh paper account"
                )
            account = self.broker.account()
            stamp = now()
            snap = self.broker.snapshot((stamp - timedelta(days=1)).isoformat(), [])
            if account["id"] != self.account_id or snap.account["id"] != self.account_id:
                raise RuntimeError("Paper account identity changed")
            if snap.positions or snap.orders or dec(snap.account["cash"]) != self.cfg.initial_capital:
                raise RuntimeError(
                    "Initialize requires an empty dedicated paper account with configured initial cash"
                )
            if dec(snap.account["equity"]) != self.cfg.initial_capital:
                raise RuntimeError("Initial equity must equal configured capital")
            experiment = {
                "id": str(uuid4()),
                "account_id": self.account_id,
                "initial_capital": str(self.cfg.initial_capital),
                "started_at": stamp.isoformat(),
                "ends_at": (stamp + timedelta(weeks=self.cfg.experiment_weeks)).isoformat(),
                "settings": self.cfg.model_dump(mode="json"),
            }
            self.store.experiment = experiment
            self.store.put("active", experiment, scope="global")
            self.store.put(experiment["id"], experiment, "experiment", scope="global")
            registry.write_text(str(self.store.path))
            self.store.log("experiment_initialized", experiment)
            return self.reconcile()

    def require_experiment(self):
        if not self.store.experiment:
            raise RuntimeError("Run init first")
        active = self.store.get("active", scope="global")
        if not active or active["id"] != self.store.scope:
            raise RuntimeError("Active experiment changed; restart this process")
        registry = self.store.control_path(self.cfg.control_dir, self.account_id, ".ledger")
        if not registry.exists() or registry.read_text() != str(self.store.path):
            raise RuntimeError("Ledger binding missing or different; refusing execution")

    def pause_reconciliation(self, reason):
        self.store.put("reconciliation", {"ok": False, "at": now(), "reason": reason})
        self.store.log("reconciliation_failed", {"reason": reason})

    def validate_order(self, order, intent):
        decision = Decision.model_validate(intent["decision"])
        if (
            order["client_order_id"] != intent["client_order_id"]
            or order["symbol"] != decision.symbol
            or order["side"] != decision.side
            or dec(order["qty"]) != decision.quantity
            or not 0 <= dec(order["filled_qty"]) <= decision.quantity
            or order.get("type") != "limit"
            or dec(order.get("limit_price")) != decision.limit_price
            or order.get("extended_hours") is not False
            or order.get("time_in_force")
            != intent.get(
                "time_in_force",
                intent.get("risk_context", {})
                .get("settings", {})
                .get("time_in_force", self.cfg.time_in_force),
            )
            or (intent.get("broker_order_id") and intent["broker_order_id"] != order["id"])
            or order.get("replaced_by")
        ):
            raise LedgerError("Broker order differs from approved intent; operator review required")

    @traced("service.reconcile")
    def reconcile(self):
        with self.lock():
            self.require_experiment()
            try:
                tracked = [
                    i["client_order_id"]
                    for i in self.store.items("intent")
                    if i["status"] in {"prepared", "unknown", "submitted"}
                ]
                tracked += [o["client_order_id"] for o in self.store.active_orders()]
                snap = self.broker.snapshot(self.store.experiment["started_at"], sorted(set(tracked)))
                if snap.account["id"] != self.account_id:
                    raise LedgerError("Broker account identity does not match experiment")
                known = {o["client_order_id"]: o for o in snap.orders}
                for order in snap.orders:
                    intent = self.store.get(order["client_order_id"], "intent")
                    if intent:
                        self.validate_order(order, intent)
                    self.store.order(order)
                effective = {a["id"]: a for a in self.store.activities()}
                for a in snap.activities:
                    prior = effective.get(a["id"])
                    if prior and prior != a:
                        raise LedgerError(f"Broker revised activity {a['id']}; audited correction required")
                    if not prior:
                        self.store.put(a["id"], a, "activity")
                        self.store.log("broker_activity", a)
                for intent in self.store.items("intent"):
                    order = known.get(intent["client_order_id"])
                    if order:
                        intent["status"] = "complete" if order["status"] in TERMINAL else "submitted"
                        intent["broker_order_id"] = order["id"]
                        self.store.put(intent["client_order_id"], intent, "intent")
                        last = self.store.get(order["symbol"], "last_trade")
                        stamp = order.get("filled_at") or order.get("submitted_at") or order["created_at"]
                        if not last or dt(stamp) > dt(last["at"]):
                            self.store.put(
                                order["symbol"],
                                {"at": stamp, "client_order_id": order["client_order_id"]},
                                "last_trade",
                            )
                    elif intent["status"] in {"prepared", "unknown", "submitted"}:
                        raise LedgerError(
                            f"Unresolved submission {intent['client_order_id']}; lookup returned no order, no blind retry"
                        )
                for order in self.store.active_orders():
                    if order["client_order_id"] not in known:
                        raise LedgerError(f"Tracked order missing at broker: {order['client_order_id']}")
                    if (
                        order.get("type") != "limit"
                        or not order.get("limit_price")
                        or order.get("extended_hours")
                    ):
                        raise LedgerError("Unsupported external order in dedicated account")
                    if not self.store.get(order["client_order_id"], "intent"):
                        raise LedgerError("External order in dedicated account; cancel and investigate")
                adjustments = {x["id"]: x for x in self.store.items("adjustment")}
                ledger = replay(
                    self.store.experiment["initial_capital"], self.store.activities(), adjustments
                )
                if abs(ledger["cash"] - dec(snap.account["cash"])) > dec("0.02"):
                    raise LedgerError(
                        f"Cash discrepancy: audited {ledger['cash']} vs broker {snap.account['cash']}"
                    )
                actual = {p["symbol"]: dec(p["qty"]) for p in snap.positions}
                expected = {s: q for s, q in ledger["shares"].items() if q != 0}
                if actual != expected:
                    raise LedgerError(
                        "Position quantity discrepancy; executions/corporate actions may still be arriving"
                    )
                if any(q < 0 for q in actual.values()) or dec(snap.account["cash"]) < 0:
                    raise LedgerError("Short position or negative cash detected")
                fill_by_order = {}
                approved_orders = {
                    o["id"]: o
                    for o in self.store.items("order")
                    if self.store.get(o["client_order_id"], "intent")
                }
                for a in ledger["fills"]:
                    approved = approved_orders.get(a["order_id"])
                    if not approved or approved["symbol"] != a["symbol"] or approved["side"] != a["side"]:
                        raise LedgerError(
                            "Execution has no matching approved order; investigate external trading"
                        )
                    fill_by_order[a["order_id"]] = fill_by_order.get(a["order_id"], ZERO) + dec(a["qty"])
                    last = self.store.get(a["symbol"], "last_trade")
                    if not last or dt(a["transaction_time"]) > dt(last["at"]):
                        self.store.put(
                            a["symbol"],
                            {"at": a["transaction_time"], "broker_order_id": a["order_id"]},
                            "last_trade",
                        )
                for o in self.store.items("order"):
                    if dec(o["filled_qty"]) != fill_by_order.get(o["id"], ZERO):
                        refreshed = self.broker.lookup(o["client_order_id"])
                        if refreshed:
                            intent = self.store.get(o["client_order_id"], "intent")
                            if intent:
                                self.validate_order(refreshed, intent)
                            self.store.order(refreshed)
                        current = self.store.get(o["client_order_id"], "order")
                        if dec(current["filled_qty"]) != fill_by_order.get(o["id"], ZERO):
                            raise LedgerError(
                                "Order cumulative fill and activity ledger disagree; waiting for broker convergence"
                            )
                self.store.put("snapshot", snap)
                self.store.put("audit", ledger)
                self.store.put("reconciliation", {"ok": True, "at": now(), "reason": None})
                self.store.log("reconciled", {"cash": snap.account["cash"], "equity": snap.account["equity"]})
                return snap
            except Exception as error:
                self.pause_reconciliation(safe_error(error))
                raise

    @traced("service.evaluate")
    def evaluate(self, decision, snapshot=None):
        decision = Decision.model_validate(decision)
        quote = self.data.quote(decision.symbol)
        asset, clock = self.broker.asset(decision.symbol), self.broker.clock()
        at = now()
        from zoneinfo import ZoneInfo

        date = at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        calendar = self.broker.calendar(date, date)
        snapshot = (
            self.reconcile()
        )  # Recheck account after external data requests, immediately before gate/POST.
        recon = self.store.get("reconciliation") or {}
        last = self.store.get(decision.symbol, "last_trade")
        checked_at = now()
        reasons = check(
            self.cfg,
            decision,
            snapshot,
            quote,
            asset,
            clock,
            calendar,
            self.store.active_orders(),
            self.store.activities(),
            last["at"] if last else None,
            recon.get("ok", False),
            self.kill_path.exists(),
            self.store.experiment["ends_at"],
            checked_at,
        )
        if decision.side == "buy":
            reasons.extend(self.position_review_issues(snapshot.positions, checked_at))
        return (
            reasons,
            quote,
            {
                "at": checked_at,
                "snapshot": snapshot,
                "asset": asset,
                "clock": clock,
                "calendar": calendar,
                "settings": self.cfg.model_dump(
                    mode="json", exclude={"sec_user_agent", "database", "control_dir"}
                ),
            },
        )

    def position_review_issues(self, positions, at):
        """Budget-independent entry restrictions, never an instruction to liquidate."""
        issues = []
        for position in positions:
            if dec(position["qty"]) <= 0:
                continue
            symbol = position["symbol"]
            thesis = self.store.get(symbol, "thesis")
            try:
                if not thesis or dt(thesis["reviewed_at"]) > at:
                    issues.append(f"{symbol}: held position lacks a valid reviewed thesis")
                elif dt(thesis["decision"]["next_review"]) <= at:
                    issues.append(f"{symbol}: held-position review overdue")
            except (KeyError, TypeError, ValueError):
                issues.append(f"{symbol}: held-position review metadata invalid")
        return sorted(issues)

    def client_order_id(self, intent_key):
        return "pg-" + hashlib.sha256(f"{self.store.scope}:{intent_key}".encode()).hexdigest()[:40]

    @traced("service.execute")
    def execute(self, decision, intent_key, evidence=None):
        decision = Decision.model_validate(decision)
        self.require_experiment()
        cid = self.client_order_id(intent_key)
        with self.lock():
            self.require_experiment()
            prior = self.store.get(cid, "intent")
            if prior:
                if prior["decision"] != decision.model_dump(mode="json"):
                    raise RuntimeError("Intent key reused with different decision")
                self.reconcile()
                return self.store.get(cid, "intent")
            if decision.action in {"HOLD", "WATCH"}:
                result = {
                    "client_order_id": cid,
                    "decision": decision,
                    "status": "no_order",
                    "at": now(),
                    "evidence": evidence,
                }
                self.store.put(cid, result, "intent")
                self.store.log("decision", result)
                return result
            # Serialization covers fresh reconciliation, risk evaluation, journal commit and POST.
            research_reasons = []
            research = self.store.get(intent_key, "research_result")
            if research:
                if research["decision"] != decision.model_dump(mode="json"):
                    research_reasons.append("Decision does not match immutable research")
                completed = dt(research["research_completed_at"])
                if not timedelta(0) <= now() - completed < timedelta(hours=self.cfg.research_cache_hours):
                    research_reasons.append("Research is stale or future-dated")
                elif any(
                    dt(item["created_at"]) > completed
                    for item in self.data.news(decision.symbol, completed.isoformat())
                ):
                    research_reasons.append("New news requires research review")
            reasons, quote, risk_context = self.evaluate(decision)
            reasons.extend(research_reasons)
            result = {
                "client_order_id": cid,
                "decision": decision.model_dump(mode="json"),
                "at": now().isoformat(),
                "evidence": evidence,
                "quote": quote.model_dump(mode="json"),
                "risk_context": risk_context,
                "status": "rejected" if reasons else "prepared",
                "time_in_force": self.cfg.time_in_force,
                "reasons": reasons,
            }
            self.store.put(cid, result, "intent")
            self.store.log("decision", result)
            if reasons:
                return result
            payload = {
                "symbol": decision.symbol,
                "qty": str(decision.quantity),
                "side": decision.side,
                "type": "limit",
                "limit_price": str(decision.limit_price),
                "time_in_force": self.cfg.time_in_force,
                "extended_hours": False,
                "client_order_id": cid,
            }
            # Kill can be activated without waiting for this account lock.
            if self.kill_path.exists():
                result.update(status="rejected", reasons=["Kill switch activated before submission"])
                self.store.put(cid, result, "intent")
                return result
            try:
                order = self.broker.submit(payload)
            except Exception as error:  # noqa: BLE001 - persist failure at the execution boundary
                result.update(status="unknown", error=safe_error(error))
                self.store.put(cid, result, "intent")
                self.store.log("submission_uncertain", result)
                try:
                    order = self.broker.lookup(cid)
                except Exception as lookup_error:  # noqa: BLE001 - never retry an ambiguous submission
                    self.store.log("lookup_failed", {"error": safe_error(lookup_error)})
                    order = None
                if order is None:
                    # Definitive validation failures are safe to release. 5xx, 429, and duplicate IDs aren't.
                    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {
                        400,
                        401,
                        403,
                    }:
                        result["status"] = "rejected"
                        self.store.put(cid, result, "intent")
                    else:
                        self.pause_reconciliation(
                            f"Ambiguous submission {cid}; keep reservation and resolve by lookup"
                        )
                    return result
            try:
                self.validate_order(order, result)
            except Exception:
                result["status"] = "unknown"
                self.store.put(cid, result, "intent")
                self.pause_reconciliation("Broker submission response differs from approved intent")
                raise
            self.store.order(order)
            result.update(status="submitted", broker_order_id=order["id"])
            self.store.put(cid, result, "intent")
            self.store.put(decision.symbol, {"at": now(), "client_order_id": cid}, "last_trade")
            self.store.log("order_submitted", {"client_order_id": cid, "broker_order_id": order["id"]})
            # Submission never changes positions or cash. Reconciliation consumes confirmed activities.
            return result

    def cancel_expired(self, cancel_all=False):
        with self.lock():
            self.require_experiment()
            if self.broker.account()["id"] != self.account_id:
                raise LedgerError("Broker account identity does not match experiment")
            # Cancellation must work even when missing fills or an unknown submission block replay.
            for o in self.broker.open_orders():
                if (
                    cancel_all
                    or self.kill_path.exists()
                    or now() >= dt(self.store.experiment["ends_at"])
                    or now() - dt(o["created_at"]) >= timedelta(minutes=self.cfg.order_ttl_minutes)
                ):
                    self.request_cancel(o)

    @traced("service.request_cancel")
    def request_cancel(self, order):
        try:
            self.require_experiment()
            if self.broker.account()["id"] != self.account_id:
                raise LedgerError("Broker account identity does not match experiment")
            self.broker.cancel(order["id"])
            self.store.log("cancel_requested", {"id": order["id"]})
        except Exception as e:  # noqa: BLE001 - failed cancellation/reconnect must remain observable
            self.store.log("cancel_error", {"id": order["id"], "error": safe_error(e)})
        # Do not change order status or release reservations until the broker confirms.

    @traced("service.kill")
    def kill(self, reason):
        self.kill_path.write_text(reason)
        with self.lock():
            self.store.log("kill_switch", {"reason": reason})
            try:
                self.reconcile()
            except Exception as error:  # noqa: BLE001 - keep attempting cancellation while paused
                self.store.log("kill_reconciliation_pending", {"error": safe_error(error)})
            # Fetch open orders even when bookkeeping failed; cancellation remains possible.
            self.cancel_expired(cancel_all=True)
            try:
                self.reconcile()
            except Exception as error:  # noqa: BLE001 - keep attempting cancellation while paused
                self.store.log("kill_reconciliation_pending", {"error": safe_error(error)})

    @traced("service.resume")
    def resume(self):
        with self.lock():
            self.reconcile()
            self.kill_path.unlink(missing_ok=True)
            self.store.log("kill_switch_resumed", {})

    @traced("service.acknowledge_adjustment")
    def acknowledge_adjustment(self, adjustment):
        with self.lock():
            if not adjustment.get("reason") or "cash" not in adjustment or "id" not in adjustment:
                raise ValueError(
                    "Adjustment requires broker activity id, exact cash delta, positions, and reason"
                )
            if not self.store.get(adjustment["id"], "activity"):
                raise ValueError("Adjustment must reference a recorded broker activity")
            self.store.put(adjustment["id"], adjustment, "adjustment")
            self.store.log("explicit_adjustment", adjustment)
            return self.reconcile()

    @traced("service.acknowledge_revision")
    def acknowledge_revision(self, revision):
        """Approve exact broker evidence, never a guessed cash/position patch."""
        with self.lock():
            self.require_experiment()
            if not self.kill_path.exists():
                raise ValueError("Pause trading before approving broker revisions")
            before, after = revision.get("before"), revision.get("after")
            if not revision.get("reason") or not before or not after or before["id"] != after["id"]:
                raise ValueError("Revision requires reason and exact before/after activity with same ID")
            identifier = hashlib.sha256(dumps(revision).encode()).hexdigest()
            if self.store.get(identifier, "broker_revision"):
                return self.reconcile()
            current = next((a for a in self.store.activities() if a["id"] == before["id"]), None)
            if current != before or before == after:
                raise ValueError("Revision predecessor does not match current audited activity")
            tracked = [o["client_order_id"] for o in self.store.items("order")]
            snap = self.broker.snapshot(self.store.experiment["started_at"], tracked)
            if snap.account["id"] != self.account_id:
                raise LedgerError("Broker account identity does not match experiment")
            if next((a for a in snap.activities if a["id"] == after["id"]), None) != after:
                raise ValueError("Replacement does not match current broker activity")
            if before["activity_type"] == "FILL" or after["activity_type"] == "FILL":
                if any(
                    before.get(k) != after.get(k) for k in ("activity_type", "order_id", "symbol", "side")
                ):
                    raise ValueError("Fill revision cannot change approved order identity")
                order_before, order_after = revision.get("order_before"), revision.get("order_after")
                if not order_before or not order_after or order_after["id"] != after["order_id"]:
                    raise ValueError("Fill revision requires exact before/after broker order")
                cid = order_after["client_order_id"]
                if self.store.get(cid, "order") != order_before:
                    raise ValueError("Order predecessor does not match audited order")
                if next((o for o in snap.orders if o["client_order_id"] == cid), None) != order_after:
                    raise ValueError("Replacement does not match current broker order")
                intent = self.store.get(cid, "intent")
                if not intent:
                    raise ValueError("Fill revision has no approved intent")
                self.validate_order(order_after, intent)
            elif revision.get("order_before") or revision.get("order_after"):
                raise ValueError("Non-fill revision cannot revise an order")
            replay(
                self.store.experiment["initial_capital"],
                [after if a["id"] == before["id"] else a for a in self.store.activities()],
                {x["id"]: x for x in self.store.items("adjustment")},
            )
            # Approval order disambiguates corrections that later return to an earlier value.
            approved = {
                **revision,
                "sequence": 1
                + max((r.get("sequence", 0) for r in self.store.items("broker_revision")), default=0),
            }
            self.store.put(identifier, approved, "broker_revision")
            self.store.log("broker_revision_approved", {"id": identifier, **approved})
            return self.reconcile()

    def reservation_summary(self):
        amount = reserved(self.store.active_orders(), self.cfg.fee_buffer)
        for i in self.store.items("intent"):
            if i["status"] in {"prepared", "unknown"} and not self.store.get(i["client_order_id"], "order"):
                d = Decision.model_validate(i["decision"])
                if d.side == "buy":
                    amount += d.quantity * d.limit_price + self.cfg.fee_buffer
        return amount
