import copy
from datetime import timedelta

import pytest
from conftest import AT

from trader.accounting import LedgerError
from trader.models import D
from trader.service import Service
from trader.store import Store


def test_submit_is_not_fill_and_restart_deduplicates(env, decision):
    service, broker, data = env
    result = service.execute(decision, "one")
    assert result["status"] == "submitted"
    service.reconcile()
    assert service.store.get("snapshot")["positions"] == []
    assert service.store.get("audit")["cash"] == "100000"
    assert service.reservation_summary() == D("1001")
    restarted = Service(service.cfg, Store(service.cfg.database), broker, data)
    restarted.execute(decision, "one")
    assert broker.submits == 1
    broker.fixture_fill(result["client_order_id"])
    restarted.reconcile()
    assert restarted.store.get("snapshot")["positions"][0]["qty"] == "10"
    assert restarted.reservation_summary() == 0
    assert D(restarted.store.get("audit")["cash"]) == D("99000")
    restarted.store.db.close()


def test_partial_fills_duplicate_and_out_of_order(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "partial")["client_order_id"]
    old = copy.deepcopy(broker.orders[cid])
    broker.fixture_fill(cid, "4", status="partially_filled")
    service.reconcile()
    service.reconcile()
    service.store.order(old)
    assert service.store.get(cid, "order")["filled_qty"] == "4"
    assert service.reservation_summary() == D("601")
    assert len(service.store.items("activity")) == 1
    broker.fixture_fill(cid, "6")
    service.reconcile()
    assert D(service.store.get("audit")["cash"]) == D("99000")
    assert service.reservation_summary() == 0


def test_cancel_race_keeps_reservation_and_accepts_fill(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "cancel")["client_order_id"]
    service.kill("test pause")
    assert broker.cancels
    assert service.store.get(cid, "order")["status"] == "pending_cancel"
    assert service.reservation_summary() == D("1001")
    broker.fixture_fill(cid)
    service.reconcile()
    assert service.reservation_summary() == 0
    assert service.store.get("snapshot")["positions"][0]["qty"] == "10"
    rejected = service.execute(decision.model_copy(update={"symbol": "OTHER"}), "second")
    assert "Kill switch is active" in rejected["reasons"]
    assert broker.submits == 1


@pytest.mark.parametrize("status", ["canceled", "expired", "rejected"])
def test_terminal_releases_unfilled_reservation(env, decision, status):
    service, broker, _ = env
    cid = service.execute(decision, status)["client_order_id"]
    broker.fixture_fill(cid, "4", status="partially_filled")
    service.reconcile()
    broker.orders[cid].update(status=status, updated_at=(AT + timedelta(seconds=10)).isoformat())
    service.reconcile()
    assert service.reservation_summary() == 0
    assert D(service.store.get("audit")["cash"]) == D("99600")


def test_timeout_looks_up_accepted_order_without_retry(env, decision):
    service, broker, _ = env
    broker.mode = "timeout_accepted"
    result = service.execute(decision, "timeout")
    assert result["status"] == "submitted"
    service.execute(decision, "timeout")
    assert broker.submits == 1


def test_unknown_timeout_pauses_and_preserves_reservation(env, decision):
    service, broker, _ = env
    broker.mode = "timeout_missing"
    result = service.execute(decision, "unknown")
    assert result["status"] == "unknown"
    assert service.reservation_summary() == D("1001")
    assert not service.store.get("reconciliation")["ok"]
    with pytest.raises(LedgerError, match="Unresolved submission"):
        service.execute(decision, "new-key")
    assert broker.submits == 1


def test_exit_thesis_invalidated_closes_position(env, decision):
    service, broker, data = env
    cid = service.execute(decision, "entry")["client_order_id"]
    broker.fixture_fill(cid)
    service.reconcile()
    data.bid, data.ask = D("110"), D("110.01")
    exit_decision = decision.model_copy(
        update={"action": "SELL", "limit_price": D("110"), "exit_reason": "thesis_invalid"}
    )
    sell = service.execute(exit_decision, "exit")
    broker.fixture_fill(sell["client_order_id"], price="110")
    service.reconcile()
    assert service.store.get("snapshot")["positions"] == []
    assert D(service.store.get("audit")["realized_average_cost"]) == D("100")
    assert D(service.store.get("audit")["cash"]) == D("100100")


