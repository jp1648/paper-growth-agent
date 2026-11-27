import copy
from datetime import timedelta

import pytest
from conftest import AT

from trader.lifecycle import approve_extension, effective_end
from trader.models import dt
from trader.paper_dispatch import fingerprint, source_fingerprint


def receipt(service):
    original = service.store.experiment["ends_at"]
    close = dt(original) + timedelta(days=20)
    schedule = {"study_id": "test-study", "expected_sessions": [{"close": close.isoformat()}]}
    return {
        "experiment_id": service.store.scope,
        "original_ends_at": original,
        "ends_at": (close + timedelta(hours=1)).isoformat(),
        "approved_at": AT.isoformat(),
        "approved_by": "fixture",
        "reason": "Complete paper window",
        "study_id": "test-study",
        "schedule": schedule,
        "schedule_sha256": fingerprint(schedule),
        "config_sha256": fingerprint(service.cfg.model_dump(mode="json")),
        "source_sha256": source_fingerprint(),
        "funding_evidence_sha256": "a" * 64,
        "prior_amendment_sha256": None,
    }


def test_extension_preserves_original_and_pause_and_is_idempotent(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    original = copy.deepcopy(service.store.experiment)
    assert effective_end(service.store, service.cfg) == original["ends_at"]
    service.kill("fixture")
    approval = receipt(service)
    assert approve_extension(service, approval) == approval
    assert approve_extension(service, approval) == approval
    assert effective_end(service.store, service.cfg) == approval["ends_at"]
    assert service.store.experiment == original and service.kill_path.exists()
    assert (
        service.store.db.execute(
            "SELECT COUNT(*) FROM journal WHERE kind='experiment_extension_approved'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(ValueError, match="immutable"):
        approve_extension(service, {**approval, "reason": "changed"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("experiment_id", "wrong"),
        ("original_ends_at", AT.isoformat()),
        ("source_sha256", "0" * 64),
        ("config_sha256", "0" * 64),
        ("schedule_sha256", "0" * 64),
        ("ends_at", "invalid"),
        ("ends_at", AT.isoformat()),
        ("approved_at", "invalid"),
        ("approved_by", ""),
        ("funding_evidence_sha256", ""),
        ("prior_amendment_sha256", "a" * 64),
        ("schedule", {"expected_sessions": []}),
    ],
)
def test_invalid_extension_rolls_back(env, monkeypatch, field, value):
    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    approval = {**receipt(service), field: value}
    with pytest.raises(ValueError):
        approve_extension(service, approval)
    assert service.store.get("approved", "experiment_extension") is None
    assert not service.store.db.execute(
        "SELECT 1 FROM journal WHERE kind='experiment_extension_approved'"
    ).fetchone()


def test_extension_requires_pause_and_preexpiry(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    approval = receipt(service)
    with pytest.raises(ValueError, match="Pause"):
        approve_extension(service, approval)
    service.kill("fixture")
    monkeypatch.setattr("trader.lifecycle.now", lambda: dt(service.store.experiment["ends_at"]))
    with pytest.raises(ValueError, match="retroactively"):
        approve_extension(service, approval)


def test_extension_atomic_journal_failure(env, monkeypatch):
    import sqlite3

    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    service.store.db.execute(
        "CREATE TRIGGER reject_extension BEFORE INSERT ON journal WHEN NEW.kind='experiment_extension_approved' BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        approve_extension(service, receipt(service))
    assert service.store.get("approved", "experiment_extension") is None


def test_extension_rejects_active_study_and_runtime_change(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    service.store.put("active", {"status": "fixture"}, "paper_study")
    with pytest.raises(ValueError, match="frozen study"):
        approve_extension(service, receipt(service))


def test_extension_report_keeps_original_and_exposes_invalid_binding(env, monkeypatch):
    from trader.reports import portfolio_report

    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    approval = receipt(service)
    approve_extension(service, approval)
    report = portfolio_report(service)
    assert report["experiment"]["ends_at"] == approval["original_ends_at"]
    assert report["lifecycle"]["effective_ends_at"] == approval["ends_at"]
    service.cfg.research_daily_cost_usd += 1
    with pytest.raises(ValueError, match="binding"):
        effective_end(service.store, service.cfg)
    assert not portfolio_report(service)["lifecycle"]["valid"]


def test_reconciliation_grace_does_not_extend_execution(env, monkeypatch):
    from trader.lifecycle import execution_end

    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    approval = receipt(service)
    approve_extension(service, approval)
    assert dt(execution_end(service.store, service.cfg)) < dt(effective_end(service.store, service.cfg))
    assert execution_end(service.store, service.cfg) == approval["schedule"]["expected_sessions"][-1]["close"]


@pytest.mark.parametrize("bad", ["bad", {"ends_at": None}, {"approved_at": 7}])
def test_malformed_persisted_extension_fails_closed(env, monkeypatch, bad, decision):
    service, broker, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.execute(decision, "before-corruption")
    reserved = service.reservation_summary()
    service.store.put("approved", bad, "experiment_extension")
    with pytest.raises(ValueError):
        effective_end(service.store, service.cfg)
    service.cancel_expired()
    assert len(broker.cancels) == 1
    assert service.reservation_summary() == reserved > 0


def test_extension_cutoff_rechecked_before_submission(env, monkeypatch, decision):
    service, broker, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    approval = receipt(service)
    approve_extension(service, approval)
    service.kill_path.unlink()
    submit = broker.submit

    def cross_cutoff(payload, *, before_send=None):
        monkeypatch.setattr(
            "trader.service.now", lambda: dt(approval["schedule"]["expected_sessions"][-1]["close"])
        )
        return submit(payload, before_send=before_send)

    monkeypatch.setattr(broker, "submit", cross_cutoff)
    result = service.execute(decision, "cross-extension-cutoff")
    assert result["status"] == "rejected" and broker.submits == 0
    assert any("Experiment ended" in reason for reason in result["reasons"])


def test_extension_survives_checkpoint_copy(env, monkeypatch, tmp_path):
    import sqlite3

    from trader.store import Store

    service, _, _ = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    approval = receipt(service)
    approve_extension(service, approval)
    path = tmp_path / "restored.sqlite"
    with sqlite3.connect(path) as target:
        service.store.db.backup(target)
    restored = Store(path)
    try:
        assert restored.experiment == service.store.experiment
        assert effective_end(restored, service.cfg) == approval["ends_at"]
    finally:
        restored.db.close()


def test_concurrent_identical_approvals_commit_once(env, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from trader.service import Service
    from trader.store import Store

    service, broker, data = env
    monkeypatch.setattr("trader.lifecycle.now", lambda: AT)
    service.kill("fixture")
    approval = receipt(service)
    barrier = Barrier(2)

    def approve():
        store = Store(service.cfg.database)
        try:
            worker = Service(service.cfg, store, broker, data)
            barrier.wait(timeout=5)
            return approve_extension(worker, approval)
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(approve) for _ in range(2)]
        assert all(f.result(timeout=10) == approval for f in futures)
    assert (
        service.store.db.execute(
            "SELECT COUNT(*) FROM journal WHERE kind='experiment_extension_approved'"
        ).fetchone()[0]
        == 1
    )
