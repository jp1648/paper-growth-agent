import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from trader.config import Security, Settings
from trader.models import now
from trader.robinhood import RobinhoodResearch
from trader.robinhood_mcp import (
    ENDPOINT,
    MAX_BYTES,
    ResearchTransport,
    RobinhoodClient,
    RobinhoodUnavailable,
    TokenFile,
    validate_schema,
)
from trader.store import Store

SCHEMA = {
    "type": "object",
    "properties": {"symbol": {"type": "string"}},
    "required": ["symbol"],
    "additionalProperties": False,
}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.failure = False

    def run(self, name=None, arguments=None):
        self.calls.append((name, arguments))
        if name is None:
            return {"get_financials": SCHEMA, "get_sec_filing": SCHEMA}
        if self.failure:
            raise RobinhoodUnavailable("provider failure")
        return {
            "response": {"content": [{"type": "text", "text": "Vendor facts with units and dates"}]},
            "schema_sha256": "schema",
        }


@pytest.fixture
def adapter(tmp_path):
    cfg = Settings(
        database=str(tmp_path / "ledger.sqlite"),
        robinhood_enabled=True,
        universe={"AAPL": Security(cik="320193", sector="Technology")},
    )
    return RobinhoodResearch(cfg, Store(cfg.database), FakeClient())


def test_cache_preserves_original_and_limits_calls(adapter):
    first = adapter.fetch("AAPL", "get_financials", {"symbol": "AAPL"})
    assert first["state"] == "received"
    second = adapter.fetch("AAPL", "get_financials", {"symbol": "AAPL"})
    assert second["cached"] and first["sources"] == second["sources"]
    assert len(adapter.client.calls) == 2  # catalog plus one data call
    operation = adapter.store.get(first["sources"][0]["id"], "robinhood_operation", scope="global")
    assert operation["cost_usd"] is None and operation["state"] == "received"
    assert first["sources"][0]["content"]["raw_sha256"]
    assert adapter.store.get("blocked", "robinhood_control", scope="global")["state"] == "cleared"


def test_ambiguous_failure_fences_restart_without_duplicate(adapter):
    adapter.client.failure = True
    assert adapter.fetch("AAPL", "get_financials", {"symbol": "AAPL"})["state"] == "unavailable"
    calls = len(adapter.client.calls)
    again = RobinhoodResearch(adapter.cfg, Store(adapter.cfg.database), adapter.client)
    assert "unknown" in again.fetch("AAPL", "get_sec_filing", {"symbol": "AAPL"})["reason"]
    assert len(adapter.client.calls) == calls
    assert adapter.store.get("blocked", "robinhood_control", scope="global")["state"] == "unknown"


def test_disabled_missing_auth_bad_arguments_and_daily_limit(adapter, tmp_path):
    adapter.cfg.robinhood_enabled = False
    assert adapter.fetch("AAPL", "get_financials", {})["reason"] == "disabled"
    assert adapter.client.calls == []
    adapter.cfg.robinhood_enabled = True
    assert "arguments" in adapter.fetch("AAPL", "get_financials", {})["reason"]
    assert not adapter.store.get("blocked", "robinhood_control", scope="global")
    adapter.cfg.robinhood_daily_calls = 0
    assert "ceiling" in adapter.fetch("AAPL", "get_financials", {})["reason"]
    with pytest.raises(RobinhoodUnavailable, match="sign-in"):
        RobinhoodClient(tmp_path / "missing.json").run()


@pytest.mark.parametrize(
    "name", ["place_order", "get_accounts", "transfer_cash", "get_portfolio", "get_equity_quotes"]
)
def test_forbidden_tools_cannot_reach_client(adapter, name):
    with pytest.raises(ValueError):
        adapter.fetch("AAPL", name, {})
    assert adapter.client.calls == []


