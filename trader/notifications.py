"""Optional Telegram alerts for durable broker fill activities; never submit orders."""

import fcntl
import hashlib
import os
import re
from datetime import timedelta

import httpx

from .models import dt, dumps, now


def resolve_delivery(store, resolution):
    """Record an operator's observation without changing or replaying the original attempt."""
    if not store.experiment:
        raise ValueError("Notification resolution requires an existing experiment")
    if not isinstance(resolution, dict):
        raise TypeError("Resolution must be an object")
    if set(resolution) - {"notification_id", "outcome", "evidence", "message_id", "attempted_at"}:
        raise ValueError("Unexpected resolution fields")
    identifier = resolution.get("notification_id")
    outcome = resolution.get("outcome")
    evidence = resolution.get("evidence")
    if (
        not isinstance(identifier, str)
        or not identifier.strip()
        or outcome not in {"observed_sent", "closed_unconfirmed"}
    ):
        raise ValueError("Specify notification_id and an allowed resolution outcome")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 2000:
        raise ValueError("Resolution requires concise operator evidence")
    message_id = resolution.get("message_id")
    if outcome == "observed_sent" and (type(message_id) is not int or message_id <= 0):
        raise ValueError("Observed delivery requires the verified Telegram message ID")
    if outcome == "closed_unconfirmed" and message_id is not None:
        raise ValueError("Unconfirmed delivery cannot claim a message ID")
    with store.path.with_suffix(".telegram.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = store.get(identifier, "telegram_notification")
        if not original or original["status"] != "unknown":
            raise ValueError("Only an unknown notification attempt can be resolved")
        if resolution.get("attempted_at") != original["attempted_at"]:
            raise ValueError("Resolution must name the exact attempted_at value")
        previous = store.get(identifier, "telegram_resolution")
        if previous:
            if previous["resolution"] != resolution:
                raise ValueError("A different resolution is already recorded")
            return previous
        record = {"resolution": resolution, "original_attempt": original, "recorded_at": now()}
        store.put(identifier, record, "telegram_resolution")
        store.log("telegram_resolution", record)
        return record


def notify_fills(store):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?\d+", chat):
        raise ValueError("Invalid Telegram configuration")
    with store.path.with_suffix(".telegram.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        enabled = store.get("telegram_enabled")
        if not enabled:
            store.put("telegram_enabled", {"at": now()})
            return  # Activation starts with future fills, not historical trade spam.
        for fill in store.activities():
            if fill.get("activity_type") != "FILL" or dt(fill["transaction_time"]) < dt(enabled["at"]):
                continue
            identifier = fill["id"]
            previous = store.get(identifier, "telegram_notification")
            revisions = [r for r in store.items("broker_revision") if r["after"] == fill]
            revision = max(revisions, key=lambda r: r.get("sequence", 0)) if revisions else None
            revision_id = hashlib.sha256(dumps(revision).encode()).hexdigest() if revision else None
            correction = False
            resolution = store.get(identifier, "telegram_resolution") or {}
            observed = resolution.get("resolution", {}).get("outcome") == "observed_sent"
            if (
                previous
                and (previous["status"] == "sent" or observed)
                and revision_id != previous.get("revision_id")
            ):
                if not (store.get("reconciliation") or {}).get("ok"):
                    continue
                if fill not in (store.get("audit") or {}).get("fills", []):
                    continue
                identifier = f"{fill['id']}:correction:{revision_id}"
                previous = store.get(identifier, "telegram_notification")
                correction = True
            if previous and (
                previous["status"] != "retryable"
                or previous.get("attempts", 1) >= 3
                or now() < dt(previous["retry_at"])
            ):
                continue
            text = (
                f"PAPER TRADE FILL\n{fill['side'].upper()} {fill['qty']} {fill['symbol']} "
                f"@ ${fill['price']}\nTime: {fill['transaction_time']}\nFill ID: {fill['id']}"
            )
            if correction:
                text = text.replace("PAPER TRADE FILL", "PAPER TRADE CORRECTION", 1)
                text += "\nCurrent corrected broker record; this is not a new trade."
            # Telegram has no sendMessage idempotency key; uncertain sends require review, not replay.
            record = {
                "status": "unknown",
                "attempted_at": now(),
                "fill_id": fill["id"],
                "notification_id": identifier,
                "revision_id": revision_id,
                "attempts": (previous.get("attempts", 1) if previous else 0) + 1,
            }
            store.put(identifier, record, "telegram_notification")
            try:
                response = httpx.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text},
                    timeout=10,
                    follow_redirects=False,
                )
                body = response.json()
                if not isinstance(body, dict):
                    raise TypeError("Invalid notification response")
                if response.is_success and body.get("ok") is True:
                    result = body.get("result")
                    message_id = result.get("message_id") if isinstance(result, dict) else None
                    if type(message_id) is not int or message_id <= 0:
                        raise ValueError("Missing confirmed message ID")
                    record.update(status="sent", message_id=message_id)
                elif response.status_code == 429 and body.get("ok") is False:
                    parameters = body.get("parameters", {})
                    if not isinstance(parameters, dict):
                        raise ValueError("Invalid retry parameters")
                    delay = parameters.get("retry_after", 60)
                    if type(delay) is not int or delay <= 0:
                        raise ValueError("Invalid retry delay")
                    delay = max(60, min(86400, delay))
                    record.update(
                        status="retryable" if record["attempts"] < 3 else "rejected",
                        http_status=429,
                        retry_at=now() + timedelta(seconds=delay),
                    )
                elif 400 <= response.status_code < 500 and body.get("ok") is False:
                    record.update(status="rejected", http_status=response.status_code)
            except (httpx.HTTPError, ValueError, TypeError, KeyError):
                pass  # Never log a request URL: Telegram embeds its credential in the path.
            store.put(identifier, record, "telegram_notification")
            store.log("telegram_notification", record)
