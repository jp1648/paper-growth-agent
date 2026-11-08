"""Restore real SQLite backups while the offline broker advances independently."""

import sqlite3
from contextlib import closing
from datetime import timedelta

import pytest
from conftest import AT

from trader.accounting import LedgerError
from trader.models import dec
from trader.service import Service
from trader.store import Store


@pytest.mark.parametrize(
    "stage",
    [
        "before_intent",
        "before_intent_then_filled",
        "prepared",
        "prepared_then_filled",
        "checkpoint_then_filled",
    ],
)
def test_restored_backup_preserves_pause_and_never_repeats_submission(env, decision, tmp_path, stage):
    service, broker, data = env
    original_id = service.store.scope
    backup = None
    if stage.startswith("before_intent"):
        backup = service.store.backup()
    if stage == "checkpoint_then_filled":
        service.store.backup()  # Deliberately leave the daily backup older than the order.
    submit = broker.submit

    def capture_before_post(payload, **kwargs):
        nonlocal backup
        if backup is None:
            backup = (
                next(service.store.path.parent.glob("backups/intent-*.sqlite"))
                if stage == "checkpoint_then_filled"
                else service.store.backup()
            )
        return submit(payload, **kwargs)

    broker.submit = capture_before_post
    result = service.execute(decision, "restore-drill")
    assert backup.exists()
    assert len(list(backup.parent.glob("intent-*.sqlite"))) == 1
    cid = result["client_order_id"]
    if stage.endswith("then_filled"):
        broker.fixture_fill(cid)
        service.reconcile()
    service.kill_path.write_text("Restore drill: remain paused")
    registry = service.store.control_path(service.cfg.control_dir, service.account_id, ".ledger")
    binding = registry.read_text()
    # Keep the post-backup source for audit; restoration never destroys our only copy.
    retained = tmp_path / "retained-source.sqlite"
    with closing(sqlite3.connect(retained)) as target:
        service.store.db.backup(target)
    service.store.db.close()
    with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(service.cfg.database)) as target:
        source.backup(target)
    restored = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, restored, broker, data)
        assert restored.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.scope == original_id
        assert registry.read_text() == binding
        if stage.startswith("before_intent"):
            reason = "External order" if stage == "before_intent" else "no matching approved order"
            with pytest.raises(LedgerError, match=reason):
                recovered.reconcile()
            with pytest.raises(LedgerError, match=reason):
                recovered.execute(decision, "restore-drill")
            assert not restored.items("intent")
        else:
            recovered.reconcile()
            recovered.execute(decision, "restore-drill")
            recovered.execute(decision, "restore-drill")
            assert len(restored.items("intent")) == 1
            if stage.endswith("then_filled"):
                assert restored.get("audit")["cash"] == "99000"
                assert restored.get("audit")["shares"]["ACME"] == "10"
                assert len(restored.items("activity")) == 1
                assert recovered.reservation_summary() == 0
            else:
                assert recovered.reservation_summary() == dec("1001")
            denied = recovered.execute(decision, "new-after-restore")
            assert denied["status"] == "rejected"
            assert "Kill switch is active" in denied["reasons"]
        assert recovered.kill_path.exists()
        assert broker.submits == 1
        assert broker.orders[cid]["client_order_id"] == cid
        with closing(sqlite3.connect(retained)) as original:
            assert original.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.db.close()


def test_backup_publish_disk_failure_preserves_source_and_cleans_partial(env, monkeypatch):
    import errno

    service, _, _ = env
    source_id = service.store.scope

    def full_disk(*_):
        raise OSError(errno.ENOSPC, "fixture full disk")

    monkeypatch.setattr("trader.store.os.replace", full_disk)
    with pytest.raises(OSError) as failure:
        service.store.backup()
    assert failure.value.errno == errno.ENOSPC
    assert service.store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert service.store.scope == source_id
    assert list((service.store.path.parent / "backups").iterdir()) == []


@pytest.mark.parametrize("damage", ["corrupt", "other_experiment", "public_permissions"])
def test_existing_backup_is_verified_not_silently_reused(env, damage):
    service, _, _ = env
    saved = service.store.backup()
    if damage == "corrupt":
        saved.write_bytes(b"not a database")
    elif damage == "public_permissions":
        saved.chmod(0o644)
    else:
        with closing(sqlite3.connect(saved)) as db:
            db.execute("UPDATE documents SET body='{}' WHERE scope='global' AND key='active'")
            db.commit()
    before = saved.read_bytes()
    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        service.store.backup()
    assert saved.read_bytes() == before
    assert service.store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_flushes_file_before_publish_and_directory_after(env, monkeypatch):
    import os
    import stat

    service, _, _ = env
    operations = []
    real_sync, real_replace = os.fsync, os.replace

    def sync(fd):
        operations.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_sync(fd)

    def replace(src, dst):
        operations.append("publish")
        real_replace(src, dst)

    monkeypatch.setattr("trader.store.os.fsync", sync)
    monkeypatch.setattr("trader.store.os.replace", replace)
    service.store.backup()
    assert operations == ["file", "publish", "directory"]


