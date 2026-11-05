import json

import httpx
import pytest

from trader.config import Security, Settings
from trader.observability import clean
from trader.store import Store
from trader.x_api import XDiscovery


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "private-fixture-token")
    cfg = Settings(
        x_enabled=True,
        database=str(tmp_path / "ledger.sqlite"),
        control_dir=str(tmp_path / "control"),
        universe={"AAPL": Security(sector="Tech", cik="1")},
    )
    return cfg, Store(cfg.database)


def test_direct_search_cached_with_citations_and_usage(setup):
    cfg, store = setup
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url).startswith("https://api.x.com/2/tweets/search/recent?")
        assert request.headers["authorization"] == "Bearer private-fixture-token"
        assert request.url.params["max_results"] == "10"
        assert "next_token" not in request.url.params
        return httpx.Response(
            200,
            json={
                "data": [{"id": "123", "text": "Unverified claim", "created_at": "2026-11-05T12:00:00Z"}],
                "meta": {"result_count": 1},
            },
        )

    adapter = XDiscovery(cfg, store, httpx.Client(transport=httpx.MockTransport(handle)))
    first = adapter.snapshot("AAPL")
    assert len(calls) == 2
    assert all(s["state"] == "received" for s in first)
    assert first[0]["content"]["response"]["data"][0]["url"] == "https://x.com/i/web/status/123"
    assert XDiscovery(cfg, Store(store.path), adapter.client).snapshot("AAPL") == first
    assert len(calls) == 2
    assert store.events("x_result")[0]["body"]["cost_usd"] is None
    assert clean({"X_BEARER_TOKEN": "private-fixture-token", "message": "private-fixture-token"}) == {
        "X_BEARER_TOKEN": "[REDACTED]",
        "message": "[REDACTED]",
    }


@pytest.mark.parametrize("failure", ["timeout", "429", "redirect", "invalid", "partial"])
def test_failures_block_all_future_calls(setup, failure):
    cfg, store = setup
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-fixture-token")
        if failure == "429":
            return httpx.Response(429)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://example.org"})
        if failure == "partial":
            return httpx.Response(200, json={"errors": [{"detail": "missing"}], "meta": {}})
        return httpx.Response(200, json={"data": [{"id": "oops"}], "meta": {}})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    result = XDiscovery(cfg, store, client).snapshot("AAPL")
    assert result[0]["state"] == "unknown"
    assert result[1]["state"] == "unavailable"
    XDiscovery(cfg, Store(store.path), client).snapshot("AAPL")
    assert len(calls) == 1
    assert "private-fixture-token" not in json.dumps(store.items("x_discovery"))


def test_crash_reservation_and_daily_ceiling(setup):
    cfg, store = setup
    store.put("AAPL:discussion", {"state": "unknown", "retrieved_at": "2026-11-01T00:00:00Z"}, "x_discovery")

    def handle(request):
        pytest.fail("Must not call X")

    client = httpx.Client(transport=httpx.MockTransport(handle))
    assert XDiscovery(cfg, store, client).snapshot("AAPL")[1]["state"] == "unavailable"
    with pytest.raises(ValueError):
        XDiscovery(cfg, store, client).snapshot("AAPL OR bad")
    cfg.x_enabled = False
    assert XDiscovery(cfg, store, client).snapshot("AAPL") == []


def test_zero_budget_and_missing_token_do_not_submit(setup, monkeypatch):
    cfg, store = setup
    cfg.x_daily_calls = 0
    assert all(s["state"] == "unavailable" for s in XDiscovery(cfg, store).snapshot("AAPL"))
    assert not store.events("x_attempt")
    cfg.x_daily_calls = 6
    monkeypatch.delenv("X_BEARER_TOKEN")
    assert all(s["state"] == "unavailable" for s in XDiscovery(cfg, store).snapshot("AAPL"))
    assert not store.events("x_attempt")
