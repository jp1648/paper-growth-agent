import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from langchain_core.messages import AIMessage

from trader import observability as obs


@pytest.fixture
def sink(tmp_path, monkeypatch):
    result = obs.Sink(tmp_path / "telemetry.sqlite", "unused", start=False)
    monkeypatch.setattr(obs, "_sink", result)
    token = obs.CONTEXT.set(None)
    yield result
    obs.CONTEXT.reset(token)


def events(sink):
    with sink.db() as db:
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM outbox ORDER BY rowid")]


def test_redaction(sink, monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "private-fixture-credential")
    obs.emit(
        "test",
        {
            "Authorization": "abc",
            "nested": [{"api_key": "xyz"}],
            "text": "private-fixture-credential sk-demo123 flv_demo456 Bearer hidden password=hunter2",
            "source_id": "sec:123",
            "input_tokens": 500,
        },
    )
    row = events(sink)[0]["payload"]
    assert row["source_id"] == "sec:123" and row["input_tokens"] == 500
    raw = json.dumps(row)
    for secret in (
        "abc",
        "xyz",
        "private-fixture-credential",
        "sk-demo123",
        "flv_demo456",
        "hidden",
        "hunter2",
    ):
        assert secret not in raw
    assert (sink.path.stat().st_mode & 0o777) == 0o600


def test_nested_failure_and_rejected_response(sink):
    @obs.traced("child")
    def child(output):
        error = ValueError("unknown citation")
        error.ai_message = AIMessage(content='{"evidence_ids":["bad-id"]}')
        raise error

    @obs.traced("run")
    def run(run_key):
        child({"evidence_ids": ["bad-id"]})

    with pytest.raises(ValueError, match="unknown citation"):
        run("stable-run")
    rows = events(sink)
    assert [r["phase"] for r in rows] == ["start", "start", "error", "error"]
    assert len({r["trace_id"] for r in rows}) == 1
    assert rows[1]["parent_span_id"] == rows[0]["span_id"]
    assert rows[2]["payload"]["ai_message"]["content"] == '{"evidence_ids":["bad-id"]}'
    assert all(r["run_key"] == "stable-run" for r in rows)
    assert obs.CONTEXT.get() is None


def test_threads_and_async_cancellation(sink):
    @obs.traced("worker")
    def work(run_key):
        return run_key

    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(work, ["a", "b", "c", "d"])) == ["a", "b", "c", "d"]

    @obs.traced("cancel")
    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancel())
    rows = events(sink)
    assert len(rows) == 10
    assert rows[-1]["phase"] == "error"
    assert rows[-1]["payload"]["error_type"] == "CancelledError"


