"""Real SIGKILL at durable boundaries against a loopback, non-trading broker."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from trader.accounting import LedgerError
from trader.models import dec, dumps
from trader.service import Service
from trader.store import Store


@pytest.mark.parametrize(
    "stage",
    [
        "prepared",
        "accepted",
        "order",
        "cancel",
        "cancel_race",
        "cancel_race_canceled",
        "activity",
        "adjustment",
        "adjustment_mid",
    ],
)
def test_process_death_preserves_intent_and_reconciles_without_duplicate(env, decision, tmp_path, stage):
    service, broker, data = env
    if stage.startswith("adjustment"):
        broker.activities.extend(
            [
                {"id": "deposit-a", "activity_type": "CSD", "date": "2026-10-21", "net_amount": "1000"},
                {"id": "deposit-b", "activity_type": "CSD", "date": "2026-10-21", "net_amount": "2000"},
            ]
        )
        broker.raw_account.update(cash="103000", equity="103000")
        with pytest.raises(LedgerError):
            service.reconcile()
        service.kill_path.write_text("Operator reviewing contributions")
        adjustments = [
            {"id": "deposit-a", "cash": "1000", "reason": "Verified A"},
            {"id": "deposit-b", "cash": "2000", "reason": "Verified B"},
        ]
        (tmp_path / "fixture-adjustments.json").write_text(json.dumps(adjustments))
    backup = None
    if stage in {"cancel", "activity"} or stage.startswith("cancel_race"):
        result = service.execute(decision, "process-drill")
        if stage.startswith("cancel_race"):
            broker.fixture_fill(result["client_order_id"], "4", status="partially_filled")
            service.reconcile()
            backup = service.store.backup()
        if stage == "activity":
            broker.fixture_fill(result["client_order_id"])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            method = self.path.removeprefix("/")
            if method not in {
                "account",
                "snapshot",
                "lookup",
                "submit",
                "cancel",
                "asset",
                "clock",
                "calendar",
                "open_orders",
            }:
                self.send_error(404)
                return
            args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            result = getattr(broker, method)(*args)
            body = dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "fixture-config.json"
    config.write_text(service.cfg.model_dump_json())
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent / "helpers/crash_worker.py"),
                str(config),
                f"http://127.0.0.1:{server.server_port}",
                stage,
            ],
            env={
                **os.environ,
                "OBSERVABILITY_DATABASE_URL": "",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == -signal.SIGKILL, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if backup:
        # Recover an older durable ledger after actual process death; retain the crashed state.
        with closing(sqlite3.connect(tmp_path / "crashed-ledger.sqlite")) as retained:
            service.store.db.backup(retained)
            assert retained.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        service.store.db.close()
        with (
            closing(sqlite3.connect(backup)) as source,
            closing(sqlite3.connect(service.cfg.database)) as target,
        ):
            source.backup(target)
    reopened = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, reopened, broker, data)
        assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reopened.scope == service.store.scope
        if stage.startswith("adjustment"):
            before = len(reopened.items("adjustment"))
            assert before == (0 if stage == "adjustment_mid" else 2)
            assert len(reopened.events("explicit_adjustment")) == before
            recovered.acknowledge_adjustment(adjustments)
            recovered.acknowledge_adjustment(adjustments)
            assert recovered.kill_path.exists()
            assert broker.submits == 0
            assert len(reopened.items("adjustment")) == 2
            assert len(reopened.events("explicit_adjustment")) == 2
            assert reopened.get("audit")["cash"] == "103000"
        elif stage.startswith("cancel_race"):
            cid = next(iter(broker.orders))
            assert broker.orders[cid]["status"] == "pending_cancel"
            assert reopened.get(cid, "order")["status"] == "partially_filled"
            assert recovered.kill_path.exists()
            assert recovered.reservation_summary() == dec("601")
            # A terminal broker order arrives before its additional fill activity.
            canceled = stage == "cancel_race_canceled"
            terminal = "canceled" if canceled else "filled"
            late = broker.fixture_fill(cid, "2" if canceled else "6", status=terminal)
            broker.activities.remove(late)
            with pytest.raises(LedgerError):
                recovered.reconcile()
            assert reopened.get("reconciliation")["ok"] is False
            with pytest.raises(LedgerError):
                recovered.execute(decision, "process-drill")
            assert broker.submits == 1
            broker.activities.append(late)
            for _ in range(2):
                recovered.reconcile()
            assert reopened.get("reconciliation")["ok"] is True
            assert reopened.get("audit")["cash"] == ("99400" if canceled else "99000")
            assert reopened.get("audit")["shares"]["ACME"] == ("6" if canceled else "10")
            assert len(reopened.items("activity")) == 2
            assert len(reopened.events("broker_activity")) == 2
            assert reopened.get(cid, "order")["status"] == terminal
            assert recovered.reservation_summary() == 0
            assert recovered.kill_path.exists()
            assert len(broker.cancels) == 1
            assert broker.submits == 1
            assert recovered.execute(decision, "new-after-process-restore")["status"] == "rejected"
        elif stage == "prepared":
            with pytest.raises(LedgerError, match="Unresolved submission"):
                recovered.execute(decision, "process-drill")
            assert recovered.reservation_summary() == dec("1001")
            assert broker.submits == 0
        else:
            recovered.execute(decision, "process-drill")
            recovered.execute(decision, "process-drill")
            assert broker.submits == 1
            if stage == "activity":
                assert reopened.get("audit")["cash"] == "99000"
                assert reopened.get("audit")["shares"]["ACME"] == "10"
                assert len(reopened.items("activity")) == 1
                assert recovered.reservation_summary() == 0
            else:
                assert recovered.reservation_summary() == dec("1001")
            if stage == "cancel":
                assert recovered.kill_path.exists()
                assert len(broker.cancels) == 1
                assert reopened.active_orders()[0]["status"] == "pending_cancel"
    finally:
        reopened.db.close()
