import hashlib

import httpx
import pytest

from trader.checkpoints import replicate_checkpoint


@pytest.mark.parametrize("mode", ["new", "existing", "timeout_committed"])
def test_replication_create_only_and_exact_generation_readback(tmp_path, monkeypatch, mode):
    path = tmp_path / "intent-fixture.sqlite"
    body = b"private checkpoint fixture"
    path.write_bytes(body)
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "169.254.169.254":
            assert request.headers["Metadata-Flavor"] == "Google"
            assert "Authorization" not in request.headers
            return httpx.Response(
                200, json={"access_token": "fixture-token"}, headers={"Metadata-Flavor": "Google"}
            )
        assert request.url.host == "storage.googleapis.com"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        if request.method == "POST":
            assert request.url.params["ifGenerationMatch"] == "0"
            assert request.read() == body
            if mode == "timeout_committed":
                raise httpx.ReadTimeout("Fixture ambiguous upload")
            return httpx.Response(412 if mode == "existing" else 200, json={})
        if request.url.params.get("alt") == "media":
            assert request.url.params["generation"] == "123"
            return httpx.Response(200, content=body)
        return httpx.Response(
            200,
            json={
                "bucket": "private-recovery",
                "name": "checkpoints/" + path.name,
                "size": str(len(body)),
                "generation": "123",
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "trader.checkpoints.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    receipt = replicate_checkpoint(path, "private-recovery")
    assert receipt["sha256"] == hashlib.sha256(body).hexdigest()
    assert [r.method for r in calls] == ["GET", "POST", "GET", "GET"]


@pytest.mark.parametrize(
    "failure", ["metadata_auth", "redirect", "missing", "wrong_identity", "oversize", "corrupt", "truncated"]
)
def test_replication_failures_never_report_success(tmp_path, monkeypatch, failure):
    path = tmp_path / "intent-fixture.sqlite"
    path.write_bytes(b"abc")
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "169.254.169.254":
            return httpx.Response(
                200,
                json={"access_token": "fixture-token"},
                headers={} if failure == "metadata_auth" else {"Metadata-Flavor": "Google"},
            )
        if request.method == "POST":
            if failure == "redirect":
                return httpx.Response(307, headers={"Location": "https://untrusted.invalid/upload"})
            if failure == "missing":
                raise httpx.ReadTimeout("Fixture upload did not commit")
            return httpx.Response(200, json={})
        if failure == "missing":
            return httpx.Response(404)
        if request.url.params.get("alt") == "media":
            return httpx.Response(
                200, content={"oversize": b"abcd", "corrupt": b"xyz", "truncated": b"ab"}.get(failure, b"abc")
            )
        return httpx.Response(
            200,
            json={
                "bucket": "other" if failure == "wrong_identity" else "private-recovery",
                "name": "checkpoints/" + path.name,
                "size": "3",
                "generation": "123",
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "trader.checkpoints.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises((ValueError, httpx.HTTPError)):
        replicate_checkpoint(path, "private-recovery")
    assert sum(r.method == "POST" for r in calls) <= 1
    assert all(r.url.host in {"169.254.169.254", "storage.googleapis.com"} for r in calls)


def test_order_waits_for_required_offhost_replication(env, decision, monkeypatch):
    service, broker, _ = env
    service.cfg.checkpoint_bucket = "private-recovery"
    calls = []

    def fail(path, bucket):
        assert path.exists() and broker.submits == 0
        calls.append(bucket)
        raise httpx.ReadTimeout("Fixture remote storage unavailable")

    monkeypatch.setattr("trader.checkpoints.replicate_checkpoint", fail)
    result = service.execute(decision, "offhost-failed")
    assert result["status"] == "rejected"
    assert broker.submits == 0 and calls == ["private-recovery"]
    assert not service.store.events("execution_checkpoint_replicated")


def test_replication_receipt_is_journaled_before_order(env, decision, monkeypatch):
    service, broker, _ = env
    service.cfg.checkpoint_bucket = "private-recovery"
    monkeypatch.setattr(
        "trader.checkpoints.replicate_checkpoint",
        lambda path, bucket: {"generation": "123", "sha256": "fixture"},
    )
    submit = broker.submit

    def checked(payload, **kwargs):
        assert service.store.events("execution_checkpoint_replicated")[0]["body"]["generation"] == "123"
        return submit(payload, **kwargs)

    monkeypatch.setattr(broker, "submit", checked)
    assert service.execute(decision, "offhost-success")["status"] == "submitted"