@pytest.mark.parametrize("stage", ["file", "directory"])
def test_backup_flush_failure_never_reports_success(env, monkeypatch, stage):
    import errno
    import os
    import stat

    service, _, _ = env
    original = os.fsync

    def fail(fd):
        kind = "directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        if kind == stage:
            raise OSError(errno.EIO, "Fixture storage flush failure")
        original(fd)

    monkeypatch.setattr("trader.store.os.fsync", fail)
    with pytest.raises(OSError, match="storage flush"):
        service.store.backup()
    files = list((service.store.path.parent / "backups").iterdir())
    assert len(files) == (1 if stage == "directory" else 0)
    assert not any(p.name.startswith(".backup-") for p in files)
    monkeypatch.setattr("trader.store.os.fsync", original)
    saved = service.store.backup()
    with closing(sqlite3.connect(saved)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


@pytest.mark.parametrize("broken", [False, True])
def test_backup_symlink_is_rejected_without_touching_target(env, tmp_path, broken):
    service, _, _ = env
    saved = service.store.backup()
    target = tmp_path / "preserved.sqlite"
    saved.rename(target)
    saved.symlink_to(target)
    before = target.read_bytes()
    if broken:
        target.unlink()
    with pytest.raises(RuntimeError, match="private regular file"):
        service.store.backup()
    assert saved.is_symlink()
    if not broken:
        assert target.read_bytes() == before
    else:
        assert not target.exists()


def test_failed_checkpoint_rejects_before_broker_post_and_never_retries_same_intent(
    env, decision, monkeypatch
):
    import errno

    service, broker, _ = env

    def full_disk(*args):
        raise OSError(errno.ENOSPC, "Fixture full disk")

    monkeypatch.setattr("trader.store.os.replace", full_disk)
    result = service.execute(decision, "checkpoint-failure")
    assert result["status"] == "rejected"
    assert result["reasons"] == ["Execution checkpoint failed"]
    assert service.execute(decision, "checkpoint-failure")["status"] == "rejected"
    assert broker.submits == 0
    assert service.reservation_summary() == 0
    assert len(service.store.events("execution_checkpoint_failed")) == 1


@pytest.mark.parametrize("interrupt", ["stale", "pause"])
def test_checkpoint_delay_or_pause_prevents_submission(env, decision, monkeypatch, interrupt):
    service, broker, _ = env
    backup = service.store.backup

    def interrupted(*args, **kwargs):
        result = backup(*args, **kwargs)
        if interrupt == "stale":
            monkeypatch.setattr("trader.service.now", lambda: AT + timedelta(minutes=2))
        else:
            service.kill_path.write_text("Pause during checkpoint")
        return result

    monkeypatch.setattr(service.store, "backup", interrupted)
    result = service.execute(decision, "checkpoint-interruption")
    assert result["status"] == "rejected"
    assert any(("stale" if interrupt == "stale" else "Kill switch") in r for r in result["reasons"])
    assert broker.submits == 0
    assert len(list(service.store.path.parent.glob("backups/intent-*.sqlite"))) == 1


def test_existing_checkpoint_with_changed_intent_blocks_post(env, decision, monkeypatch):
    service, broker, _ = env
    backup = service.store.backup

    def altered(*, intent_id):
        path = backup(intent_id=intent_id)
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE documents SET body='{}' WHERE kind='intent'")
            db.commit()
        return backup(intent_id=intent_id)

    monkeypatch.setattr(service.store, "backup", altered)
    result = service.execute(decision, "tampered-checkpoint")
    assert result["status"] == "rejected" and broker.submits == 0
    assert "does not match" in result["error"]


@pytest.mark.parametrize("recovery_fault", [None, "timeout", "missing_order"])
def test_full_ledger_after_broker_acceptance_recovers_without_resubmit(
    env, decision, monkeypatch, recovery_fault
):
    service, broker, data = env
    submit = broker.submit

    def accepted_then_capacity(payload, *, before_send=None):
        order = submit(payload, before_send=before_send)
        pages = service.store.db.execute("PRAGMA page_count").fetchone()[0]
        service.store.db.execute(f"PRAGMA max_page_count={pages}")
        # Additional broker response metadata forces allocation at the save boundary.
        order["fixture_metadata"] = "x" * 100_000
        return order

    monkeypatch.setattr(broker, "submit", accepted_then_capacity)
    with pytest.raises(sqlite3.OperationalError) as failure:
        service.execute(decision, "accepted-ledger-full")
    assert failure.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert broker.submits == 1
    assert service.store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert len(service.store.items("intent")) == 1
    assert service.store.items("order") == []
    assert service.reservation_summary() == dec("1001")
    service.store.db.close()
    restored = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, restored, broker, data)
        if recovery_fault:
            import httpx

            snapshot = broker.snapshot

            def unavailable_snapshot(*args):
                if recovery_fault == "timeout":
                    raise httpx.ReadTimeout("Offline recovery fixture timeout")
                return snapshot(*args).model_copy(update={"orders": []})

            with monkeypatch.context() as patch:
                patch.setattr(broker, "snapshot", unavailable_snapshot)
                for _ in range(2):
                    with pytest.raises((httpx.ReadTimeout, LedgerError)):
                        recovered.execute(decision, "accepted-ledger-full")
                    assert restored.get("reconciliation")["ok"] is False
                    assert recovered.reservation_summary() == dec("1001")
                    assert broker.submits == 1
                    assert restored.items("order") == []
                    assert len(restored.items("intent")) == 1
        for _ in range(2):
            recovered.execute(decision, "accepted-ledger-full")
        assert broker.submits == 1
        assert len(restored.items("order")) == 1
        cid = next(iter(broker.orders))
        broker.fixture_fill(cid)
        recovered.reconcile()
        assert restored.get("audit")["cash"] == "99000"
        assert restored.get("audit")["shares"]["ACME"] == "10"
        assert len(restored.items("activity")) == 1
        assert recovered.reservation_summary() == 0
    finally:
        restored.db.close()


