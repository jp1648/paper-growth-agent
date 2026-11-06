"""Restore real SQLite backups while the offline broker advances independently."""

import sqlite3
from contextlib import closing

import pytest

from trader.accounting import LedgerError
from trader.models import dec
from trader.service import Service
from trader.store import Store


@pytest.mark.parametrize(
    "stage", ["before_intent", "before_intent_then_filled", "prepared", "prepared_then_filled"]
)
def test_restored_backup_preserves_pause_and_never_repeats_submission(env, decision, tmp_path, stage):
    service, broker, data = env
    original_id = service.store.scope
    backup = None
    if stage.startswith("before_intent"):
        backup = service.store.backup()
    submit = broker.submit

    def capture_before_post(payload):
        nonlocal backup
        if backup is None:
            backup = service.store.backup()
        return submit(payload)

    broker.submit = capture_before_post
    result = service.execute(decision, "restore-drill")
    assert list(backup.parent.iterdir()) == [backup]
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
            if stage == "prepared_then_filled":
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
