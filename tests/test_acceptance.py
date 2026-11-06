import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "acceptance", Path(__file__).parents[1] / "deploy/acceptance.py"
)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def prepare(service, decision):
    service.kill_path.write_text("operator pause")
    service.store.put(
        "research-ok", {"mode": "research", "status": "complete", "symbol": "ACME"}, "acceptance"
    )
    service.store.put("research-ok", {"decision": decision.model_dump(mode="json")}, "acceptance_research")


def test_research_once_and_pause(env, decision):
    service, _, _ = env
    prepare(service, decision)

    class Team:
        calls = 0

        def run(self, context):
            self.calls += 1
            assert service.kill_path.exists()
            assert context["research_only"]
            return {
                "team_reports": {key: {} for key in ("business", "catalysts", "valuation", "adversarial")},
                "decision": decision.model_dump(mode="json"),
            }

    team = Team()
    result = acceptance.run(service, "research", "ACME", "one", team=team)
    assert result["status"] == "complete"
    assert result["estimated_cap_usd"] == acceptance.D("4.75")
    assert service.cfg.research_max_model_calls == 24
    with pytest.raises(RuntimeError, match="already attempted"):
        acceptance.run(service, "research", "ACME", "one", team=team)
    assert team.calls == 1
    assert service.kill_path.read_text() == "operator pause"


@pytest.mark.parametrize("stage", ["before", "after"])
def test_paper_failure_restores_pause_and_does_not_retry(env, decision, monkeypatch, stage):
    service, broker, _ = env
    prepare(service, decision)
    if stage == "before":
        broker.open = False
    else:

        def fail(*args):
            assert not service.kill_path.exists()
            raise RuntimeError("submission failed")

        monkeypatch.setattr(service, "execute", fail)
    with pytest.raises(RuntimeError):
        acceptance.run(service, "paper-smoke", "ACME", "smoke", "research-ok", True)
    assert service.kill_path.read_text() == "operator pause"
    with pytest.raises(RuntimeError, match="already attempted"):
        acceptance.run(service, "paper-smoke", "ACME", "smoke", "research-ok", True)
    assert broker.submits == 0


def test_paper_fill_keeps_one_share_and_pause(env, decision, monkeypatch):
    service, broker, _ = env
    prepare(service, decision)
    monkeypatch.setattr(acceptance, "now", lambda: broker.at)
    original = service.execute

    def execute(*args):
        result = original(*args)
        broker.fixture_fill(service.client_order_id("explicit-paper-smoke:ACME"), qty="1")
        return result

    monkeypatch.setattr(service, "execute", execute)
    result = acceptance.run(service, "paper-smoke", "ACME", "smoke", "research-ok", True)
    assert result["status"] == "complete"
    assert broker.submits == 1
    assert broker.positions[0]["qty"] == "1"
    assert service.kill_path.read_text() == "operator pause"


def test_requires_review_and_listener_exclusion(env, decision):
    import fcntl

    service, broker, _ = env
    prepare(service, decision)
    with pytest.raises(RuntimeError, match="reviewed"):
        acceptance.run(service, "paper-smoke", "ACME", "smoke", "research-ok")
    path = service.store.control_path(service.cfg.control_dir, service.account_id, ".listener.lock")
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            acceptance.run(service, "research", "ACME", "one")
    assert broker.submits == 0
    assert service.kill_path.exists()


def test_failure_after_submission_requests_cancel_and_preserves_intent(env, decision, monkeypatch):
    service, broker, _ = env
    prepare(service, decision)
    monkeypatch.setattr(acceptance, "now", lambda: broker.at)
    original = service.execute

    def submit_then_fail(*args):
        original(*args)
        raise RuntimeError("post-submit interruption")

    monkeypatch.setattr(service, "execute", submit_then_fail)
    with pytest.raises(RuntimeError, match="post-submit"):
        acceptance.run(service, "paper-smoke", "ACME", "smoke", "research-ok", True)
    assert broker.submits == 1
    assert broker.cancels
    assert service.store.get(service.client_order_id("explicit-paper-smoke:ACME"), "intent")
    assert service.kill_path.read_text() == "operator pause"


def test_real_deepagents_under_acceptance_lock(env, decision):
    from test_research_team import team

    service, broker, _ = env
    prepare(service, decision)
    worker, model = team(env, decision)
    result = acceptance.run(service, "research", "ACME", "real-fixture", team=worker)
    assert result["status"] == "complete"
    saved = service.store.get("real-fixture", "acceptance_research")
    assert set(saved["team_reports"]) == {"business", "catalysts", "valuation", "adversarial"}
    assert saved["calculations"] and model.calls
    assert broker.submits == 0
    assert service.kill_path.exists()


