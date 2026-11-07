import json
from datetime import timedelta

import httpx
import pytest
from conftest import AT

from trader.alpaca import AlpacaBroker
from trader.executor_owner import ExecutionFenceError, verify_executor_owner


@pytest.mark.parametrize(
    "case",
    ["match", "other_vm", "other_project", "other_zone", "malformed", "missing", "timeout", "redirect"],
)
def test_owner_check_fails_closed(case, monkeypatch):
    identity = {"instance_id": "123", "project_number": "456", "zone": "projects/456/zones/us-east1-b"}
    owner = {"version": 1, **identity}
    changes = {
        "other_vm": ("instance_id", "789"),
        "other_project": ("project_number", "789"),
        "other_zone": ("zone", "projects/456/zones/us-east1-c"),
        "malformed": ("version", True),
    }
    if case in changes:
        key, value = changes[case]
        owner[key] = value
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.host == "169.254.169.254":
            values = {
                "id": identity["instance_id"],
                "numeric-project-id": identity["project_number"],
                "zone": identity["zone"],
            }
            if request.url.path.endswith("/token"):
                return httpx.Response(
                    200, json={"access_token": "fixture-token"}, headers={"Metadata-Flavor": "Google"}
                )
            return httpx.Response(
                200, text=values[request.url.path.split("/")[-1]], headers={"Metadata-Flavor": "Google"}
            )
        if case == "timeout":
            raise httpx.ReadTimeout("Fixture storage unavailable")
        if case == "redirect":
            return httpx.Response(307, headers={"Location": "https://wrong.invalid"})
        return httpx.Response(404 if case == "missing" else 200, json=owner)

    real = httpx.Client
    monkeypatch.setattr(
        "trader.executor_owner.httpx.Client", lambda **kw: real(transport=httpx.MockTransport(respond), **kw)
    )
    if case == "match":
        verify_executor_owner("private-recovery")
    else:
        with pytest.raises(ExecutionFenceError):
            verify_executor_owner("private-recovery")
    assert all(r.url.host in {"169.254.169.254", "storage.googleapis.com"} for r in requests)


@pytest.mark.parametrize("method,path", [("POST", "/v2/orders"), ("DELETE", "/v2/orders/fixture")])
def test_broker_mutations_fenced_but_reads_remain_available(env, monkeypatch, method, path):
    service, _, _ = env
    service.cfg.checkpoint_bucket = "private-recovery"
    service.cfg.executor_fence = True
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture")
    sent = []

    def respond(request):
        sent.append(request.method)
        return httpx.Response(200, json={})

    def deny(_):
        raise ExecutionFenceError("Fixture wrong VM")

    monkeypatch.setattr("trader.alpaca.verify_executor_owner", deny)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        broker = AlpacaBroker(service.cfg, client)
        with pytest.raises(ExecutionFenceError):
            broker.request(method, path)
        broker.account()
    assert sent == ["GET"]


def test_known_fence_denial_is_not_an_ambiguous_submission(env, decision, monkeypatch):
    service, broker, _ = env

    def denied(payload, **kwargs):
        raise ExecutionFenceError("Fixture revoked ownership")

    monkeypatch.setattr(broker, "submit", denied)
    result = service.execute(decision, "wrong-owner")
    assert result["status"] == "rejected"
    assert broker.submits == 0 and service.reservation_summary() == 0
    assert service.store.events("execution_fenced")
    assert not service.store.events("submission_uncertain")


@pytest.mark.parametrize(
    "case", ["healthy", "stale_clock", "stale_quote", "session_closed", "pause", "check_error"]
)
def test_final_risk_check_runs_after_ownership_io_before_actual_transport(env, decision, monkeypatch, case):
    service, scripted, _ = env
    service.cfg.checkpoint_bucket = "private-recovery"
    service.cfg.executor_fence = True
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture")
    monkeypatch.setattr("trader.checkpoints.replicate_checkpoint", lambda *args: {"generation": "123"})
    sent = []
    submit = scripted.submit

    def transport(request):
        sent.append(request.method)
        return httpx.Response(200, json=submit(json.loads(request.read())))

    def ownership(_):
        if case in {"stale_clock", "stale_quote", "session_closed"}:
            seconds = {"stale_clock": 31, "stale_quote": 61, "session_closed": 6 * 3600}[case]
            monkeypatch.setattr("trader.service.now", lambda: AT + timedelta(seconds=seconds))
        elif case == "pause":
            service.kill_path.write_text("Pause while ownership verification was in flight")
        elif case == "check_error":

            def failed(*args):
                raise RuntimeError("Fixture risk check failure")

            monkeypatch.setattr("trader.service.check", failed)

    monkeypatch.setattr("trader.alpaca.verify_executor_owner", ownership)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        real = AlpacaBroker(service.cfg, client)
        monkeypatch.setattr(scripted, "submit", real.submit)
        result = service.execute(decision, "transport-boundary")
    if case == "healthy":
        assert result["status"] == "submitted" and sent == ["POST"]
    else:
        assert result["status"] == "rejected" and sent == []
        assert service.reservation_summary() == 0
        assert service.store.events("submission_preflight_rejected")
        assert not service.store.events("submission_uncertain")
        expected = {
            "stale_clock": "broker clock stale",
            "stale_quote": "Quote delayed, stale",
            "session_closed": "Outside broker calendar",
            "pause": "Kill switch",
            "check_error": "risk check failed",
        }[case]
        assert any(expected in reason for reason in result["reasons"])