def test_cash_drift_blocks_execution(env, decision):
    service, broker, _ = env
    broker.raw_account["cash"] = "99000"
    with pytest.raises(LedgerError, match="Cash discrepancy"):
        service.execute(decision, "drift")
    assert broker.submits == 0
    assert not service.store.get("reconciliation")["ok"]


def test_changed_intent_cannot_be_reused(env, decision):
    service, _, _ = env
    service.execute(decision, "same")
    with pytest.raises(RuntimeError, match="different decision"):
        service.execute(decision.model_copy(update={"quantity": D("2")}), "same")


def test_no_automatic_reset_and_explicit_new_experiment(env):
    service, _, _ = env
    original = service.store.scope
    with pytest.raises(RuntimeError, match="already exists"):
        service.initialize()
    service.initialize(new_experiment=True)
    assert service.store.scope != original
    assert service.store.get(original, "experiment", "global")


def test_cash_movement_requires_explicit_acknowledgment(env):
    service, broker, _ = env
    broker.activities.append(
        {"id": "deposit", "activity_type": "CSD", "date": "2026-10-21", "net_amount": "1000"}
    )
    broker.raw_account.update(cash="101000", equity="101000")
    with pytest.raises(LedgerError, match="Unacknowledged"):
        service.reconcile()
    service.kill_path.write_text("Operator reviewing account adjustment")
    service.acknowledge_adjustment(
        {"id": "deposit", "cash": "1000", "reason": "Explicit experiment contribution"}
    )
    assert service.store.get("audit")["capital_flows"] == "1000"


def test_corporate_action_pauses_until_audited_adjustment(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "entry")["client_order_id"]
    broker.fixture_fill(cid)
    service.reconcile()
    broker.activities.append(
        {"id": "split", "activity_type": "SPLIT", "date": "2026-10-22", "symbol": "ACME", "qty": "10"}
    )
    broker.positions[0]["qty"] = "20"
    with pytest.raises(LedgerError, match="Unsupported corporate"):
        service.reconcile()
    service.kill_path.write_text("Operator reviewing account adjustment")
    service.acknowledge_adjustment(
        {
            "id": "split",
            "cash": "0",
            "reason": "Broker-confirmed 2:1 split",
            "positions": {"ACME": {"quantity_delta": "10", "basis_delta": "0"}},
        }
    )
    assert service.store.get("audit")["shares"]["ACME"] == "20"


def test_second_database_same_account_is_rejected(env, tmp_path):
    service, broker, data = env
    other = Service(service.cfg, Store(tmp_path / "other.sqlite"), broker, data)
    with pytest.raises(RuntimeError, match="another ledger"):
        other.initialize()
    other.store.db.close()


def test_recovered_unknown_order_then_fill_survives_restart(env, decision):
    service, broker, data = env
    broker.mode = "timeout_missing"
    original = service.execute(decision, "lost-response")
    cid = original["client_order_id"]
    broker.orders[cid] = {
        "client_order_id": cid,
        "symbol": "ACME",
        "side": "buy",
        "qty": "10",
        "filled_qty": "0",
        "id": "eventually-visible",
        "type": "limit",
        "limit_price": "100",
        "time_in_force": "day",
        "extended_hours": False,
        "status": "new",
        "created_at": AT.isoformat(),
        "updated_at": AT.isoformat(),
    }
    restarted = Service(service.cfg, Store(service.cfg.database), broker, data)
    restarted.reconcile()
    assert restarted.store.get(cid, "intent")["status"] == "submitted"
    broker.fixture_fill(cid)
    restarted.reconcile()
    assert restarted.store.get(cid, "intent")["status"] == "complete"
    assert broker.submits == 1
    restarted.store.db.close()