def test_closed_market_uses_completed_regular_session(env):
    service, broker, _ = env
    broker.open = False
    result = acceptance.research_price(service, "ACME")
    assert result["historical"] and result["executable"] is False
    assert result["timestamp"].startswith("2026-10-20T16:00")
    assert result["reference_price"] == "100"


def test_fill_requires_matching_activity_quantity(env, decision):
    service, broker, _ = env
    result = service.execute(decision, "qty-proof", {})
    cid = service.client_order_id("qty-proof")
    assert result["status"] == "submitted"
    broker.fixture_fill(cid, qty="1")
    service.store.order(broker.lookup(cid))
    activity = dict(broker.activities[0], qty=".5")
    service.store.put(activity["id"], activity, "activity")
    assert acceptance.confirmed_fill(service.store, cid) is None
    service.store.put(activity["id"], broker.activities[0], "activity")
    assert acceptance.confirmed_fill(service.store, cid)["status"] == "complete"


def test_cancel_race_records_confirmed_late_fill(env, decision, monkeypatch):
    service, broker, _ = env
    prepare(service, decision)
    monkeypatch.setattr(acceptance, "now", lambda: broker.at)
    original = service.execute
    cid = service.client_order_id("explicit-paper-smoke:ACME")

    def interrupted(*args):
        original(*args)
        raise RuntimeError("interrupted after POST")

    def late_fill(order_id):
        broker.fixture_fill(cid, qty="1")

    monkeypatch.setattr(service, "execute", interrupted)
    monkeypatch.setattr(broker, "cancel", late_fill)
    with pytest.raises(RuntimeError, match="interrupted"):
        acceptance.run(service, "paper-smoke", "ACME", "late-fill", "research-ok", True)
    assert service.store.get("late-fill", "acceptance")["status"] == "complete"
    assert service.kill_path.exists()
    assert broker.submits == 1


def test_unrestricted_reservation_never_disguises_unknown_usage_as_settled(env):
    from trader.spending import DailyBudgetExceeded, DailyReservation, spending_status

    service, _, _ = env
    service.cfg.research_daily_cost_usd = acceptance.D(0)
    reservation = DailyReservation(service.store, service.cfg, "unbounded-crash", unrestricted_research=True)
    assert reservation.allowance is None
    saved = service.store.get(reservation.key, "research_spend", scope="global")
    assert saved["unknown_total"] and saved["status"] == "reserved"
    assert saved["usd"] == "0" and saved["unrestricted_research"]
    service.cfg.research_daily_cost_usd = acceptance.D(100)
    with pytest.raises(DailyBudgetExceeded):
        DailyReservation(service.store, service.cfg, "normal-after-crash")
    reservation.finish(acceptance.D(".123"), unknown=True)
    saved = service.store.get(reservation.key, "research_spend", scope="global")
    assert saved["status"] == "unknown" and saved["unknown_total"] and saved["usd"] == "0.123"
    assert spending_status(service.store, acceptance.D(100))["unbounded_unknown_runs"] == 1


@pytest.mark.parametrize(
    "failure", [acceptance.LedgerError("activity delayed"), acceptance.httpx.ReadTimeout("retry")]
)
def test_paper_fill_retries_transient_reconciliation_without_resubmission(
    env, decision, monkeypatch, failure
):
    service, broker, _ = env
    prepare(service, decision)
    monkeypatch.setattr(acceptance, "now", lambda: broker.at)
    execute = service.execute
    reconcile = service.reconcile
    pending = False

    def submit(*args):
        nonlocal pending
        result = execute(*args)
        broker.fixture_fill(service.client_order_id("explicit-paper-smoke:ACME"), qty="1")
        pending = True
        return result

    def delayed():
        nonlocal pending
        if pending:
            pending = False
            raise failure
        return reconcile()

    monkeypatch.setattr(service, "execute", submit)
    monkeypatch.setattr(service, "reconcile", delayed)
    sleeps = []
    result = acceptance.run(
        service, "paper-smoke", "ACME", "delayed-fill", "research-ok", True, sleep=sleeps.append
    )
    assert result["status"] == "complete"
    assert broker.submits == 1 and sleeps == [5]
    assert service.kill_path.read_text() == "operator pause"
