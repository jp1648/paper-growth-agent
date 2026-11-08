import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from trader.price_capture import capture_prices


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.price_capture.now", lambda: datetime(2026, 11, 8, tzinfo=UTC))
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            [
                {"date": "2026-10-30", "open": "09:30", "close": "16:00"},
                {"date": "2026-11-02", "open": "09:30", "close": "16:00"},
            ]
        )
    )
    return path


def provider(handler):
    return SimpleNamespace(
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), headers={}
    )


def bars():
    return [{"t": "2026-10-30T04:00:00Z", "c": "100"}, {"t": "2026-11-02T05:00:00Z", "c": "101"}]


def test_pages_dst_matrix_immutable_and_no_repeated_capture(calendar, tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and str(request.url).startswith(
            "https://data.alpaca.markets/v2/stocks/bars?"
        )
        q = request.url.params
        assert q["adjustment"] == "raw" and q["feed"] == "sip" and q["currency"] == "USD"
        assert q["asof"] == "2026-11-02" and q["start"].endswith("-04:00") and q["end"].endswith("-05:00")
        if len(calls) == 2:
            assert q["page_token"] == "next"
        return httpx.Response(
            200,
            json={
                "bars": {"SPY" if len(calls) == 1 else "QQQ": bars()},
                "next_page_token": "next" if len(calls) == 1 else None,
            },
        )

    p = provider(handler)
    root = tmp_path / "capture"
    result = capture_prices(p, ["SPY", "QQQ"], calendar, root)
    assert result["status"] == "complete_requested_matrix" and result["unique_marks"] == 4
    assert not result["trial_acceptance"] and not result["calendar_authenticity_verified"]
    assert (root / "calendar.json").read_bytes() == calendar.read_bytes()
    for page in result["pages"]:
        assert hashlib.sha256((root / page["file"]).read_bytes()).hexdigest() == page["sha256"]
    with pytest.raises(FileExistsError):
        capture_prices(p, ["SPY", "QQQ"], calendar, root)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "conflict",
        "cycle",
        "cursor",
        "symbol",
        "time",
        "naive",
        "nan",
        "negative",
        "boolean",
        "redirect",
        "bytes",
        "page_bound",
    ],
)
def test_failure_retains_manifest_not_complete(calendar, tmp_path, monkeypatch, fault):
    monkeypatch.setattr("trader.price_capture.MAX_PAGES", 2)
    monkeypatch.setattr("trader.price_capture.MAX_PAGE_BYTES", 1024)
    calls = []

    def handler(request):
        calls.append(request)
        records = bars()
        page = {"bars": {"SPY": records}, "next_page_token": None}
        if fault == "missing":
            records.pop()
        elif fault == "conflict":
            records.append(dict(records[0], c="999"))
        elif fault == "cycle":
            page["next_page_token"] = "same"
        elif fault == "cursor":
            del page["next_page_token"]
        elif fault == "symbol":
            page["bars"]["BAD"] = page["bars"].pop("SPY")
        elif fault == "time":
            records[0]["t"] = "2026-10-30T05:00:00Z"
        elif fault == "naive":
            records[0]["t"] = "2026-10-30T00:00:00"
        elif fault in ["nan", "negative", "boolean"]:
            records[0]["c"] = {"nan": "NaN", "negative": "-1", "boolean": True}[fault]
        elif fault == "redirect":
            return httpx.Response(302, headers={"location": "https://example.com"})
        elif fault == "bytes":
            return httpx.Response(200, content=b" " * 1025)
        elif fault == "page_bound":
            page["next_page_token"] = str(len(calls))
        return httpx.Response(200, json=page)

    root = tmp_path / "capture"
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        capture_prices(provider(handler), ["SPY"], calendar, root)
    assert len(calls) <= 2
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["error"] and not manifest["trial_acceptance"]


