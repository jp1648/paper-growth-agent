"""Real SIGKILL at durable boundaries against a loopback, non-trading broker."""

import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from trader.accounting import LedgerError
from trader.models import dec, dumps
from trader.service import Service
from trader.store import Store


@pytest.mark.parametrize("stage", ["prepared", "accepted", "order", "cancel", "activity"])
def test_process_death_preserves_intent_and_reconciles_without_duplicate(env, decision, tmp_path, stage):
    service, broker, data = env
    if stage in {"cancel", "activity"}:
        result = service.execute(decision, "process-drill")
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
    reopened = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, reopened, broker, data)
        assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reopened.scope == service.store.scope
        if stage == "prepared":
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
