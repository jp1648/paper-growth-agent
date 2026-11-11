"""Exact synchronous model-response capture inside a leased research operation."""

import hashlib
import json
from contextvars import ContextVar

import httpx

from .research_journal import JournalError

ACTIVE_EXCHANGE = ContextVar("research_provider_exchange", default=None)


class ResearchTransport(httpx.BaseTransport):
    def __init__(self, transport=None):
        self.transport = transport or httpx.HTTPTransport(retries=0)

    def handle_request(self, request):
        active = ACTIVE_EXCHANGE.get()
        if active is None:
            raise JournalError("Durable model request is outside its leased operation")
        journal, key, request_hash, replay_only = active
        if request.method != "POST" or str(request.url) != "https://api.openai.com/v1/responses":
            raise JournalError("Unexpected durable model endpoint")
        body = request.read()
        if json.loads(body).get("stream"):
            raise JournalError("Durable response capture requires non-streaming requests")
        identifier = request.headers.get("X-Client-Request-Id")
        identity = {
            "method": request.method,
            "url": str(request.url),
            "body": body,
            "headers_sha256": sorted(
                (name.lower(), hashlib.sha256(value).hexdigest()) for name, value in request.headers.raw
            ),
        }

        def send():
            response = self.transport.handle_request(request)
            try:
                chunks, size = [], 0
                for chunk in response.stream:
                    size += len(chunk)
                    if size > 20_000_000:
                        raise JournalError("Provider response exceeds durable capture bound")
                    chunks.append(chunk)
                return {
                    "status": response.status_code,
                    "headers": list(response.headers.raw),
                    "body": b"".join(chunks),
                }
            finally:
                response.close()

        saved = journal.exchange(key, request_hash, identifier, identity, send, replay_only=replay_only)
        return httpx.Response(
            saved["status"], headers=saved["headers"], stream=httpx.ByteStream(saved["body"])
        )

    def close(self):
        self.transport.close()
