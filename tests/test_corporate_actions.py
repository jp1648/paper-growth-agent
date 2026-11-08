import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from trader.corporate_actions import capture_actions


def provider(handler):
    return SimpleNamespace(
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        headers={"APCA-API-KEY-ID": "fixture-key"},
    )


def test_capture_preserves_pages_and_duplicates_without_certifying_returns(tmp_path):
    record = {"id": "distribution", "symbol": "SPY", "rate": "1.888834"}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "data.alpaca.markets"
        assert request.url.params["data_quality"] == "all"
        assert request.url.params["start"] == "2026-09-01"
        if len(requests) == 2:
            assert request.url.params["page_token"] == "next"
        return httpx.Response(
            200,
            json={
                "corporate_actions": {"cash_dividends": [record]},
                "next_page_token": "next" if len(requests) == 1 else None,
            },
        )

    p = provider(handler)
    result = capture_actions(p, "SPY", "2026-09-01", "2026-10-31", tmp_path / "capture")
    assert len(requests) == 2 and result["unique_actions"] == 1
    assert result["status"] == "pagination_exhausted"
    assert result["economic_fields_validated"] is False  # Incomplete dates retained, not fabricated.
    assert result["history_completeness_verified"] is False
    for page in result["pages"]:
        raw = (tmp_path / "capture" / page["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == page["sha256"]
    with pytest.raises(FileExistsError):
        capture_actions(p, "SPY", "2026-09-01", "2026-10-31", tmp_path / "capture")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "fault", ["conflict", "cycle", "missing_token", "group", "identity", "json", "redirect", "bytes", "pages"]
)
def test_failed_capture_is_retained_and_never_complete(tmp_path, monkeypatch, fault):
    calls = []
    monkeypatch.setattr("trader.corporate_actions.MAX_PAGES", 2)
    monkeypatch.setattr("trader.corporate_actions.MAX_PAGE_BYTES", 1024)

    def handler(request):
        calls.append(request)
        record = {"id": "same", "rate": str(len(calls)) if fault == "conflict" else "1"}
        page = {"corporate_actions": {"cash_dividends": [record]}, "next_page_token": "next"}
        if fault == "missing_token":
            page.pop("next_page_token")
        elif fault == "group":
            page["corporate_actions"]["cash_dividends"] = None
        elif fault == "identity":
            record.pop("id")
        elif fault == "pages":
            page["next_page_token"] = str(len(calls))
        elif fault == "redirect":
            return httpx.Response(307, headers={"Location": "https://untrusted.example/"})
        elif fault in {"bytes", "json"}:
            return httpx.Response(200, content=b"x" * (1025 if fault == "bytes" else 1))
        return httpx.Response(200, json=page)

    with pytest.raises((ValueError, TypeError, httpx.HTTPStatusError)):
        capture_actions(provider(handler), "SPY", "2026-09-01", "2026-10-31", tmp_path / "capture")
    result = json.loads((tmp_path / "capture/manifest.json").read_text())
    assert result["status"] == "failed" and result["error"]
    assert result["history_completeness_verified"] is False
    assert len(calls) <= 2
    assert all(r.url.host == "data.alpaca.markets" for r in calls)
    assert "fixture-key" not in (tmp_path / "capture/manifest.json").read_text()


@pytest.mark.parametrize(
    "symbol,start,end",
    [
        ("spy", "2026-09-01", "2026-10-31"),
        ("SPY", "2026-10-31", "2026-09-01"),
        ("SPY", "2024-01-01", "2026-01-01"),
        ("SPY", "20260901", "2026-10-31"),
    ],
)
def test_invalid_request_does_not_create_capture_or_request(tmp_path, symbol, start, end):
    p = provider(lambda _: pytest.fail("No network expected"))
    with pytest.raises(ValueError):
        capture_actions(p, symbol, start, end, tmp_path / "capture")
    assert not (tmp_path / "capture").exists()


@pytest.mark.parametrize("failure", ["total_bytes", "http", "stream"])
def test_later_page_failure_retains_prior_evidence_and_does_not_retry(tmp_path, monkeypatch, failure):
    first = b'{"corporate_actions":{"cash_dividends":[{"id":"a"}]},"next_page_token":"second"}'
    second = b'{"corporate_actions":{"cash_dividends":[{"id":"b"}]},"next_page_token":null}'
    calls = []
    if failure == "total_bytes":
        monkeypatch.setattr("trader.corporate_actions.MAX_TOTAL_BYTES", len(first) + len(second) - 1)

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield second[:20]
            raise httpx.ReadError("fixture-key must not appear in the failure receipt")

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, content=first)
        assert request.url.params["page_token"] == "second"
        if failure == "http":
            return httpx.Response(503, text="fixture-key must not appear in the failure receipt")
        if failure == "stream":
            return httpx.Response(200, stream=BrokenStream())
        return httpx.Response(200, content=second)

    root = tmp_path / "failed"
    with pytest.raises((ValueError, httpx.HTTPError)):
        capture_actions(provider(handler), "SPY", "2026-09-01", "2026-10-31", root)
    assert len(calls) == 2
    assert (root / "page-001.json").read_bytes() == first
    assert not (root / "page-002.json").exists()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["unique_actions"] == 1
    assert manifest["pages"] == [
        {"file": "page-001.json", "bytes": len(first), "sha256": hashlib.sha256(first).hexdigest()}
    ]
    assert "fixture-key" not in (root / "manifest.json").read_text()

    monkeypatch.setattr("trader.corporate_actions.MAX_TOTAL_BYTES", 10 * 1024 * 1024)
    recovered = capture_actions(
        provider(lambda _: httpx.Response(200, content=second)),
        "SPY",
        "2026-09-01",
        "2026-10-31",
        tmp_path / "new-attempt",
    )
    assert recovered["status"] == "pagination_exhausted"
    assert json.loads((root / "manifest.json").read_text()) == manifest


def test_cli_capture_does_not_construct_broker_or_ledger(tmp_path, monkeypatch):
    from trader import cli

    p = provider(lambda _: httpx.Response(200, json={"corporate_actions": {}, "next_page_token": None}))
    monkeypatch.setattr(cli.Settings, "load", lambda _: object())
    monkeypatch.setattr(cli, "AlpacaData", lambda _: p)
    monkeypatch.setattr(cli, "AlpacaBroker", lambda _: pytest.fail("No broker expected"))
    monkeypatch.setattr(cli, "Store", lambda _: pytest.fail("No ledger expected"))
    assert (
        cli.main(
            [
                "capture-corporate-actions",
                "SPY",
                "--start",
                "2026-09-01",
                "--end",
                "2026-10-31",
                "--output-dir",
                str(tmp_path / "capture"),
            ]
        )
        == 0
    )
    assert p.client.is_closed