def test_cli_never_constructs_broker_or_ledger(calendar, tmp_path, monkeypatch, capsys):
    from trader import cli

    p = provider(lambda r: httpx.Response(200, json={"bars": {"SPY": bars()}, "next_page_token": None}))
    monkeypatch.setattr(cli, "AlpacaData", lambda cfg: p)
    monkeypatch.setattr(cli.Settings, "load", lambda path: None)
    for name in ["AlpacaBroker", "Store", "Runner", "Service"]:
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("Execution path forbidden"))
    assert (
        cli.main(
            [
                "capture-daily-prices",
                "SPY",
                "--calendar",
                str(calendar),
                "--output-dir",
                str(tmp_path / "capture"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "complete_requested_matrix"
    assert p.client.is_closed


@pytest.mark.parametrize(
    "fault", ["future", "duplicate", "reversed", "bad_hours", "bad_symbol", "duplicate_symbol"]
)
def test_invalid_request_never_contacts_provider(calendar, tmp_path, fault):
    days = json.loads(calendar.read_text())
    symbols = ["SPY"]
    if fault == "future":
        days[-1]["date"] = "2026-11-08"
    elif fault == "duplicate":
        days.append(days[-1])
    elif fault == "reversed":
        days.reverse()
    elif fault == "bad_hours":
        days[-1]["close"] = "09:00"
    elif fault == "bad_symbol":
        symbols = ["../SPY"]
    else:
        symbols = ["SPY", "SPY"]
    calendar.write_text(json.dumps(days))
    p = provider(lambda request: pytest.fail("Invalid request reached provider"))
    with pytest.raises(ValueError):
        capture_prices(p, symbols, calendar, tmp_path / "capture")
    assert not (tmp_path / "capture").exists()


@pytest.fixture
def captured(calendar, tmp_path):
    from trader.price_capture import validate_prices

    root = tmp_path / "retained"
    capture_prices(
        provider(lambda r: httpx.Response(200, json={"bars": {"SPY": bars()}, "next_page_token": None})),
        ["SPY"],
        calendar,
        root,
    )
    assert len(validate_prices(root)["marks"]) == 2
    return root


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "calendar",
        "request",
        "path",
        "symlink",
        "failed",
        "count",
        "cursor",
        "coverage",
        "policy",
        "time",
    ],
)
def test_offline_reader_rejects_corrupt_or_incomplete_capture(captured, fault):
    from trader.price_capture import validate_prices

    mpath = captured / "manifest.json"
    manifest = json.loads(mpath.read_text())
    page = captured / "page-001.json"
    if fault == "hash":
        page.write_text(page.read_text() + " ")
    elif fault == "calendar":
        (captured / "calendar.json").write_text("[]")
    elif fault == "request":
        (captured / "request.json").write_text("{}")
    elif fault == "path":
        manifest["pages"][0]["file"] = "../outside.json"
    elif fault == "symlink":
        target = captured / "original.json"
        page.rename(target)
        page.symlink_to(target)
    elif fault == "failed":
        manifest["status"] = "failed"
    elif fault == "count":
        manifest["unique_marks"] = 3
    elif fault == "time":
        manifest["finished_at"] = "2020-01-01T00:00:00Z"
    elif fault == "policy":
        manifest["params"]["adjustment"] = "split"
        request = json.loads((captured / "request.json").read_text())
        request["params"] = manifest["params"]
        (captured / "request.json").write_text(json.dumps(request))
    else:
        body = json.loads(page.read_text())
        if fault == "cursor":
            body["next_page_token"] = "remaining"
        else:
            body["bars"]["SPY"].pop()
        page.write_text(json.dumps(body))
        raw = page.read_bytes()
        manifest["pages"][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    mpath.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        validate_prices(captured)


def test_revision_comparison_retains_original_and_separates_price_from_volume(captured, calendar, tmp_path):
    from trader.price_capture import compare_prices

    original = {p.name: p.read_bytes() for p in captured.iterdir()}
    revised = bars()
    revised[0]["c"] = "99"
    revised[1]["v"] = 50
    new = tmp_path / "new"
    capture_prices(
        provider(lambda r: httpx.Response(200, json={"bars": {"SPY": revised}, "next_page_token": None})),
        ["SPY"],
        calendar,
        new,
    )
    result = compare_prices(captured, new)
    assert result["status"] == "revisions_require_review"
    assert [r["close_changed"] for r in result["changes"]] == [True, False]
    assert {p.name: p.read_bytes() for p in captured.iterdir()} == original
    assert compare_prices(captured, captured)["changes"] == []
    other = tmp_path / "other"
    capture_prices(
        provider(lambda r: httpx.Response(200, json={"bars": {"QQQ": bars()}, "next_page_token": None})),
        ["QQQ"],
        calendar,
        other,
    )
    with pytest.raises(ValueError, match="identical"):
        compare_prices(captured, other)


def test_offline_price_cli_has_no_configuration_or_provider(captured, monkeypatch, capsys):
    from trader import cli

    monkeypatch.setattr(cli.Settings, "load", lambda p: pytest.fail("No configuration needed"))
    monkeypatch.setattr(cli, "AlpacaData", lambda p: pytest.fail("No provider allowed"))
    assert cli.main(["validate-daily-prices", str(captured)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "retained_matrix_validated"
    assert cli.main(["compare-daily-prices", str(captured), str(captured)]) == 0
    assert json.loads(capsys.readouterr().out)["changes"] == []
