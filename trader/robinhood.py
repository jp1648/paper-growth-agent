"""Bounded Robinhood research acquisition with durable cache and unknown-call fencing."""

import fcntl
import hashlib
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from .models import dt, dumps, now
from .observability import traced
from .robinhood_mcp import ENDPOINT, TOOLS, RobinhoodClient, RobinhoodUnavailable, validate_schema


class RobinhoodResearch:
    def __init__(self, cfg, store, client=None):
        self.cfg, self.store = cfg, store
        self.client = client or RobinhoodClient(Path(cfg.database).resolve().parent / "robinhood-oauth.json")

    @traced("robinhood.catalog")
    def catalog(self):
        if not self.cfg.robinhood_enabled:
            return {"state": "unavailable", "tools": {}, "gap": "Robinhood research disabled"}
        try:
            result = self.client.run()
            if (
                not isinstance(result, dict)
                or not set(result) <= TOOLS
                or len(dumps(result).encode()) > 64_000
            ):
                raise RobinhoodUnavailable("Invalid catalog")
            for schema in result.values():
                validate_schema(schema)
            return {"state": "available", "tools": result}
        except (RobinhoodUnavailable, OSError, ValueError, TypeError, SchemaError):
            return {
                "state": "unavailable",
                "tools": {},
                "gap": "Robinhood sign-in, entitlement or schema unavailable",
            }

    @traced("robinhood.research")
    def fetch(self, symbol, name, arguments):
        if symbol not in self.cfg.research_universe or name not in TOOLS:
            raise ValueError("Only configured symbols and allowlisted research capabilities are allowed")
        if not isinstance(arguments, dict) or len(dumps(arguments).encode()) > 8000:
            raise ValueError("Research arguments must be a bounded object")
        for key in ("symbol", "ticker", "symbols", "tickers"):
            if key in arguments and arguments[key] not in (symbol, [symbol]):
                raise ValueError("Research argument symbol differs from requested company")
        if "cik" in arguments and str(arguments["cik"]).zfill(10) != self.cfg.research_universe[
            symbol
        ].cik.zfill(10):
            raise ValueError("Research argument CIK differs from requested company")
        if not self.cfg.robinhood_enabled:
            return self.gap("disabled")
        path = self.store.control_path(self.cfg.control_dir, "robinhood-research", ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._fetch(symbol, name, arguments)

    def gap(self, reason):
        return {"state": "unavailable", "reason": reason, "sources": [], "provider": "Robinhood MCP"}

    def _fetch(self, symbol, name, arguments):
        key = hashlib.sha256(
            dumps({"symbol": symbol, "tool": name, "arguments": arguments}).encode()
        ).hexdigest()
        cached = self.store.get(key, "robinhood_cache", scope="global")
        if cached and timedelta(0) <= now() - dt(cached["retrieved_at"]) < timedelta(
            hours=self.cfg.robinhood_cache_hours
        ):
            return {"state": "received", "sources": [cached], "cached": True}
        block = self.store.get("blocked", "robinhood_control", scope="global")
        if block and block.get("state") != "cleared":
            return self.gap("unknown prior request; reconcile before retry")
        day = now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
        counter = self.store.get(day, "robinhood_calls", scope="global") or {"attempts": 0}
        if counter["attempts"] >= self.cfg.robinhood_daily_calls:
            return self.gap("daily request ceiling")
        catalog = self.catalog()
        if name not in catalog["tools"]:
            return self.gap("authentication or capability unavailable")
        try:
            Draft202012Validator(catalog["tools"][name]).validate(arguments)
        except ValidationError:
            return self.gap("arguments do not match authenticated schema; inspect research_data_catalog")
        operation = "robinhood-" + uuid4().hex
        # Write the block first: a crash before/after dispatch must never silently repeat a call.
        self.store.put(
            "blocked", {"operation_id": operation, "state": "unknown"}, "robinhood_control", scope="global"
        )
        self.store.put(day, {"attempts": counter["attempts"] + 1}, "robinhood_calls", scope="global")
        request = {
            "operation_id": operation,
            "symbol": symbol,
            "tool": name,
            "arguments": arguments,
            "state": "unknown",
            "cost_usd": None,
        }
        self.store.put(operation, request, "robinhood_operation", scope="global")
        try:
            result = self.client.run(name, arguments)
            if not isinstance(result, dict) or not isinstance(result.get("response"), dict):
                raise RobinhoodUnavailable("Invalid result")
            raw = result["response"]
            if raw.get("isError") or not (raw.get("content") or raw.get("structuredContent")):
                raise RobinhoodUnavailable("Empty/error result")
            stamp = now().isoformat()
            source = {
                "id": operation,
                "source": "Robinhood MCP (vendor research)",
                "url": ENDPOINT,
                "retrieved_at": stamp,
                "content": {
                    "requested_symbol": symbol,
                    "tool": name,
                    "arguments": arguments,
                    "response": raw,
                    "schema_sha256": result.get("schema_sha256"),
                    "raw_sha256": hashlib.sha256(dumps(raw).encode()).hexdigest(),
                    "coverage": "Untrusted vendor data, not a verified original filing or executable quote. Verify issuer, accession, original URL, publication/reporting date and units against SEC originals. Retrieval time is not publication time; do not infer point-in-time availability. No news/transcript entitlement implied.",
                },
            }
            self.store.put(
                operation,
                {**request, "state": "received", "source": source},
                "robinhood_operation",
                scope="global",
            )
            self.store.put(key, source, "robinhood_cache", scope="global")
            self.store.put(
                "blocked",
                {"state": "cleared", "operation_id": operation},
                "robinhood_control",
                scope="global",
            )
            return {"state": "received", "sources": [source], "cached": False}
        except (RobinhoodUnavailable, OSError, ValueError, TypeError):
            # Preserve the unknown operation/block; fallbacks never erase accounting uncertainty.
            return self.gap("failed or ambiguous request; provider usage requires reconciliation")
