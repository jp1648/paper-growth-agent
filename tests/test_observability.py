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


def test_concurrent_emit_waits_for_local_commit_without_losing_event(sink, monkeypatch):
    from concurrent.futures import TimeoutError
    from contextlib import contextmanager
    from threading import Event

    entered, release = Event(), Event()
    database = sink.db
    first = True

    @contextmanager
    def slow_commit():
        nonlocal first
        hold = first
        first = False
        with database() as db:
            yield db
            if hold:
                entered.set()
                assert release.wait(10)

    monkeypatch.setattr(sink, "db", slow_commit)
    with ThreadPoolExecutor(2) as workers:
        initial = workers.submit(obs.emit, "first", {})
        try:
            assert entered.wait(5)
            following = workers.submit(obs.emit, "second", {})
            # The SQLite busy timeout is two seconds. A queued writer must not drop.
            with pytest.raises(TimeoutError):
                following.result(timeout=2.3)
        finally:
            release.set()
        initial.result(timeout=5)
        following.result(timeout=5)
    assert [row["name"] for row in events(sink)] == ["first", "second"]


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


@pytest.mark.skipif(
    not os.environ.get("TEST_OBSERVABILITY_DATABASE_URL"), reason="Opt-in local Postgres test"
)
def test_lost_commit_ack_replays_exact_payloads_after_exporter_restart(sink):
    import select
    import socket
    import time

    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    direct = os.environ["TEST_OBSERVABILITY_DATABASE_URL"]
    config = conninfo_to_dict(direct)
    assert config.get("host") in {"localhost", "127.0.0.1"}, "Only isolated loopback Postgres allowed"
    with psycopg.connect(direct) as pg:
        pg.execute(obs.SCHEMA)
    for index in range(201):
        obs.emit("lost-ack.fixture", {"index": index, "text": "exact payload " + str(index)})
    originals = events(sink)
    ids = [row["event_id"] for row in originals]

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        sink.dsn = make_conninfo(
            direct,
            host="127.0.0.1",
            hostaddr="127.0.0.1",
            port=listener.getsockname()[1],
            sslmode="disable",
            gssencmode="disable",
        )

        def drop_commit_ack():
            client, _ = listener.accept()
            with (
                client,
                socket.create_connection(
                    (config["host"], int(config.get("port", 5432))), timeout=5
                ) as server,
            ):
                client.settimeout(5)
                pending = b""
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    readable, _, _ = select.select([client, server], [], [], 1)
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            raise AssertionError("Connection closed before the COMMIT acknowledgement")
                        if source is client:
                            server.sendall(data)
                            continue
                        pending += data
                        # PostgreSQL backend messages: type byte, four-byte length, payload.
                        while len(pending) >= 5:
                            length = int.from_bytes(pending[1:5], "big")
                            assert 4 <= length <= 8_000_000
                            if len(pending) < length + 1:
                                break
                            message, pending = pending[: length + 1], pending[length + 1 :]
                            if message[:1] == b"C" and message[5:] == b"COMMIT\x00":
                                return "commit_ack_dropped"
                            client.sendall(message)
                raise AssertionError("Postgres did not commit within the fixture deadline")

        with ThreadPoolExecutor(1) as pool:
            fault = pool.submit(drop_commit_ack)
            with pytest.raises(psycopg.OperationalError):
                sink.flush()
            assert fault.result(timeout=20) == "commit_ack_dropped"
    assert events(sink) == originals
    with psycopg.connect(direct) as pg:
        rows = pg.execute(
            "SELECT event_id::text,payload FROM telemetry_events WHERE event_id::text = ANY(%s)", (ids,)
        ).fetchall()
    assert dict(rows) == {row["event_id"]: row["payload"] for row in originals[:200]}
    # New events still queue while the old committed batch is locally unacknowledged.
    obs.emit("lost-ack.fixture", {"index": 201})
    expected = events(sink)
    restarted = obs.Sink(sink.path, direct, start=False)
    assert restarted.flush() == 200
    assert restarted.flush() == 2
    assert restarted.flush() == 0 and events(sink) == []
    with psycopg.connect(direct) as pg:
        rows = pg.execute(
            "SELECT event_id::text,payload FROM telemetry_events WHERE event_id::text = ANY(%s)",
            ([row["event_id"] for row in expected],),
        ).fetchall()
    assert len(rows) == 202
    assert dict(rows) == {row["event_id"]: row["payload"] for row in expected}


def test_outbox_capacity_loss_preserves_execution_evidence_and_fill_replay(
    env, decision, sink, monkeypatch, caplog
):
    import sqlite3
    from contextlib import contextmanager

    from trader.models import dec

    service, broker, _ = env
    database = sink.db

    @contextmanager
    def capacity_limited():
        with database() as db:
            db.execute(f"PRAGMA max_page_count={db.execute('PRAGMA page_count').fetchone()[0]}")
            yield db

    evidence = {"source": "offline audit fixture", "body": "x" * 100_000}
    with monkeypatch.context() as patch:
        patch.setattr(sink, "db", capacity_limited)
        with pytest.raises(sqlite3.OperationalError) as full:
            sink.emit("capacity.probe", "event", evidence, {})
        assert full.value.sqlite_errorcode == sqlite3.SQLITE_FULL
        result = service.execute(decision, "outbox-full-audit", evidence=evidence)
        assert result["status"] == "submitted" and broker.submits == 1
        cid = result["client_order_id"]
        assert "Telemetry event could not be persisted: decision" in caplog.text
        assert not any(row["name"] == "decision" for row in events(sink))
        saved = service.store.get(cid, "intent")
        assert saved["evidence"] == evidence and saved["risk_context"]
        receipts = service.store.events("decision")
        assert len(receipts) == 1 and receipts[0]["body"]["evidence"] == evidence
        checkpoint = next(service.store.path.parent.glob("backups/intent-*.sqlite"))
        with sqlite3.connect(f"file:{checkpoint}?mode=ro", uri=True) as copy:
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            row = copy.execute("SELECT body FROM documents WHERE kind='intent' AND key=?", (cid,)).fetchone()
            assert json.loads(row[0])["evidence"] == evidence
            assert copy.execute("SELECT count(*) FROM journal WHERE kind='decision'").fetchone()[0] == 1
        broker.fixture_fill(cid)
        for _ in range(2):
            service.reconcile()
        assert service.store.get("audit")["cash"] == "99000"
        assert service.store.get("audit")["shares"]["ACME"] == "10"
        assert len(service.store.items("activity")) == len(service.store.events("broker_activity")) == 1
        assert service.reservation_summary() == dec(0)
        assert broker.submits == 1
    obs.emit("capacity.recovered", {"body": "y" * 100_000})
    assert any(row["name"] == "capacity.recovered" for row in events(sink))
    assert not any(row["name"] == "decision" for row in events(sink))  # Lost traces are not reconstructed.