def test_unconfigured_company_and_oversized_arguments_denied(adapter):
    with pytest.raises(ValueError):
        adapter.fetch("UNKNOWN", "get_financials", {})
    with pytest.raises(ValueError):
        adapter.fetch("AAPL", "get_financials", {"x": "x" * 9000})
    assert adapter.client.calls == []


def test_stale_cache_is_not_reused(adapter):
    first = adapter.fetch("AAPL", "get_financials", {"symbol": "AAPL"})
    row = adapter.store.db.execute("SELECT key,body FROM documents WHERE kind='robinhood_cache'").fetchone()
    cached = json.loads(row[1])
    cached["retrieved_at"] = (now() - timedelta(days=1)).isoformat()
    adapter.store.put(row[0], cached, "robinhood_cache", scope="global")
    second = adapter.fetch("AAPL", "get_financials", {"symbol": "AAPL"})
    assert first["sources"][0]["id"] != second["sources"][0]["id"]
    assert adapter.store.get(first["sources"][0]["id"], "robinhood_operation", scope="global")


@pytest.mark.parametrize(
    "schema", [{"$ref": "https://evil.test/schema"}, {"properties": {"x": {"$ref": "file:///etc/passwd"}}}]
)
def test_remote_schema_resolution_forbidden(schema):
    with pytest.raises(RobinhoodUnavailable):
        validate_schema(schema)


def test_private_token_storage_rejects_symlink_and_public_file(tmp_path):
    file = tmp_path / "auth.json"
    file.write_text("{}")
    file.chmod(0o644)
    with pytest.raises(RobinhoodUnavailable):
        TokenFile(file).read()
    file.chmod(0o600)
    assert TokenFile(file).read() == {}
    link = tmp_path / "link"
    link.symlink_to(file)
    with pytest.raises(OSError):
        TokenFile(link).read()


@pytest.mark.parametrize(
    "url,method,payload",
    [
        ("https://evil.test/mcp", "POST", {}),
        ("https://api.robinhood.com/orders/", "POST", {}),
        (ENDPOINT, "POST", {"method": "resources/read"}),
        (ENDPOINT, "POST", {"method": "tools/call", "params": {"name": "place_order"}}),
        (ENDPOINT + "?bypass=1", "POST", {"method": "tools/call", "params": {"name": "get_accounts"}}),
    ],
)
def test_wire_allowlist_blocks_before_network(url, method, payload):
    calls = []

    async def test():
        async with httpx.AsyncClient(
            transport=ResearchTransport(httpx.MockTransport(lambda request: calls.append(request)))
        ) as client:
            with pytest.raises(RobinhoodUnavailable):
                await client.request(method, url, json=payload)

    asyncio.run(test())
    assert calls == []


def test_transport_bounds_response():
    async def test():
        async with httpx.AsyncClient(
            transport=ResearchTransport(
                httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (MAX_BYTES + 1)))
            )
        ) as client:
            with pytest.raises(RobinhoodUnavailable):
                await client.get(ENDPOINT)

    asyncio.run(test())


def test_real_sdk_initialize_catalog_and_call_contract(monkeypatch, tmp_path):
    import trader.robinhood_mcp as module

    calls = []

    def handle(request):
        assert str(request.url) == ENDPOINT
        if request.method in {"GET", "DELETE"}:
            return httpx.Response(405)
        message = json.loads(request.content)
        method = message["method"]
        calls.append(message)
        if method.startswith("notifications/"):
            return httpx.Response(202)
        result = {
            "initialize": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            },
            "tools/list": {
                "tools": [
                    {"name": "get_financials", "inputSchema": SCHEMA},
                    {"name": "place_order", "inputSchema": {"type": "object"}},
                ]
            },
            "tools/call": {"content": [{"type": "text", "text": "Financial data"}]},
        }[method]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    original = module.ResearchTransport
    monkeypatch.setattr(module, "ResearchTransport", lambda: original(httpx.MockTransport(handle)))
    monkeypatch.setattr(module, "OAuthClientProvider", lambda **kwargs: None)
    original_session = module.ClientSession
    timeouts = []

    def session(*args, **kwargs):
        timeouts.append(kwargs["read_timeout_seconds"].total_seconds())
        return original_session(*args, **kwargs)

    monkeypatch.setattr(module, "ClientSession", session)
    file = tmp_path / "auth.json"
    file.write_text('{"tokens":{"access_token":"test-only"}}')
    file.chmod(0o600)
    client = RobinhoodClient(file)
    assert list(client.run()) == ["get_financials"]

    async def interactive(_url):
        pass

    assert list(asyncio.run(client.exchange(redirect=interactive))) == ["get_financials"]
    assert timeouts[:2] == [30, 330]  # Human authorization must not inherit the runtime timeout.
    assert (
        client.run("get_financials", {"symbol": "AAPL"})["response"]["content"][0]["text"] == "Financial data"
    )
    with pytest.raises(RobinhoodUnavailable):
        client.run("place_order", {})
    assert [c["params"]["name"] for c in calls if c["method"] == "tools/call"] == ["get_financials"]