def test_concurrent_executors_cannot_submit_conflicting_orders(env, decision):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    service, broker, data = env
    barrier = Barrier(2)

    def attempt(key):
        local = Service(service.cfg, Store(service.cfg.database), broker, data)
        barrier.wait(timeout=5)
        try:
            return local.execute(decision, key)["status"]
        finally:
            local.store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["concurrent-1", "concurrent-2"]))
    assert sorted(results) == ["rejected", "submitted"]
    assert broker.submits == 1


def test_new_account_new_experiment_preserves_old_history(env):
    service, broker, _ = env
    old_id = service.store.scope
    broker.raw_account["id"] = "new-paper-account"
    service.initialize(new_experiment=True)
    assert service.store.scope != old_id
    assert service.store.experiment["account_id"] == "new-paper-account"
    assert service.store.get(old_id, "experiment", "global")


def test_old_process_cannot_write_after_experiment_changes(env):
    service, broker, data = env
    old = Service(service.cfg, Store(service.cfg.database), broker, data)
    service.initialize(new_experiment=True)
    with pytest.raises(RuntimeError, match="experiment changed"):
        old.reconcile()
    old.store.db.close()


def test_fill_event_before_activity_pauses_then_recovers(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "lagged-activity")["client_order_id"]
    broker.orders[cid].update(
        filled_qty="10", status="filled", updated_at=(AT + timedelta(seconds=1)).isoformat()
    )
    with pytest.raises(LedgerError, match="cumulative fill"):
        service.reconcile()
    assert not service.kill_path.exists()
    broker.orders[cid]["filled_qty"] = "0"
    broker.fixture_fill(cid)
    service.reconcile()
    assert service.store.get("reconciliation")["ok"]


def test_late_fill_after_terminal_cancel_refreshes_order(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "late-after-cancel")["client_order_id"]
    broker.orders[cid].update(status="canceled", updated_at=(AT + timedelta(seconds=1)).isoformat())
    service.reconcile()
    assert service.store.get(cid, "intent")["status"] == "complete"
    assert service.reservation_summary() == 0
    broker.fixture_fill(cid)
    service.reconcile()
    assert service.store.get(cid, "order")["status"] == "filled"
    assert service.store.get("snapshot")["positions"][0]["qty"] == "10"


def test_supplied_fees_affect_cash_and_are_reported(env):
    service, broker, _ = env
    broker.activities.append(
        {"id": "fee", "activity_type": "FEE", "date": "2026-10-21", "net_amount": "-0.03"}
    )
    broker.raw_account.update(cash="99999.97", equity="99999.97")
    service.reconcile()
    assert D(service.store.get("audit")["fees"]) == D(".03")
    assert D(service.store.get("audit")["cash"]) == D("99999.97")


def test_partial_fill_cancellation_cooldown_uses_last_execution(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "partial-cooldown")["client_order_id"]
    broker.fixture_fill(cid, "4", status="partially_filled")
    broker.orders[cid].update(status="canceled", updated_at=(AT + timedelta(seconds=10)).isoformat())
    service.reconcile()
    assert service.store.get("ACME", "last_trade")["at"] == broker.activities[0]["transaction_time"]


def test_prebaseline_funding_requires_explicit_zero_delta_acknowledgment(env):
    service, broker, _ = env
    activity = {
        "id": "baseline-cash",
        "activity_type": "JNLC",
        "date": "2026-10-20",
        "created_at": "2026-10-20T12:00:00Z",
        "net_amount": "100000",
        "status": "executed",
    }
    broker.activities.append(activity)
    with pytest.raises(LedgerError):
        service.reconcile()
    service.kill_path.write_text("Operator reviewing account adjustment")
    service.acknowledge_adjustment(
        {
            "id": activity["id"],
            "cash": "0",
            "positions": {},
            "reason": "Verified initial funding included in baseline cash",
        }
    )
    service.reconcile()
    assert service.store.get(activity["id"], "activity") == activity
    assert len(service.store.events("explicit_adjustment")) == 1
    assert service.store.get("reconciliation")["ok"]


