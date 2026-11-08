import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from trader.corporate_actions import capture_actions, validate_cash_dividends


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


def ordinary_capture(tmp_path, **changes):
    action = {
        "id": "a",
        "symbol": "SPY",
        "rate": "1.888834",
        "foreign": False,
        "special": False,
        "ex_date": "2026-09-18",
        "record_date": "2026-09-18",
        "payable_date": "2026-10-30",
        "process_date": "2026-10-30",
        **changes,
    }
    p = provider(
        lambda _: httpx.Response(
            200, json={"corporate_actions": {"cash_dividends": [action]}, "next_page_token": None}
        )
    )
    root = tmp_path / "capture"
    capture_actions(p, "SPY", "2026-10-01", "2026-10-31", root)
    p.client.close()
    return root, action


def test_validate_capture_preserves_ex_date_outside_processing_window(tmp_path):
    root, action = ordinary_capture(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = validate_cash_dividends(root)
    assert result["dividends"] == [action]
    assert result["manifest_sha256"] == hashlib.sha256(before["manifest.json"]).hexdigest()
    assert result["history_completeness_verified"] is False
    assert result["entitlements_verified"] is False
    assert result["net_withholding_verified"] is False
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


@pytest.mark.parametrize(
    "field,value",
    [
        ("rate", "NaN"),
        ("rate", "Infinity"),
        ("rate", "0"),
        ("rate", "-1"),
        ("rate", True),
        ("symbol", "QQQ"),
        ("foreign", True),
        ("special", True),
        ("special", None),
        ("ex_date", "2026-11-01"),
        ("record_date", "2026-09-01"),
        ("process_date", "2026-11-01"),
        ("payable_date", None),
        ("ex_date", "20260918"),
    ],
)
def test_unusable_cash_fields_are_not_silently_normalized(tmp_path, field, value):
    root, _ = ordinary_capture(tmp_path, **{field: value})
    with pytest.raises((ValueError, TypeError)):
        validate_cash_dividends(root)


@pytest.mark.parametrize(
    "fault", ["bytes", "hash", "status", "path", "symlink", "count", "request", "continuation", "noncash"]
)
def test_capture_integrity_failures_prevent_dividend_conversion(tmp_path, fault):
    root, _ = ordinary_capture(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    page_path = root / "page-001.json"
    if fault == "bytes":
        page_path.write_bytes(page_path.read_bytes() + b" ")
    elif fault == "hash":
        manifest["pages"][0]["sha256"] = "0" * 64
    elif fault == "status":
        manifest["status"] = "failed"
    elif fault == "path":
        manifest["pages"][0]["file"] = "../outside.json"
    elif fault == "symlink":
        page_path.rename(tmp_path / "outside.json")
        page_path.symlink_to(tmp_path / "outside.json")
    elif fault == "count":
        manifest["unique_actions"] = 0
    elif fault == "request":
        manifest["params"]["symbols"] = "QQQ"
    else:
        page = json.loads(page_path.read_text())
        if fault == "continuation":
            page["next_page_token"] = "missing-page"
        else:
            page["corporate_actions"] = {"forward_splits": [{"id": "split"}]}
        raw = json.dumps(page).encode()
        page_path.write_bytes(raw)
        manifest["pages"][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate_cash_dividends(root)


def test_validation_cli_needs_no_config_credentials_or_broker(tmp_path, monkeypatch):
    from trader import cli

    root, _ = ordinary_capture(tmp_path)
    monkeypatch.setattr(cli.Settings, "load", lambda _: pytest.fail("No config expected"))
    monkeypatch.setattr(cli, "AlpacaData", lambda _: pytest.fail("No provider expected"))
    monkeypatch.setattr(cli, "Store", lambda _: pytest.fail("No ledger expected"))
    assert cli.main(["validate-cash-dividends", str(root)]) == 0


@pytest.mark.parametrize(
    "changes", [{}, {"rate": "2.00"}, {"payable_date": "2026-10-31"}, {"id": "replacement"}]
)
def test_dividend_revision_comparison_retains_both_versions(tmp_path, changes):
    from trader.corporate_actions import compare_cash_dividends

    before, original = ordinary_capture(tmp_path / "old")
    after, revised = ordinary_capture(tmp_path / "new", **changes)
    saved = {str(p): p.read_bytes() for root in (before, after) for p in root.iterdir()}
    result = compare_cash_dividends(before, after)
    assert result["history_completeness_verified"] is False and result["trial_accepted"] is False
    if not changes:
        assert result["status"] == "no_observed_changes" and not result["changes"]
    elif "id" in changes:
        assert [r["change"] for r in result["changes"]] == ["missing", "added"]
    else:
        assert result["changes"] == [
            {
                "action_id": "a",
                "change": "changed",
                "before": original,
                "after": revised,
                "changed_fields": sorted(changes),
            }
        ]
    assert all(Path(p).read_bytes() == raw for p, raw in saved.items())


def test_missing_dividend_is_not_a_cancellation(tmp_path):
    from trader.corporate_actions import compare_cash_dividends

    before, _ = ordinary_capture(tmp_path / "old")
    after = tmp_path / "empty"
    p = provider(lambda _: httpx.Response(200, json={"corporate_actions": {}, "next_page_token": None}))
    capture_actions(p, "SPY", "2026-10-01", "2026-10-31", after)
    p.client.close()
    result = compare_cash_dividends(before, after)
    assert result["changes"][0]["change"] == "missing"
    assert result["changes"][0]["after"] is None
    assert result["status"] == "revision_review_required"


@pytest.mark.parametrize("fault", ["window", "time", "corruption", "unsupported"])
def test_dividend_comparison_rejects_unusable_replacement(tmp_path, fault):
    from trader.corporate_actions import compare_cash_dividends

    before, _ = ordinary_capture(tmp_path / "old")
    after, _ = ordinary_capture(tmp_path / "new", **({"special": True} if fault == "unsupported" else {}))
    manifest = json.loads((after / "manifest.json").read_text())
    if fault == "window":
        request = json.loads((after / "request.json").read_text())
        request["params"]["start"] = "2026-09-01"
        manifest["params"] = request["params"]
        (after / "request.json").write_text(json.dumps(request))
    elif fault == "time":
        request = json.loads((after / "request.json").read_text())
        request["started_at"] = "2020-01-01T00:00:00+00:00"
        manifest["started_at"] = request["started_at"]
        manifest["finished_at"] = "2020-01-01T00:01:00+00:00"
        (after / "request.json").write_text(json.dumps(request))
    elif fault == "corruption":
        (after / "page-001.json").write_text("{}")
    (after / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        compare_cash_dividends(before, after)


def test_dividend_comparison_cli_is_offline(tmp_path, monkeypatch, capsys):
    from trader import cli

    before, _ = ordinary_capture(tmp_path / "old")
    after, _ = ordinary_capture(tmp_path / "new", rate="2")
    monkeypatch.setattr(cli.Settings, "load", lambda *a: pytest.fail("Configuration read"))
    monkeypatch.setattr(cli, "AlpacaData", lambda *a: pytest.fail("Network client"))
    monkeypatch.setattr(cli, "Store", lambda *a: pytest.fail("Ledger opened"))
    assert cli.main(["compare-cash-dividends", str(before), str(after)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "revision_review_required"
