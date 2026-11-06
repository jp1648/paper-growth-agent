"""Optional Telegram alerts for durable broker fill activities; never submit orders."""

import fcntl
import os
import re

import httpx

from .models import dt, now


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
        for fill in store.items("activity"):
            if fill.get("activity_type") != "FILL" or dt(fill["transaction_time"]) < dt(enabled["at"]):
                continue
            if store.get(fill["id"], "telegram_notification"):
                continue
            text = (
                f"PAPER TRADE FILL\n{fill['side'].upper()} {fill['qty']} {fill['symbol']} "
                f"@ ${fill['price']}\nTime: {fill['transaction_time']}\nFill ID: {fill['id']}"
            )
            # Telegram has no sendMessage idempotency key; uncertain sends require review, not replay.
            record = {"status": "unknown", "attempted_at": now(), "fill_id": fill["id"]}
            store.put(fill["id"], record, "telegram_notification")
            try:
                response = httpx.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text},
                    timeout=10,
                    follow_redirects=False,
                )
                body = response.json()
                if response.is_success and body.get("ok") is True:
                    record.update(status="sent", message_id=body["result"]["message_id"])
                else:
                    record.update(status="rejected", http_status=response.status_code)
            except (httpx.HTTPError, ValueError, KeyError):
                pass  # Never log a request URL: Telegram embeds its credential in the path.
            store.put(fill["id"], record, "telegram_notification")
            store.log("telegram_notification", record)