def test_broker_time_in_force_must_match_approved_intent(env, decision):
    from trader.accounting import LedgerError

    service, broker, _ = env
    result = service.execute(decision, "tif-check")
    order = dict(broker.orders[result["client_order_id"]])
    order["time_in_force"] = "ioc"
    with pytest.raises(LedgerError, match="approved intent"):
        service.validate_order(order, result)
    service.cfg.time_in_force = "ioc"
    order["time_in_force"] = "day"
    service.validate_order(order, result)  # Later config changes do not rewrite the approval.


def test_missing_historical_activity_blocks_even_when_balances_match(env, decision):
    service, broker, _ = env
    result = service.execute(decision, "missing-history")
    broker.fixture_fill(result["client_order_id"])
    service.reconcile()
    original = copy.deepcopy(broker.activities)
    saved = service.store.activities()
    broker.activities = []
    with pytest.raises(LedgerError, match="missing.*historical|Historical.*missing"):
        service.reconcile()
    assert service.store.activities() == saved
    assert service.store.get("reconciliation")["ok"] is False
    assert service.kill_path.exists()
    assert broker.submits == 1
    broker.activities = original
    service.reconcile()
    assert service.store.get("reconciliation")["ok"] is True
    assert service.kill_path.exists()
    assert service.store.activities() == saved
    assert broker.submits == 1


def test_conflicting_duplicate_activity_rejected_before_recording(env, decision):
    service, broker, _ = env
    result = service.execute(decision, "duplicate-history")
    broker.fixture_fill(result["client_order_id"])
    original = copy.deepcopy(broker.activities)
    conflicting = {**original[0], "price": "1"}
    broker.activities.insert(0, conflicting)
    with pytest.raises(LedgerError, match="Conflicting.*activities"):
        service.reconcile()
    assert service.store.activities() == []
    assert service.kill_path.exists()
    broker.activities = original
    service.reconcile()
    assert service.store.activities() == original
    assert broker.submits == 1


def test_identical_activity_overlap_is_recorded_once(env, decision):
    service, broker, _ = env
    result = service.execute(decision, "overlapping-history")
    broker.fixture_fill(result["client_order_id"])
    original = copy.deepcopy(broker.activities)
    broker.activities.extend(copy.deepcopy(original))
    service.reconcile()
    service.reconcile()
    assert service.store.activities() == original
    assert len(service.store.events("broker_activity")) == 1
    assert not service.kill_path.exists()
    assert broker.submits == 1


def test_history_failure_preserves_operator_pause_across_restart(env, decision):
    service, broker, data = env
    result = service.execute(decision, "history-restart")
    broker.fixture_fill(result["client_order_id"])
    service.reconcile()
    original = copy.deepcopy(broker.activities)
    service.kill_path.write_text("Operator investigation in progress")
    broker.activities = []
    with pytest.raises(LedgerError, match="Historical"):
        service.reconcile()
    broker.activities = original
    restarted = Service(service.cfg, Store(service.cfg.database), broker, data)
    try:
        restarted.reconcile()
        assert restarted.kill_path.read_text() == "Operator investigation in progress"
        denied = restarted.execute(decision, "new-after-history-recovery")
        assert denied["status"] == "rejected"
        assert "Kill switch is active" in denied["reasons"]
        assert broker.submits == 1
        restarted.resume()
        assert not restarted.kill_path.exists()
    finally:
        restarted.store.db.close()


def test_revision_preflight_does_not_partially_import_new_activities(env, decision):
    service, broker, _ = env
    result = service.execute(decision, "revision-preflight")
    broker.fixture_fill(result["client_order_id"], qty="5", status="partially_filled")
    service.reconcile()
    saved = service.store.activities()
    broker.fixture_fill(result["client_order_id"], qty="5")
    broker.activities[0] = {**broker.activities[0], "price": "1"}
    broker.activities.reverse()  # New activity arrives before the revised historical one.
    with pytest.raises(LedgerError, match="revised activity"):
        service.reconcile()
    assert service.store.activities() == saved
    assert len(service.store.events("broker_activity")) == 1
    assert service.kill_path.exists()
    assert service.reservation_summary() > 0
    assert broker.submits == 1
