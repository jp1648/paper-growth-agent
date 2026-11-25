"""Fixed-origin, research-only MCP client. Never exposes brokerage/account tools."""

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import stat
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.types import PaginatedRequestParams

ENDPOINT = "https://agent.robinhood.com/mcp/trading"
TOOLS = frozenset(
    {
        "get_sec_filing_index",
        "get_sec_filing",
        "get_sec_filing_facts",
        "get_sec_filing_facts_catalog",
        "get_financials",
        "get_earnings_calendar",
        "get_earnings_results",
        "get_equity_fundamentals",
        "get_equity_analyst_ratings",
    }
)
MAX_BYTES = 2_000_000


class CredentialSafeLog(logging.Filter):
    def filter(self, record):
        # SDK validation errors may embed token response bodies. Keep severity/event,
        # but only our sanitized acquisition records may carry response details.
        record.msg = "Robinhood MCP transport/auth diagnostic (credential-bearing details withheld)"
        record.args = ()
        record.exc_info = record.exc_text = record.stack_info = None
        return True


for _logger in ("mcp.client.auth.oauth2", "mcp.client.streamable_http"):
    logging.getLogger(_logger).addFilter(CredentialSafeLog())


class RobinhoodUnavailable(ValueError):
    pass


class TokenFile:
    """Private OAuth state, deliberately separate from traces and the ledger."""

    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 100_000:
                raise RobinhoodUnavailable("OAuth file must be private (0600), regular and bounded")
            return json.load(stream)

    def write(self, key, value):
        data = self.read()
        data[key] = value.model_dump(mode="json")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".robinhood-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def get_tokens(self):
        value = self.read().get("tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, tokens):
        self.write("tokens", tokens)

    async def get_client_info(self):
        value = self.read().get("client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info):
        self.write("client", client_info)


def validate_schema(schema):
    if len(json.dumps(schema).encode()) > 40_000:
        raise RobinhoodUnavailable("Schema exceeds limit")

    # jsonschema must never fetch remote references supplied by a server.
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(item, str) or not item.startswith("#")):
                    raise RobinhoodUnavailable("External schema reference denied")
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)

    inspect(schema)
    Draft202012Validator.check_schema(schema)


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > MAX_BYTES:
                raise RobinhoodUnavailable("Response exceeds limit")
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class ResearchTransport(httpx.AsyncBaseTransport):
    """Enforce the allowlist at the wire, including SDK-generated requests."""

    def __init__(self, transport=None):
        self.transport = transport or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        url = urlsplit(str(request.url))
        if url.scheme != "https" or url.port not in {None, 443} or url.username or url.password:
            raise RobinhoodUnavailable("Untrusted MCP/OAuth origin")
        if url.hostname not in {"agent.robinhood.com", "api.robinhood.com"}:
            raise RobinhoodUnavailable("Untrusted MCP/OAuth origin")
        if url.hostname == "api.robinhood.com" and url.path != "/oauth2/token/":
            raise RobinhoodUnavailable("Only OAuth token exchange allowed")
        if str(request.url) == ENDPOINT and request.method == "POST":
            payload = json.loads(await request.aread())
            method = payload.get("method")
            if method not in {
                "initialize",
                "notifications/initialized",
                "notifications/cancelled",
                "tools/list",
                "tools/call",
                "ping",
            }:
                raise RobinhoodUnavailable("MCP method denied")
            if method == "tools/call" and payload.get("params", {}).get("name") not in TOOLS:
                raise RobinhoodUnavailable("MCP tool denied")
        elif url.hostname == "agent.robinhood.com" and not (
            (url.path.startswith("/.well-known/") and request.method == "GET")
            or (url.path == "/oauth/trading/register" and request.method == "POST")
            or (str(request.url) == ENDPOINT and request.method in {"GET", "DELETE"})
        ):
            raise RobinhoodUnavailable("MCP/OAuth route denied")
        request.headers["Accept-Encoding"] = "identity"
        response = await self.transport.handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise RobinhoodUnavailable("Compressed MCP response denied")
        if response.is_stream_consumed and len(response.content) > MAX_BYTES:
            await response.aclose()
            raise RobinhoodUnavailable("Response exceeds limit")
        response.stream = BoundedStream(response.stream)
        return response

    async def aclose(self):
        await self.transport.aclose()


async def no_interactive_auth(*args):
    raise RobinhoodUnavailable("Robinhood sign-in required; run the connection command")


class RobinhoodClient:
    def __init__(self, token_path):
        self.tokens = TokenFile(token_path)

    async def exchange(self, name=None, arguments=None, *, redirect=None, callback=None):
        auth = OAuthClientProvider(
            server_url=ENDPOINT,
            client_metadata=OAuthClientMetadata(
                client_name="Trader research only",
                redirect_uris=["http://127.0.0.1:18765/callback"],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="none",
                scope="internal",
            ),
            storage=self.tokens,
            redirect_handler=redirect or no_interactive_auth,
            callback_handler=callback or no_interactive_auth,
        )
        async with (
            httpx.AsyncClient(
                auth=auth, transport=ResearchTransport(), timeout=30, follow_redirects=False
            ) as http,
            streamable_http_client(ENDPOINT, http_client=http) as (read, write, _),
            ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=330 if redirect else 30)
            ) as session,
        ):
            await session.initialize()
            catalog, cursor = {}, None
            for _ in range(5):
                page = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
                for tool in page.tools:
                    if tool.name in TOOLS:
                        if tool.name in catalog:
                            raise RobinhoodUnavailable("Duplicate tool name")
                        validate_schema(tool.inputSchema)
                        catalog[tool.name] = tool.inputSchema
                cursor = page.nextCursor
                if not cursor:
                    break
            else:
                raise RobinhoodUnavailable("Tool catalog pagination limit")
            if name is None:
                return catalog
            if name not in TOOLS or name not in catalog:
                raise RobinhoodUnavailable("Research tool unavailable")
            Draft202012Validator(catalog[name]).validate(arguments)
            result = await session.call_tool(name, arguments)
            if result.isError or not (result.content or result.structuredContent):
                raise RobinhoodUnavailable("Research response unavailable")
            raw = result.model_dump(mode="json")
            if len(json.dumps(raw).encode()) > MAX_BYTES:
                raise RobinhoodUnavailable("Research result exceeds limit")
            return {
                "response": raw,
                "schema_sha256": hashlib.sha256(
                    json.dumps(catalog[name], sort_keys=True).encode()
                ).hexdigest(),
            }

    def run(self, name=None, arguments=None):
        if name is not None and name not in TOOLS:
            raise RobinhoodUnavailable("Research tool denied")
        if not self.tokens.read().get("tokens"):
            raise RobinhoodUnavailable("Robinhood sign-in required")
        lock_path = self.tokens.path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                return asyncio.run(asyncio.wait_for(self.exchange(name, arguments), timeout=60))
            except Exception:  # noqa: BLE001 - sanitize SDK/OAuth exception groups at credential boundary
                # SDK/OAuth exceptions can contain credentials or request URLs.
                raise RobinhoodUnavailable("Robinhood auth, transport or schema unavailable") from None
