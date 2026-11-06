"""Local durable telemetry outbox -> Postgres. Never participates in trading decisions."""

from __future__ import annotations

import atexit
import contextvars
import dataclasses
import fcntl
import functools
import inspect
import json
import logging
import os
import re
import sqlite3
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.types.json import Jsonb

LOG = logging.getLogger(__name__)
CONTEXT = contextvars.ContextVar("trace", default=None)
_sink = None
SECRET = re.compile(
    r"authorization|cookie|password|secret|api.?key|access.?token|refresh.?token|bearer.?token|bot.?token|dsn",
    re.IGNORECASE,
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_events (
 event_id uuid PRIMARY KEY, at timestamptz NOT NULL, trace_id text NOT NULL,
 span_id text, parent_span_id text, experiment_id text, run_key text,
 name text NOT NULL, phase text NOT NULL, payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_trace ON telemetry_events(trace_id, at);
CREATE INDEX IF NOT EXISTS telemetry_run ON telemetry_events(run_key, at);
CREATE INDEX IF NOT EXISTS telemetry_name ON telemetry_events(name, at);
CREATE INDEX IF NOT EXISTS telemetry_payload ON telemetry_events USING gin(payload);
"""


def clean(value):
    """Serialize explicitly; never inspect arbitrary objects or credentials on self."""
    secrets = tuple(v for k, v in os.environ.items() if SECRET.search(k) and len(v) >= 8)
    return _clean(value, secrets)


def _clean(value, secrets):
    if isinstance(value, httpx.Response):
        value = {"status_code": value.status_code, "body": value.text}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if SECRET.search(str(k)) else _clean(v, secrets) for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_clean(v, secrets) for v in value]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\b(?:sk-[\w-]+|flv_[\w-]+)", "[REDACTED]", value)
        value = re.sub(r"(?i)(bearer\s+)[^\s\"',}]+", r"\1[REDACTED]", value)
        value = re.sub(r"(postgres(?:ql)?://)[^\s]+", r"\1[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:password|secret|api[_-]?key|access[_-]?token)[\w-]*[\"\']?\s*[:=]\s*[\"\']?)[^\s\"\',}]+",
            r"\1[REDACTED]",
            value,
        )
        return value.replace("\x00", "\\u0000")
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if type(value).__module__ in {"decimal", "datetime", "pathlib"}:
        return str(value)
    return {"type": type(value).__name__}


class Sink:
    def __init__(self, path, dsn, start=True):
        self.path, self.dsn = Path(path), dsn
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        self.export_lock = self.path.with_suffix(".export.lock")
        self.export_lock.touch(mode=0o600, exist_ok=True)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        if start:
            self.thread = threading.Thread(target=self.work, daemon=True, name="telemetry-export")
            self.thread.start()
            atexit.register(self.close)

    @contextmanager
    def db(self):
        # Independent short-lived connections work across model threads and CLI processes.
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def emit(self, name, phase, payload, context):
        row = {
            "event_id": str(uuid.uuid4()),
            "at": datetime.now(UTC).isoformat(),
            "trace_id": context.get("trace_id") or str(uuid.uuid4()),
            "span_id": context.get("span_id"),
            "parent_span_id": context.get("parent_span_id"),
            "experiment_id": context.get("experiment_id"),
            "run_key": context.get("run_key"),
            "name": name,
            "phase": phase,
            "payload": clean(payload),
        }
        with self.db() as db:
            db.execute("INSERT INTO outbox VALUES (?, ?)", (row["event_id"], json.dumps(row)))

    def flush(self):
        with self.lock, self.export_lock.open("a") as lock, self.db() as db:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            rows = db.execute("SELECT id, body FROM outbox ORDER BY rowid LIMIT 200").fetchall()
            if not rows:
                return 0
            with psycopg.connect(self.dsn, connect_timeout=2, options="-c statement_timeout=30000") as pg:
                # Hosted writers only need INSERT/SELECT; schema provisioning belongs to migration.
                if pg.execute("SELECT to_regclass('public.telemetry_events')").fetchone()[0] is None:
                    pg.execute(SCHEMA)
                batch = [json.loads(body) for _, body in rows]
                for row in batch:
                    row["payload"] = Jsonb(row["payload"])
                with pg.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO telemetry_events VALUES (
                        %(event_id)s, %(at)s, %(trace_id)s, %(span_id)s, %(parent_span_id)s,
                        %(experiment_id)s, %(run_key)s, %(name)s, %(phase)s, %(payload)s)
                        ON CONFLICT (event_id) DO NOTHING""",
                        batch,
                    )
            # Commit Postgres first. Replays after a crash are idempotent.
            db.executemany("DELETE FROM outbox WHERE id=?", [(r[0],) for r in rows])
            return len(rows)

    def work(self):
        failed = False
        while True:
            try:
                count = self.flush()
                if failed:
                    LOG.warning("Telemetry Postgres export recovered")
                failed = False
            except Exception:  # noqa: BLE001 - telemetry must not change trading behavior
                if not failed:
                    LOG.warning("Telemetry export unavailable; events retained in local outbox")
                failed, count = True, 0
            if self.stop.is_set():
                return
            if count < 200:
                self.stop.wait(2 if not failed else 15)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=4)