@pytest.mark.parametrize(
    "terminal,late_qty,total_qty,cash", [("filled", "6", "10", "99000"), ("canceled", "2", "6", "99400")]
)
def test_restore_partial_cancel_race_survives_outage_restarts_and_delayed_activity(
    env, decision, tmp_path, monkeypatch, terminal, late_qty, total_qty, cash
):
    import httpx

    service, broker, data = env
    original_id = service.store.scope
    result = service.execute(decision, "combined-restore")
    cid = result["client_order_id"]
    broker.fixture_fill(cid, "4", status="partially_filled")
    service.reconcile()
    service.kill("Restore drill: cancellation remains unresolved")
    assert broker.orders[cid]["status"] == "pending_cancel"
    assert service.reservation_summary() == dec("601")
    backup = service.store.backup()
    # Broker progresses independently while the old process and its disk are unavailable.
    late = broker.fixture_fill(cid, late_qty, status=terminal)
    broker.activities.remove(late)
    retained = tmp_path / "pre-restore-retained.sqlite"
    with closing(sqlite3.connect(retained)) as target:
        service.store.db.backup(target)
    service.store.db.close()
    with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(service.cfg.database)) as target:
        source.backup(target)
    snapshot = broker.snapshot

    def unavailable(*args):
        raise httpx.ReadTimeout("Isolated broker partition")

    monkeypatch.setattr(broker, "snapshot", unavailable)
    for _ in range(3):
        restored = Store(service.cfg.database)
        try:
            recovered = Service(service.cfg, restored, broker, data)
            assert restored.scope == original_id
            with pytest.raises(httpx.ReadTimeout):
                recovered.execute(decision, "combined-restore")
            assert restored.get("reconciliation")["ok"] is False
            assert recovered.reservation_summary() == dec("601")
            assert restored.get("audit")["cash"] == "99600"
            assert recovered.kill_path.exists() and broker.submits == 1
        finally:
            restored.db.close()
    monkeypatch.setattr(broker, "snapshot", snapshot)
    restored = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, restored, broker, data)
        # Even a terminal cancellation must not release the inconsistent-account gate.
        with pytest.raises(LedgerError):
            recovered.execute(decision, "new-during-delayed-activity")
        assert restored.get("reconciliation")["ok"] is False
        assert broker.submits == 1 and len(restored.items("intent")) == 1
    finally:
        restored.db.close()
    broker.activities.append(late)
    restored = Store(service.cfg.database)
    try:
        recovered = Service(service.cfg, restored, broker, data)
        for _ in range(2):
            recovered.reconcile()
            recovered.execute(decision, "combined-restore")
        assert restored.get("reconciliation")["ok"] is True
        assert restored.get("audit")["cash"] == cash
        assert restored.get("audit")["shares"]["ACME"] == total_qty
        assert len(restored.items("activity")) == 2
        assert len(restored.events("broker_activity")) == 2
        assert restored.get(cid, "order")["status"] == terminal
        assert recovered.reservation_summary() == 0
        assert recovered.execute(decision, "new-after-recovery")["status"] == "rejected"
        assert broker.submits == 1 and len(broker.cancels) == 1
        assert recovered.kill_path.exists()
        assert restored.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with closing(sqlite3.connect(retained)) as original:
            assert original.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        restored.db.close()
