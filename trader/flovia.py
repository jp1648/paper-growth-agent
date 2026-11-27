"""Bounded public X evidence; never a price feed or an execution signal."""

import fcntl
import json
import os
import re
from datetime import timedelta
from uuid import uuid4

import httpx

from .models import dec, dt, now
from .observability import traced


class FloviaX:
    def __init__(self, cfg, store, client=None):
        self.cfg, self.store = cfg, store
        self.client = client

    @traced("flovia.call")
    def call(self, name, arguments):
        key = os.environ.get("FLOVIA_KEY")
        if not key:
            raise ValueError("FLOVIA_KEY missing")

        def request(client):
            with client.stream(
                "POST",
                "https://api.flovia.dev/mcp",
                headers={"Authorization": "Bearer " + key, "Accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 250_000:
                        raise ValueError("Flovia response too large")
                    chunks.append(chunk)
                raw = b"".join(chunks).decode()
            if raw.lstrip().startswith("{"):
                envelope = json.loads(raw)
            else:
                events = [
                    json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith("data:")
                ]
                envelope = next(e for e in events if e.get("id") == 1)
            result = envelope["result"]
            value = json.loads(next(c["text"] for c in result["content"] if c["type"] == "text"))
            if not isinstance(value, dict):
                raise TypeError("Invalid Flovia response")
            if result.get("isError") and not isinstance(value.get("explanation"), dict):
                raise ValueError("Flovia tool failed")
            return value

        if self.client is not None:
            return request(self.client)
        with httpx.Client(timeout=30) as client:
            return request(client)

    @traced("flovia.snapshot")
    def snapshot(self, symbol):
        if symbol not in self.cfg.research_universe or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Invalid research symbol")
        if not self.cfg.flovia_x_enabled:
            return []
        path = self.store.control_path(self.cfg.control_dir, "flovia-x", ".lock")
        with path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return [self._search(symbol, p) for p in ("discussion", "contrary")]
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _search(self, symbol, perspective):
        key = symbol + ":" + perspective
        previous = self.store.get(key, "flovia_x")
        if previous and (
            previous["state"] == "unknown"
            or now() - dt(previous["retrieved_at"]) < timedelta(hours=self.cfg.flovia_x_cache_hours)
        ):
            return previous
        query = f"${symbol} lang:en -is:retweet"
        if perspective == "contrary":
            query += " (risk OR bearish OR overvalued OR accounting OR competition)"
        source = {
            "id": "x-" + uuid4().hex,
            "source": "X via Flovia",
            "retrieved_at": now().isoformat(),
            "state": "unavailable",
            "content": {
                "query": query,
                "perspective": perspective,
                "coverage": "Last 7 days, one recency-sorted page of at most 10 posts. Sampled discussion, not representative sentiment. Claims are unverified; corroborate factual claims. No historical baseline implies no measured sentiment change.",
            },
        }
        operation = "trader-x-" + uuid4().hex
        try:
            if self.store.get("blocked", "flovia_control"):
                raise ValueError("Flovia X paused pending provider recovery")
            attempts = [
                e for e in self.store.events("flovia_x_attempt") if e["at"][:10] == now().date().isoformat()
            ]
            if len(attempts) >= self.cfg.flovia_x_daily_calls:
                raise ValueError("Daily X lookup ceiling reached")
            status = self.call("flovia_status", {})
            if (
                dec(status["purchased_balance_usd"]) != 0
                or status["data_consent"]["needed"]
                or status["data_consent"]["answer"] != "y"
                or "x" in status["services_off"]
                or status["services"]["x"]["state"] != "normal"
                or any(dec(status[w]["used_pct"]) >= 100 for w in ("five_hour", "week"))
            ):
                raise ValueError("Flovia free-use gate unavailable")
            source["operation_id"] = operation
            source["state"] = "unknown"
            self.store.put(key, source, "flovia_x")
            self.store.log("flovia_x_attempt", {"operation_id": operation, "symbol": symbol, "query": query})
            result = self.call(
                "flovia_x",
                {
                    "action": "search_recent",
                    "query": query,
                    "max_results": 10,
                    "sort_order": "recency",
                    "request_text": f"Public company discussion for {symbol}",
                    "purpose": "Research sentiment and contrary claims; verify against primary sources",
                    "operation_id": operation,
                    "conversation_id": "trader-" + self.store.scope,
                },
            )
            source["content"]["response"] = result
            explanation = result.get("explanation", {})
            if not isinstance(explanation, dict):
                raise TypeError("Invalid Flovia explanation")
            source["state"] = "received"
            if explanation or result.get("ok") is False:
                source["state"] = "refused" if explanation.get("executed") == "no" else "unknown"
                source["content"]["gap"] = "X request refused or ambiguous; no usable sentiment evidence."
                self.store.put(
                    "blocked",
                    {"operation_id": operation, "code": explanation.get("code", "unknown")},
                    "flovia_control",
                )
            self.store.log("flovia_x_result", {"operation_id": operation, "response": result})
        except (httpx.HTTPError, ValueError, KeyError, StopIteration, TypeError, ArithmeticError):
            if source["state"] == "unknown":
                self.store.put("blocked", {"operation_id": operation, "code": "unknown"}, "flovia_control")
            # Do not log upstream error strings, which could contain credentials or request details.
            source["content"]["gap"] = (
                "X lookup unavailable or ambiguous; no sentiment conclusion. Unknown operations require manual reconciliation before retry."
            )
        self.store.put(key, source, "flovia_x")
        return source