def configure(ledger):
    global _sink
    if not CONTEXT.get() and os.environ.get("OBSERVABILITY_TRACE_CONTEXT"):
        try:
            inherited = json.loads(os.environ["OBSERVABILITY_TRACE_CONTEXT"])
            CONTEXT.set(
                {
                    k: str(v)
                    for k, v in inherited.items()
                    if k in {"trace_id", "span_id", "experiment_id", "run_key"} and v is not None
                }
            )
        except (ValueError, AttributeError):
            LOG.warning("Ignoring malformed telemetry parent context")
    if _sink is None and os.environ.get("OBSERVABILITY_DATABASE_URL"):
        try:
            _sink = Sink(
                Path(ledger).parent / "telemetry-outbox.sqlite", os.environ["OBSERVABILITY_DATABASE_URL"]
            )
        except Exception:  # noqa: BLE001 - telemetry must not change trading behavior
            LOG.error("Telemetry local outbox unavailable; trading ledger remains authoritative")


def emit(name, payload, phase="event", **context):
    if _sink is not None:
        try:
            _sink.emit(
                name,
                phase,
                payload,
                {**(CONTEXT.get() or {}), **{k: v for k, v in context.items() if v is not None}},
            )
        except Exception:  # noqa: BLE001 - telemetry must not change trading behavior
            LOG.error("Telemetry event could not be persisted: %s", name)


@contextmanager
def span(name, data, experiment_id=None):
    parent = CONTEXT.get() or {}
    run_key = data.get("run_key")
    for value in data.values():
        if isinstance(value, dict) and value.get("run_key"):
            run_key = value["run_key"]
    ctx = {
        **parent,
        "trace_id": parent.get("trace_id") or run_key or str(uuid.uuid4()),
        "experiment_id": experiment_id or parent.get("experiment_id"),
        "run_key": run_key or parent.get("run_key"),
        "parent_span_id": parent.get("span_id"),
        "span_id": str(uuid.uuid4()),
    }
    token = CONTEXT.set(ctx)
    start = time.monotonic()
    outcome = {}
    emit(name, {"input": data}, "start")
    try:
        yield outcome
        emit(name, {**outcome, "duration_ms": (time.monotonic() - start) * 1000}, "end")
    except BaseException as error:
        emit(
            name,
            {
                "error_type": type(error).__name__,
                "error": str(error),
                "traceback": traceback.format_exc(),
                "ai_message": getattr(error, "ai_message", None),
                "response": getattr(error, "response", None),
                "duration_ms": (time.monotonic() - start) * 1000,
            },
            "error",
        )
        raise
    finally:
        CONTEXT.reset(token)


def traced(name, inputs=None):
    """Start before work; end/error even for validation failures and async cancellation."""

    def decorate(fn):
        signature = inspect.signature(fn)

        def capture(args, kwargs):
            data = (
                inputs(*args, **kwargs)
                if inputs
                else {
                    k: v
                    for k, v in signature.bind(*args, **kwargs).arguments.items()
                    if k not in {"self", "handler", "schema"}
                }
            )
            store = getattr(args[0], "store", None) if args else None
            return span(name, data, getattr(store, "scope", None))

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if _sink is None:
                return fn(*args, **kwargs)
            with capture(args, kwargs) as outcome:
                result = fn(*args, **kwargs)
                outcome["output"] = result
                return result

        @functools.wraps(fn)
        async def async_wrapped(*args, **kwargs):
            if _sink is None:
                return await fn(*args, **kwargs)
            with capture(args, kwargs) as outcome:
                result = await fn(*args, **kwargs)
                outcome["output"] = result
                return result

        return async_wrapped if inspect.iscoroutinefunction(fn) else wrapped

    return decorate
