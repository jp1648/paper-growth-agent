"""Real SIGKILL at durable boundaries against a loopback, non-trading broker."""

import copy
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
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
        "accepted_lost_ack",
        "accepted_partial_lost_ack",
        "accepted_filled_lost_ack",
        "accepted_lost_ack_replacement",
        "accepted_partial_lost_ack_replacement",
        "accepted_filled_lost_ack_replacement",
        "order",
        "cancel",
        "cancel_race",
        "cancel_race_canceled",
        "cancel_race_lost_ack",
        "cancel_race_canceled_lost_ack",
        "sell_cancel_race",
        "sell_cancel_race_canceled",
        "sell_cancel_race_lost_ack",
        "sell_cancel_race_canceled_lost_ack",
        "cancel_race_lost_ack_replacement",
        "cancel_race_canceled_lost_ack_replacement",
        "sell_cancel_race_lost_ack_replacement",
        "sell_cancel_race_canceled_lost_ack_replacement",
        "activity",
        "adjustment",
        "adjustment_mid",
        "revision_mid",
        "revision_committed",
    ],
)
def test_process_death_preserves_intent_and_reconciles_without_duplicate(
    env, decision, tmp_path, stage, monkeypatch
):
    service, broker, data = env
    replacement = stage.endswith("_replacement")
    stage = stage.removesuffix("_replacement")
    selling = stage.startswith("sell_")
    if selling:
        stage = stage.removeprefix("sell_")
        entry = service.execute(decision, "fixture-entry")["client_order_id"]
        broker.fixture_fill(entry)
        service.reconcile()
        decision = decision.model_copy(update={"action": "SELL", "exit_reason": "risk"})
    if stage.startswith("revision"):
        original = {"id": "fee", "activity_type": "FEE", "net_amount": "-1", "date": "2026-10-21"}
        broker.activities = [original.copy()]
        broker.raw_account.update(cash="99999", equity="99999")
        service.reconcile()
        corrected = {**original, "net_amount": "-2"}
        broker.activities = [corrected.copy()]
        broker.raw_account.update(cash="99998", equity="99998")
        service.kill_path.write_text("Operator reviewing fee revision")
        revision = {"before": original, "after": corrected, "reason": "Verified fixture correction"}
        (tmp_path / "fixture-revision.json").write_text(json.dumps(revision))
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

    dropped_responses = []

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
            if stage.endswith("lost_ack") and (
                method == "submit" if stage.startswith("accepted") else method == "cancel"
            ):
                if stage == "accepted_partial_lost_ack":
                    broker.fixture_fill(result["client_order_id"], "4", status="partially_filled")
                elif stage == "accepted_filled_lost_ack":
                    broker.fixture_fill(result["client_order_id"])

                dropped_responses.append(method)
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
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
        if stage.endswith("lost_ack"):
            operation = "submit" if stage.startswith("accepted") else "cancel"
            assert dropped_responses == [operation]
            failure = json.loads((tmp_path / "transport-failure.json").read_text())
            assert failure == {"operation": operation, "error": "RemoteProtocolError"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if backup and not replacement:
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
    if replacement:
        checkpoint = backup or next(service.store.path.parent.glob("backups/intent-*.sqlite"))
        replacement_root = tmp_path / "replacement"
        replacement_root.mkdir()
        destination = replacement_root / "ledger.sqlite"
        shutil.copyfile(checkpoint, destination)
        # Retain forensic evidence, but make the original configured paths unavailable.
        original_path = service.store.path
        service.store.db.close()
        original_path.rename(tmp_path / "unavailable-original.sqlite")
        for suffix in ("-wal", "-shm"):
            Path(str(original_path) + suffix).unlink(missing_ok=True)
        assert not original_path.exists()
        service.cfg = service.cfg.model_copy(
            update={"database": str(destination), "control_dir": str(replacement_root / "control")}
        )
        restored = Store(destination)
        try:
            candidate = Service(service.cfg, restored, broker, data)
            candidate.kill_path.write_text("Replacement recovery must remain paused")
            with pytest.raises(RuntimeError, match="binding missing"):
                candidate.execute(decision, "process-drill")
            restored.control_path(service.cfg.control_dir, candidate.account_id, ".ledger").write_text(
                str(restored.path)
            )
        finally:
            restored.db.close()
    reopened = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, reopened, broker, data)
        assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reopened.scope == service.store.scope
        if replacement:
            assert not original_path.exists()
            assert reopened.path == destination
            assert recovered.kill_path.exists()
        if stage.startswith("revision"):
            committed = stage == "revision_committed"
            assert len(reopened.items("broker_revision")) == int(committed)
            assert len(reopened.events("broker_revision_approved")) == int(committed)
            assert reopened.get("fee", "activity") == original
            assert reopened.activities() == [corrected if committed else original]
            # The worker dies before post-approval reconciliation in both cases.
            assert reopened.get("audit")["fees"] == "1"
            recovered.acknowledge_revision(revision)
            recovered.acknowledge_revision(revision)
            assert reopened.activities() == [corrected]
            assert reopened.get("audit")["fees"] == "2"
            assert reopened.get("audit")["cash"] == "99998"
            assert len(reopened.items("broker_revision")) == 1
            assert len(reopened.events("broker_revision_approved")) == 1
            assert recovered.kill_path.exists()
            assert broker.submits == 0
        elif stage.startswith("adjustment"):
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
            cid = next(
                cid for cid, order in broker.orders.items() if order["side"] == ("sell" if selling else "buy")
            )
            submissions = 2 if selling else 1
            reservation = dec("0") if selling else dec("601")
            assert broker.orders[cid]["status"] == "pending_cancel"
            assert reopened.get(cid, "order")["status"] == "partially_filled"
            stale = copy.deepcopy(reopened.get(cid, "order"))
            assert recovered.kill_path.exists()
            assert recovered.reservation_summary() == reservation
            # A terminal broker order arrives before its additional fill activity.
            canceled = "canceled" in stage
            terminal = "canceled" if canceled else "filled"
            late = broker.fixture_fill(cid, "2" if canceled else "6", status=terminal)
            broker.activities.remove(late)
            # A non-listening loopback endpoint times out or refuses by OS;
            # neither outcome is a mocked exception or an actual broker request.
            with socket.socket() as unavailable:
                unavailable.bind(("127.0.0.1", 0))
                endpoint = f"http://127.0.0.1:{unavailable.getsockname()[1]}/snapshot"

                def partitioned_snapshot(*_):
                    with httpx.Client(timeout=0.2, trust_env=False) as client:
                        client.get(endpoint)
                    raise AssertionError("The offline partition unexpectedly answered")

                with monkeypatch.context() as patch:
                    patch.setattr(broker, "snapshot", partitioned_snapshot)
                    for _ in range(3):
                        reopened.db.close()
                        reopened = Store(service.cfg.database)
                        recovered = Service(service.cfg, reopened, broker, data)
                        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
                            recovered.execute(decision, "process-drill")
                        assert reopened.get("reconciliation")["ok"] is False
                        assert reopened.get("audit")["cash"] == ("99400" if selling else "99600")
                        assert reopened.get("audit")["shares"]["ACME"] == ("6" if selling else "4")
                        assert recovered.reservation_summary() == reservation
                        assert (
                            sum(dec(o["qty"]) - dec(o["filled_qty"]) for o in reopened.active_orders()) == 6
                        )
                        assert len(reopened.items("intent")) == submissions
                        assert len(reopened.items("activity")) == submissions
                        assert recovered.kill_path.exists()
                        assert broker.submits == submissions and len(broker.cancels) == 1
            with pytest.raises(LedgerError):
                recovered.reconcile()
            assert reopened.get("reconciliation")["ok"] is False
            with pytest.raises(LedgerError):
                recovered.execute(decision, "process-drill")
            assert broker.submits == submissions
            broker.activities.append(late)
            for _ in range(2):
                recovered.reconcile()
            assert reopened.get("reconciliation")["ok"] is True
            assert reopened.get("audit")["cash"] == (
                ("99600" if canceled else "100000") if selling else ("99400" if canceled else "99000")
            )
            assert dec(reopened.get("audit")["shares"].get("ACME", "0")) == (
                dec("4" if canceled else "0") if selling else dec("6" if canceled else "10")
            )
            assert len(reopened.items("activity")) == submissions + 1
            assert len(reopened.events("broker_activity")) == submissions + 1
            assert reopened.get(cid, "order")["status"] == terminal
            assert recovered.reservation_summary() == 0
            assert recovered.kill_path.exists()
            assert len(broker.cancels) == 1
            assert broker.submits == submissions
            # A lagging broker replica resurfaces the earlier partial order after recovery.
            terminal_order = copy.deepcopy(broker.orders[cid])
            broker.orders[cid] = stale
            recovered.reconcile()
            assert reopened.get(cid, "order")["status"] == terminal
            assert reopened.get(cid, "intent")["status"] == "complete"
            assert recovered.reservation_summary() == 0
            broker.orders[cid] = terminal_order
            recovered.reconcile()
            assert len(reopened.items("activity")) == submissions + 1
            assert len(reopened.events("broker_activity")) == submissions + 1
            assert broker.submits == submissions
            assert recovered.execute(decision, "new-after-process-restore")["status"] == "rejected"
        elif stage.startswith("accepted") and stage.endswith("lost_ack"):
            assert len(reopened.items("intent")) == 1
            assert not reopened.items("order")
            assert recovered.reservation_summary() == dec("1001")
            fills = list(broker.activities)
            if fills:
                broker.activities.clear()
                with pytest.raises(LedgerError):
                    recovered.reconcile()
                assert reopened.get("reconciliation")["ok"] is False
                for _ in range(2):
                    with pytest.raises(LedgerError):
                        recovered.execute(decision, "process-drill")
                    assert broker.submits == 1
                broker.activities.extend(fills)
            for _ in range(3):
                recovered.execute(decision, "process-drill")
                recovered.reconcile()
            assert reopened.get("reconciliation")["ok"] is True
            if replacement:
                assert recovered.kill_path.exists()
                assert not original_path.exists()
            qty = 4 if "partial" in stage else 10 if "filled" in stage else 0
            assert reopened.get("audit")["cash"] == str(100000 - 100 * qty)
            assert dec(reopened.get("audit")["shares"].get("ACME", "0")) == qty
            assert recovered.reservation_summary() == dec("601" if qty == 4 else "0" if qty == 10 else "1001")
            assert len(reopened.items("intent")) == len(reopened.items("order")) == 1
            assert len(reopened.items("activity")) == len(fills)
            assert len(reopened.events("broker_activity")) == len(fills)
            assert broker.submits == 1
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