def test_outage_retains_events_and_telemetry_error_does_not_change_result(sink, monkeypatch):
    obs.emit("queued", {"x": 1})
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(OSError):
        sink.flush()
    assert len(events(sink)) == 1
    monkeypatch.setattr(sink, "emit", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    assert obs.traced("good")(lambda: 42)() == 42


def test_real_deepagents_trace_completion(env, decision, sink):
    from test_research_team import team

    from trader.research import build_graph

    research, _ = team(env, decision)
    service, broker, _ = env
    result = build_graph(service, research).invoke(
        {"symbol": "ACME", "run_key": "telemetry-fixture", "research_only": True}
    )
    assert result["execution"]["status"] == "deferred"
    assert broker.submits == 0
    rows = events(sink)
    starts = {r["span_id"]: r for r in rows if r["phase"] == "start"}
    ends = {r["span_id"]: r for r in rows if r["phase"] in {"end", "error"}}
    assert starts.keys() == ends.keys()
    assert {"model.call", "tool.call", "research.validate", "graph.reconciliation_reporting"} <= {
        r["name"] for r in rows
    }
    for row in rows:
        if row["name"] in {"model.call", "tool.call"}:
            assert row["run_key"] == "telemetry-fixture"
            assert row["parent_span_id"] in starts
    response = next(r for r in rows if r["name"] == "model.response")
    assert response["payload"]["response"]["result"][0]["type"] == "ai"
    if os.environ.get("TEST_OBSERVABILITY_DATABASE_URL"):
        sink.dsn = os.environ["TEST_OBSERVABILITY_DATABASE_URL"]
        while sink.flush():
            continue


@pytest.mark.skipif(
    not os.environ.get("TEST_OBSERVABILITY_DATABASE_URL"), reason="Opt-in local Postgres test"
)
def test_postgres_delivery_and_duplicate_replay(sink):
    sink.dsn = os.environ["TEST_OBSERVABILITY_DATABASE_URL"]
    obs.emit("integration.fixture", {"verified": True})
    original = events(sink)[0]
    assert sink.flush() == 1
    with sink.db() as db:
        db.execute("INSERT INTO outbox VALUES (?, ?)", (original["event_id"], json.dumps(original)))
    assert sink.flush() == 1 and not events(sink)
    with psycopg.connect(sink.dsn) as pg:
        assert (
            pg.execute(
                "SELECT count(*) FROM telemetry_events WHERE event_id=%s AND payload @> %s::jsonb",
                (original["event_id"], '{"verified":true}'),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.skipif(
    not os.environ.get("TEST_OBSERVABILITY_DATABASE_URL"), reason="Opt-in local Postgres test"
)
@pytest.mark.parametrize("stage", ["before_commit", "after_commit"])
def test_export_process_death_preserves_batch_and_replays_once(sink, stage):
    import signal
    import subprocess
    import sys

    sink.dsn = os.environ["TEST_OBSERVABILITY_DATABASE_URL"]
    with psycopg.connect(sink.dsn) as pg:
        pg.execute(obs.SCHEMA)
    for index in range(201):
        obs.emit("crash.fixture", {"index": index})
    originals = events(sink)
    ids = [row["event_id"] for row in originals]
    child = r"""
import os, signal, sys
from contextlib import contextmanager
from trader import observability as obs
connect = obs.psycopg.connect
@contextmanager
def interrupted(*args, **kwargs):
    with connect(*args, **kwargs) as pg:
        yield pg
        if sys.argv[2] == 'before_commit':
            os.kill(os.getpid(), signal.SIGKILL)
    os.kill(os.getpid(), signal.SIGKILL)
obs.psycopg.connect = interrupted
obs.Sink(sys.argv[1], os.environ['TEST_OBSERVABILITY_DATABASE_URL'], start=False).flush()
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(sink.path), stage],
        env={**os.environ, "OBSERVABILITY_DATABASE_URL": ""},
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL
    assert events(sink) == originals
    with psycopg.connect(sink.dsn) as pg:
        assert pg.execute(
            "SELECT count(*) FROM telemetry_events WHERE event_id::text = ANY(%s)", (ids,)
        ).fetchone()[0] == (200 if stage == "after_commit" else 0)
    restarted = obs.Sink(sink.path, sink.dsn, start=False)
    assert restarted.flush() == 200
    assert restarted.flush() == 1
    assert restarted.flush() == 0
    assert events(sink) == []
    with psycopg.connect(sink.dsn) as pg:
        rows = pg.execute(
            "SELECT event_id::text, payload FROM telemetry_events WHERE event_id::text = ANY(%s)", (ids,)
        ).fetchall()
    assert len(rows) == 201
    assert dict(rows) == {row["event_id"]: row["payload"] for row in originals}


def test_real_rejected_report_is_available(sink):
    from test_research_team import test_decimal_valuation_and_fabricated_calculation_rejected

    test_decimal_valuation_and_fabricated_calculation_rejected()
    rows = events(sink)
    rejected = next(r for r in rows if r["phase"] == "error" and "unknown evidence" in r["payload"]["error"])
    started = next(r for r in rows if r["span_id"] == rejected["span_id"] and r["phase"] == "start")
    assert "fabricated-source" in started["payload"]["input"]["output"]["evidence_ids"]


def test_subprocess_parent_context(sink, monkeypatch):
    monkeypatch.setenv(
        "OBSERVABILITY_TRACE_CONTEXT", json.dumps({"trace_id": "parent-trace", "span_id": "parent"})
    )
    obs.configure(sink.path)
    obs.traced("child")(lambda: 1)()
    assert events(sink)[0]["trace_id"] == "parent-trace"
    assert events(sink)[0]["parent_span_id"] == "parent"


def test_only_one_process_exports_a_shared_outbox(sink, monkeypatch):
    import fcntl

    obs.emit("queued", {"x": 1})
    other = obs.Sink(sink.path, "unused", start=False)
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("Must not connect while locked"))
    with sink.export_lock.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert other.flush() == 0
    assert len(events(sink)) == 1


def test_redaction_refreshes_secrets_between_events(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "first-private-token")
    assert obs.clean({"nested": ["first-private-token"]}) == {"nested": ["[REDACTED]"]}
    monkeypatch.setenv("X_BEARER_TOKEN", "second-private-token")
    assert obs.clean(["second-private-token"]) == ["[REDACTED]"]


@pytest.mark.skipif(
    not os.environ.get("TEST_OBSERVABILITY_DATABASE_URL"), reason="Opt-in local Postgres test"
)
def test_export_outage_backlog_recovers_with_concurrent_producers(sink, monkeypatch):
    from contextlib import contextmanager
    from threading import Event

    sink.dsn = os.environ["TEST_OBSERVABILITY_DATABASE_URL"]
    connect = psycopg.connect

    def produce(index):
        # Use the public emit path: its swallowed errors must not hide lost events.
        obs.emit("backlog.fixture", {"index": index, "body": "x" * 4096})

    def unavailable(*args, **kwargs):
        raise psycopg.OperationalError("Injected exporter outage")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    with ThreadPoolExecutor(4) as producers:
        for batch in range(5):
            list(producers.map(produce, range(batch * 200, (batch + 1) * 200)))
            with pytest.raises(psycopg.OperationalError):
                sink.flush()
            assert len(events(sink)) == (batch + 1) * 200
    originals = events(sink)
    entered, release = Event(), Event()

    @contextmanager
    def slow_export(*args, **kwargs):
        with connect(*args, **kwargs) as pg:
            entered.set()
            assert release.wait(10), "Concurrent producer did not finish"
            yield pg

    monkeypatch.setattr(psycopg, "connect", slow_export)
    restarted = obs.Sink(sink.path, sink.dsn, start=False)
    with ThreadPoolExecutor(1) as exporter:
        exporting = exporter.submit(restarted.flush)
        try:
            assert entered.wait(10)
            with ThreadPoolExecutor(4) as producers:
                list(producers.map(produce, range(1000, 1100)))
            pending = events(sink)
            assert len(pending) == 1100
            assert pending[:1000] == originals
        finally:
            release.set()
        assert exporting.result(timeout=10) == 200
    monkeypatch.setattr(psycopg, "connect", connect)
    assert [restarted.flush() for _ in range(6)] == [200, 200, 200, 200, 100, 0]
    assert events(sink) == []
    ids = [row["event_id"] for row in pending]
    with connect(sink.dsn) as pg:
        rows = pg.execute(
            "SELECT event_id::text, payload FROM telemetry_events WHERE event_id::text = ANY(%s)", (ids,)
        ).fetchall()
    assert len(rows) == 1100
    assert dict(rows) == {row["event_id"]: row["payload"] for row in pending}


def test_sqlite_full_preserves_queued_events_and_recovers(sink, monkeypatch, caplog):
    from contextlib import contextmanager

    obs.emit("before.capacity", {"index": 1})
    original = events(sink)
    database = sink.db

    @contextmanager
    def capacity_limited():
        with database() as db:
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={pages}")
            yield db

    with monkeypatch.context() as patch:
        patch.setattr(sink, "db", capacity_limited)
        # Exercise SQLite's real SQLITE_FULL rollback, not a mocked write error.
        import sqlite3

        with pytest.raises(sqlite3.OperationalError) as failure:
            sink.emit("direct.capacity", "event", {"body": "x" * 100_000}, {})
        assert failure.value.sqlite_errorcode == sqlite3.SQLITE_FULL
        obs.emit("lost.capacity", {"body": "x" * 100_000})
        assert "Telemetry event could not be persisted: lost.capacity" in caplog.text
        assert events(sink) == original
        with database() as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # Simulate restored capacity and a reopened exporter without replacing the DB.
    restarted = obs.Sink(sink.path, sink.dsn, start=False)
    monkeypatch.setattr(obs, "_sink", restarted)
    obs.emit("after.capacity", {"body": "y" * 100_000})
    recovered = events(restarted)
    assert recovered[:1] == original
    assert [row["name"] for row in recovered] == ["before.capacity", "after.capacity"]
