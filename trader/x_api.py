"""Direct, bounded X discovery. Posts are unverified evidence, never trading signals."""

import fcntl
import json
import os
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx

from .models import dt, now
from .observability import traced


class XDiscovery:
    def __init__(self, cfg, store, client=None):
        self.cfg, self.store, self.client = cfg, store, client

    def token(self):
        token = os.getenv("X_BEARER_TOKEN", "")
        if not token:
            path = Path(self.cfg.database).resolve().parent / "x-api.env"
            if path.exists():
                for line in path.read_text().splitlines():
                    if line.startswith("X_BEARER_TOKEN="):
                        token = line.partition("=")[2].strip()
                        break
            if token:
                os.environ["X_BEARER_TOKEN"] = token  # Include in telemetry credential redaction.
        if not token or any(c.isspace() for c in token):
            raise ValueError("X Bearer Token missing or invalid")
        return token

    @traced("x.search")
    def call(self, query):
        token = self.token()

        def request(client):
            with client.stream(
                "GET",
                "https://api.x.com/2/tweets/search/recent",
                headers={"Authorization": "Bearer " + token},
                params={
                    "query": query,
                    "max_results": 10,
                    "sort_order": "recency",
                    "tweet.fields": "created_at,author_id,public_metrics",
                },
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 250_000:
                        raise ValueError("X response too large")
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks))
            if not isinstance(value, dict) or value.get("errors"):
                raise ValueError("Incomplete X response")
            posts = value.get("data", [])
            if not isinstance(posts, list) or len(posts) > 10 or not isinstance(value.get("meta"), dict):
                raise ValueError("Invalid X response")
            for post in posts:
                if (
                    not isinstance(post, dict)
                    or not re.fullmatch(r"[0-9]+", str(post.get("id", "")))
                    or not isinstance(post.get("text"), str)
                    or not post.get("created_at")
                ):
                    raise ValueError("Invalid X post")
                dt(post["created_at"])
                post["url"] = "https://x.com/i/web/status/" + str(post["id"])
            return value

        if self.client is not None:
            return request(self.client)
        with httpx.Client(timeout=30) as client:
            return request(client)

    @traced("x.snapshot")
    def snapshot(self, symbol, *, acquisition_callback=None):
        if symbol not in self.cfg.research_universe or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Invalid research symbol")
        if not self.cfg.x_enabled:
            return []
        path = self.store.control_path(self.cfg.control_dir, "direct-x", ".lock")
        with path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return [
                    acquisition_callback(
                        "x_" + perspective,
                        {"symbol": symbol, "perspective": perspective},
                        lambda perspective=perspective: self._search(symbol, perspective),
                    )
                    if acquisition_callback is not None
                    else self._search(symbol, perspective)
                    for perspective in ("discussion", "contrary")
                ]
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _search(self, symbol, perspective):
        key = symbol + ":" + perspective
        previous = self.store.get(key, "x_discovery")
        if previous and (
            previous["state"] == "unknown"
            or (
                previous["state"] == "received"
                and now() - dt(previous["retrieved_at"]) < timedelta(hours=self.cfg.x_cache_hours)
            )
        ):
            return previous
        query = f"${symbol} lang:en -is:retweet"
        if perspective == "contrary":
            query += " (risk OR bearish OR overvalued OR accounting OR competition)"
        source = {
            "id": "x-direct-" + uuid4().hex,
            "source": "X API",
            "retrieved_at": now().isoformat(),
            "state": "unavailable",
            "content": {
                "query": query,
                "perspective": perspective,
                "coverage": "Last 7 days; one recency-sorted page of at most 10 posts. Sampled discussion, not representative sentiment. Claims are unverified; corroborate with primary sources. No historical baseline implies no measured sentiment change.",
            },
        }
        try:
            self.token()
            if self.store.get("blocked", "x_control") or any(
                item["state"] == "unknown" for item in self.store.items("x_discovery")
            ):
                raise ValueError("X paused pending reconciliation")
            attempts = [e for e in self.store.events("x_attempt") if e["at"][:10] == now().date().isoformat()]
            if len(attempts) >= self.cfg.x_daily_calls:
                raise ValueError("Daily X ceiling reached")
            operation = "trader-direct-x-" + uuid4().hex
            source.update(state="unknown", operation_id=operation)
            self.store.put(key, source, "x_discovery")
            self.store.log(
                "x_attempt", {"operation_id": operation, "symbol": symbol, "query": query, "max_results": 10}
            )
            result = self.call(query)
            source["content"]["response"] = result
            source["state"] = "received"
            self.store.log(
                "x_result",
                {
                    "operation_id": operation,
                    "response": result,
                    "returned_posts": len(result.get("data", [])),
                    "cost_usd": None,
                },
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError):
            if source["state"] == "unknown":
                self.store.put(
                    "blocked", {"operation_id": source["operation_id"], "code": "unknown"}, "x_control"
                )
            source["content"]["gap"] = (
                "X unavailable; no sentiment conclusion. Failed/ambiguous requests are not retried automatically; reconcile provider usage first."
            )
        self.store.put(key, source, "x_discovery")
        return source