def test_research_desk_fallback_and_original_source_preservation(adapter):
    from trader.evidence import EvidenceProvider
    from trader.research_team import Budget, ResearchDesk

    class Provider(EvidenceProvider):
        def research_data(self, *args):
            return {"state": "unavailable", "reason": "auth", "sources": []}

    evidence = {
        "symbol": "AAPL",
        "sources": [{"id": "sec-original", "source": "SEC EDGAR", "content": {"facts": {}}}],
    }
    desk = ResearchDesk(
        adapter.cfg,
        Provider(adapter.cfg, adapter.store, None),
        {"symbol": "AAPL", "portfolio": {}, "run_key": "fixture"},
        evidence,
        Budget(adapter.cfg),
        None,
    )
    tools = {t.name: t for t in desk.tools()}
    result = tools["research_data"].invoke(
        {"symbol": "AAPL", "capability": "get_financials", "arguments_json": '{"symbol":"AAPL"}'}
    )
    assert result["state"] == "fallback" and result["reason"] == "auth"
    assert list(desk.sources) == ["sec-original"]
    result = tools["research_data"].invoke(
        {"symbol": "AAPL", "capability": "get_equity_analyst_ratings", "arguments_json": "{}"}
    )
    assert result["state"] == "unavailable" and not result["sources"]
    desk.evaluation_at = now()
    assert tools["research_data_catalog"].invoke({})["state"] == "unavailable"


