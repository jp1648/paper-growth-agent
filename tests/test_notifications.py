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


def test_confirmed_rate_limit_retries_with_delay_and_bound(env, monkeypatch):
    from conftest import AT

    monkeypatch.setattr("trader.notifications.now", lambda: AT)
    service, _, _ = env
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fixture")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    notify_fills(service.store)
    service.store.put(
        "fill-retry",
        {
            "id": "fill-retry",
            "activity_type": "FILL",
            "transaction_time": AT.isoformat(),
            "side": "buy",
            "qty": "1",
            "symbol": "ACME",
            "price": "100",
        },
        "activity",
    )
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 120}})

    monkeypatch.setattr("trader.notifications.httpx.post", post)
    for seconds in (0, 30, 120, 121, 240, 400):
        monkeypatch.setattr(
            "trader.notifications.now", lambda seconds=seconds: AT + timedelta(seconds=seconds)
        )
        notify_fills(service.store)
    assert len(calls) == 3
    record = service.store.get("fill-retry", "telegram_notification")
    assert record["status"] == "rejected"
    assert record["attempts"] == 3


def test_server_failure_is_ambiguous_not_retried(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fixture")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    notify_fills(service.store)
    service.store.put(
        "fill-server",
        {
            "id": "fill-server",
            "activity_type": "FILL",
            "transaction_time": now(),
            "side": "buy",
            "qty": "1",
            "symbol": "ACME",
            "price": "100",
        },
        "activity",
    )
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(500, json={"ok": False})

    monkeypatch.setattr("trader.notifications.httpx.post", post)
    notify_fills(service.store)
    notify_fills(service.store)
    assert len(calls) == 1
    assert service.store.get("fill-server", "telegram_notification")["status"] == "unknown"


@pytest.mark.parametrize(
    "status,body",
    [
        (200, None),
        (200, []),
        (200, "unexpected"),
        (200, {"ok": True, "result": None}),
        (200, {"ok": True, "result": {"message_id": None}}),
        (200, {"ok": True, "result": {"message_id": True}}),
        (429, {"ok": False, "parameters": None}),
        (429, {"ok": False, "parameters": {"retry_after": []}}),
    ],
)
def test_malformed_provider_reply_stays_unknown_and_does_not_block_other_fills(
    env, monkeypatch, status, body
):
    service, _, _ = env
    store = service.store
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fixture")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    notify_fills(store)
    for identifier in ("a-malformed", "b-valid"):
        store.put(
            identifier,
            {
                "id": identifier,
                "activity_type": "FILL",
                "transaction_time": now(),
                "side": "buy",
                "qty": "1",
                "symbol": "ACME",
                "price": "100",
            },
            "activity",
        )
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"]["text"])
        if len(calls) == 1:
            return (
                httpx.Response(status, json=body) if body is not None else httpx.Response(status, text="null")
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 123}})

    monkeypatch.setattr("trader.notifications.httpx.post", post)
    notify_fills(store)
    notify_fills(store)
    assert len(calls) == 2
    assert store.get("a-malformed", "telegram_notification")["status"] == "unknown"
    assert store.get("b-valid", "telegram_notification")["status"] == "sent"


@pytest.mark.parametrize("outcome", ["observed_sent", "closed_unconfirmed"])
def test_operator_resolution_is_audited_idempotent_and_never_resends(env, monkeypatch, outcome):
    import json

    from trader.models import dumps
    from trader.notifications import resolve_delivery

    service, _, _ = env
    store = service.store
    original = {
        "status": "unknown",
        "attempted_at": now().isoformat(),
        "fill_id": "fill-unknown",
        "attempts": 1,
    }
    store.put("fill-unknown", original, "telegram_notification")
    resolution = {
        "notification_id": "fill-unknown",
        "attempted_at": original["attempted_at"],
        "outcome": outcome,
        "evidence": "Operator inspected the private chat history.",
    }
    if outcome == "observed_sent":
        resolution["message_id"] = 456
    monkeypatch.setattr(
        "trader.notifications.httpx.post", lambda *a, **k: pytest.fail("Resolution must not send")
    )
    recorded = resolve_delivery(store, resolution)
    assert resolve_delivery(store, resolution) == json.loads(dumps(recorded))
    assert store.get("fill-unknown", "telegram_notification") == original
    assert store.get("fill-unknown", "telegram_resolution")["original_attempt"] == original
    assert len(store.events("telegram_resolution")) == 1
    with pytest.raises(ValueError, match="different resolution"):
        resolve_delivery(store, {**resolution, "evidence": "Changed evidence"})


