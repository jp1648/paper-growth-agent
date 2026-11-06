from datetime import timedelta

import httpx
import pytest

from trader.models import now
from trader.notifications import notify_fills
from trader.observability import clean


@pytest.mark.parametrize("outcome", ["sent", "timeout", "rejected"])
def test_confirmed_fills_once_and_no_secret_in_events(env, monkeypatch, outcome):
    service, broker, _ = env
    store = service.store
    token = "123456:fixture_token_only"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    notify_fills(store)
    assert store.get("telegram_enabled")
    fill = {
        "id": "fill-1",
        "activity_type": "FILL",
        "transaction_time": now(),
        "side": "buy",
        "qty": "1",
        "symbol": "ACME",
        "price": "100.25",
    }
    store.put("old", {**fill, "id": "old", "transaction_time": now() - timedelta(days=1)}, "activity")
    store.put("cash", {"id": "cash", "activity_type": "JNLC"}, "activity")
    store.put(fill["id"], fill, "activity")
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        assert store.get(fill["id"], "telegram_notification")["status"] == "unknown"
        if outcome == "timeout":
            raise httpx.ReadTimeout(url)
        return httpx.Response(
            200 if outcome == "sent" else 403,
            json={"ok": outcome == "sent", "result": {"message_id": 42}},
        )

    monkeypatch.setattr("trader.notifications.httpx.post", post)
    notify_fills(store)
    notify_fills(store)
    assert len(calls) == 1
    assert "BUY 1 ACME @ $100.25" in calls[0]["json"]["text"]
    assert calls[0]["follow_redirects"] is False
    assert store.get(fill["id"], "telegram_notification")["status"] == (
        "unknown" if outcome == "timeout" else outcome
    )
    assert token not in str(store.events())
    assert clean({"url": "https://api.telegram.org/bot" + token + "/sendMessage"}) == {
        "url": "https://api.telegram.org/bot[REDACTED]/sendMessage"
    }
    assert broker.submits == 0


def test_disabled_notifications_make_no_requests(env, monkeypatch):
    service, _, _ = env
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr("trader.notifications.httpx.post", lambda *a, **k: pytest.fail("disabled"))
    notify_fills(service.store)
    assert service.store.get("telegram_enabled") is None


def test_notification_failure_does_not_interrupt_maintenance(env, monkeypatch):
    from trader.runner import Runner

    service, broker, _ = env

    def fail(store):
        raise RuntimeError("https://api.telegram.org/botSECRET/sendMessage")

    monkeypatch.setattr("trader.notifications.notify_fills", fail)
    assert Runner(service, None).maintain()["status"] == "reconciled"
    events = service.store.events("telegram_notification_failed")
    assert events[-1]["body"] == {"error_type": "RuntimeError"}
    assert "SECRET" not in str(events)
    assert broker.submits == 0