def test_oauth_diagnostics_never_log_tokens(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        try:
            raise ValueError("refresh_token=fixture-sensitive-value")
        except ValueError:
            logging.getLogger("mcp.client.auth.oauth2").exception(
                "token response: %s", "fixture-sensitive-value"
            )
    assert "fixture-sensitive-value" not in caplog.text
    assert "withheld" in caplog.text


def test_symbol_arguments_cannot_silently_switch_company(adapter):
    with pytest.raises(ValueError):
        adapter.fetch("AAPL", "get_financials", {"symbol": "MSFT"})
    assert adapter.client.calls == []


def test_token_refresh_storage_is_private_atomic_and_not_ledger(tmp_path):
    from mcp.shared.auth import OAuthToken

    storage = TokenFile(tmp_path / "tokens.json")
    token = OAuthToken(access_token="fixture-access", token_type="Bearer", refresh_token="fixture-refresh")
    asyncio.run(storage.set_tokens(token))
    assert storage.path.stat().st_mode & 0o777 == 0o600
    assert asyncio.run(storage.get_tokens()).access_token == "fixture-access"
    assert not list(tmp_path.glob(".robinhood-*"))


def test_empty_provider_result_keeps_unknown_fence(adapter):
    normal = adapter.client.run
    adapter.client.run = lambda name=None, arguments=None: (
        normal() if name is None else {"response": {"content": []}}
    )
    assert adapter.fetch("AAPL", "get_financials", {"symbol": "AAPL"})["state"] == "unavailable"
    assert adapter.store.get("blocked", "robinhood_control", scope="global")["state"] == "unknown"


@pytest.mark.parametrize(
    "path,reason",
    [
        ("/callback?error=access_denied&state=fixture", "access_denied"),
        ("/callback?error=invalid_scope", "invalid_scope"),
        ("/callback?error=secret-untrusted-provider-text", "provider_error"),
        ("/callback?code=fixture", "missing_code_or_state"),
        ("/callback?code=fixture&state=a&state=b", "missing_code_or_state"),
    ],
)
def test_callback_errors_are_specific_and_never_echo_credentials(path, reason):
    from trader.robinhood_connect import parse_callback

    with pytest.raises(RobinhoodUnavailable, match=reason) as caught:
        parse_callback(path)
    assert "fixture" not in str(caught.value)
    assert "secret-untrusted" not in str(caught.value)


def test_valid_callback_passes_code_and_state_only_to_sdk():
    from trader.robinhood_connect import parse_callback

    assert parse_callback("/callback?code=fixture-code&state=fixture-state") == {
        "code": "fixture-code",
        "state": "fixture-state",
    }


def test_evidence_adapter_uses_thread_local_ledger_connection(adapter, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from trader.evidence import EvidenceProvider

    monkeypatch.setattr("trader.robinhood.RobinhoodClient", lambda _path: FakeClient())
    provider = EvidenceProvider(adapter.cfg, adapter.store, None)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(provider.research_data, "AAPL", "get_financials", {"symbol": "AAPL"}).result()
    assert result["state"] == "received"
    assert (
        adapter.store.get(result["sources"][0]["id"], "robinhood_operation", scope="global")["state"]
        == "received"
    )


@pytest.mark.parametrize("structured", [True, False])
def test_large_robinhood_source_preview_preserves_paged_original(adapter, structured):
    import copy
    import hashlib

    from trader.models import dumps
    from trader.research_team import Budget, ResearchDesk

    # A nested Unicode request and a response repeated in both MCP representations
    # must not be copied into every specialist/lead prompt or tool reply.
    body = {"data": {"results": [{"symbol": "AAPL", "detail": '证据\\"' * 30_000}]}}
    response = {"content": [{"type": "text", "text": dumps(body)}]}
    if structured:
        response["structuredContent"] = body
    source = {
        "id": "robinhood-test-original",
        "source": "Robinhood MCP (vendor research)",
        "url": "https://agent.robinhood.com/mcp/trading",
        "retrieved_at": "2026-11-25T22:00:00+00:00",
        "content": {
            "requested_symbol": "AAPL",
            "tool": "get_financials",
            "arguments": {"nested": {"filter": "证据" * 1_000}},
            "response": response,
            "schema_sha256": "a" * 64,
            "raw_sha256": hashlib.sha256(dumps(response).encode()).hexdigest(),
            "coverage": "Untrusted vendor data; original source required.",
        },
    }
    original = copy.deepcopy(source)
    desk = ResearchDesk(
        adapter.cfg,
        None,
        {"symbol": "AAPL", "portfolio": {}},
        {"symbol": "AAPL", "sources": [source]},
        Budget(adapter.cfg),
        None,
    )
    preview = desk.source_previews(desk.sources.values())[0]
    assert len(dumps(preview).encode()) < 2500
    assert preview["id"] == source["id"]
    assert preview["content"]["raw_sha256"] == source["content"]["raw_sha256"]
    assert preview["content"]["schema_sha256"] == source["content"]["schema_sha256"]
    assert "response" not in preview["content"] and "arguments" not in preview["content"]
    assert "read_source" in preview["content"]["note"]
    reader = next(t for t in desk.tools() if t.name == "read_source")
    pages, offset = [], 0
    while offset is not None:
        page = reader.invoke({"source_id": source["id"], "offset": offset})
        pages.append(page["content"])
        offset = page["next_offset"]
    assert json.loads("".join(pages)) == source["content"]
    assert source == original and desk.sources[source["id"]] == original