@pytest.mark.parametrize(
    "fault",
    ["missing_attempt", "already_sent", "stale_attempt", "no_evidence", "no_message_id", "unknown_field"],
)
def test_notification_resolution_rejects_unverified_or_stale_claims(env, fault):
    from trader.notifications import resolve_delivery

    service, _, _ = env
    store = service.store
    original = {"status": "sent" if fault == "already_sent" else "unknown", "attempted_at": now().isoformat()}
    if fault != "missing_attempt":
        store.put("fill", original, "telegram_notification")
    resolution = {
        "notification_id": "fill",
        "attempted_at": original["attempted_at"],
        "outcome": "observed_sent",
        "message_id": 456,
        "evidence": "Checked private chat.",
    }
    if fault == "stale_attempt":
        resolution["attempted_at"] = "2000-01-01T00:00:00Z"
    elif fault == "no_evidence":
        resolution["evidence"] = ""
    elif fault == "no_message_id":
        resolution.pop("message_id")
    elif fault == "unknown_field":
        resolution["extra"] = "not allowed"
    with pytest.raises(ValueError):
        resolve_delivery(store, resolution)
    assert not store.items("telegram_resolution")


def test_resolution_cli_never_constructs_broker(env, monkeypatch, tmp_path):
    import json
    from argparse import Namespace

    from trader.cli import dispatch

    service, _, _ = env
    stamp = now().isoformat()
    service.store.put("cli-fill", {"status": "unknown", "attempted_at": stamp}, "telegram_notification")
    path = tmp_path / "resolution.json"
    path.write_text(
        json.dumps(
            {
                "notification_id": "cli-fill",
                "attempted_at": stamp,
                "outcome": "closed_unconfirmed",
                "evidence": "Unable to verify; do not resend.",
            }
        )
    )
    monkeypatch.setattr("trader.cli.Settings.load", lambda _: service.cfg)
    monkeypatch.setattr("trader.cli.AlpacaBroker", lambda _: pytest.fail("No broker access needed"))
    monkeypatch.setattr("trader.cli.AlpacaData", lambda _: pytest.fail("No data access needed"))
    assert dispatch(Namespace(config="unused", command="resolve-notification", file=str(path))) == 0
    assert (
        service.store.get("cli-fill", "telegram_resolution")["resolution"]["outcome"] == "closed_unconfirmed"
    )


@pytest.mark.parametrize("delivery", ["sent", "timeout"])
def test_approved_corrections_notify_once_even_when_returning_to_original_value(
    env, decision, monkeypatch, delivery
):
    from conftest import AT

    monkeypatch.setattr("trader.notifications.now", lambda: AT)
    service, broker, _ = env
    store = service.store
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fixture")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    notify_fills(store)
    messages = []

    def post(*args, **kwargs):
        messages.append(kwargs["json"]["text"])
        if delivery == "timeout" and len(messages) > 1:
            raise httpx.ReadTimeout("ambiguous correction delivery")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(messages)}})

    monkeypatch.setattr("trader.notifications.httpx.post", post)
    cid = service.execute(decision, "notify-corrections")["client_order_id"]
    original = broker.fixture_fill(cid)
    service.reconcile()
    notify_fills(store)
    original_notification = store.get(original["id"], "telegram_notification")
    before = original
    service.kill_path.write_text("correction review")
    for index, qty in enumerate((9, 10)):
        order_before = store.get(cid, "order")
        after = {**original, "qty": str(qty)}
        broker.activities = [after]
        broker.orders[cid]["filled_qty"] = str(qty)
        broker.raw_account["cash"] = str(100000 - qty * 100)
        broker.positions[0].update(qty=str(qty), cost_basis=str(qty * 100), market_value=str(qty * 100))
        service.acknowledge_revision(
            {
                "before": before,
                "after": after,
                "reason": f"Correction {index}",
                "order_before": order_before,
                "order_after": broker.orders[cid].copy(),
            }
        )
        reconciled = store.get("reconciliation")
        audit = store.get("audit")
        store.put("reconciliation", {**reconciled, "ok": False})
        notify_fills(store)
        assert len(messages) == index + 1
        store.put("reconciliation", reconciled)
        store.put("audit", {**audit, "fills": []})
        notify_fills(store)
        assert len(messages) == index + 1
        store.put("audit", audit)
        notify_fills(store)
        notify_fills(store)
        before = after
    assert len(messages) == 3
    assert messages[0].startswith("PAPER TRADE FILL")
    assert all(m.startswith("PAPER TRADE CORRECTION") for m in messages[1:])
    assert "BUY 9 ACME" in messages[1]
    assert "BUY 10 ACME" in messages[2]
    assert store.get(original["id"], "telegram_notification") == original_notification
    assert broker.submits == 1
