import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from conftest import AT

from trader.health import check_health
from trader.models import dec
from trader.runner import Runner
from trader.spending import DailyBudgetExceeded, DailyReservation, spending_status
from trader.store import Store
from trader.streams import listen, run_command


def test_durable_reservation_reuses_original_authority_without_new_spending(env, monkeypatch):
    monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *a, **kw: None)
    service, _, _ = env
    first = DailyReservation.for_durable_run(service.store, service.cfg, "durable-one")
    reopened = Store(service.cfg.database)
    try:
        second = DailyReservation.for_durable_run(reopened, service.cfg, "durable-one")
        assert second.key == first.key and second.allowance == first.allowance
        assert (
            reopened.db.execute("SELECT COUNT(*) FROM documents WHERE kind='research_spend'").fetchone()[0]
            == 1
        )
        changed = service.cfg.model_copy(update={"research_max_cost_usd": dec("6")})
        with pytest.raises(DailyBudgetExceeded, match="reconciliation"):
            DailyReservation.for_durable_run(reopened, changed, "durable-one")
        second.finish(dec("1.2"))
        with pytest.raises(DailyBudgetExceeded, match="already settled"):
            DailyReservation.for_durable_run(reopened, service.cfg, "durable-one")
    finally:
        reopened.db.close()


def test_durable_reservation_cannot_adopt_legacy_or_unknown_spend(env, monkeypatch):
    monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *a, **kw: None)
    service, _, _ = env
    legacy = DailyReservation(service.store, service.cfg, "legacy")
    with pytest.raises(DailyBudgetExceeded, match="reconciliation"):
        DailyReservation.for_durable_run(service.store, service.cfg, "legacy")
    legacy.finish(dec("0.1"))
    durable = DailyReservation.for_durable_run(service.store, service.cfg, "durable")
    durable.finish(dec("0.2"), unknown=True)
    with pytest.raises(DailyBudgetExceeded, match="reconciliation"):
        DailyReservation.for_durable_run(service.store, service.cfg, "durable")


def test_durable_resume_rechecks_other_liabilities_and_original_day(env, monkeypatch):
    monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *a, **kw: None)
    service, _, _ = env
    monkeypatch.setattr("trader.spending.now", lambda: AT)
    DailyReservation.for_durable_run(service.store, service.cfg, "durable")
    service.store.put(
        "other",
        {"day": AT.date().isoformat(), "usd": "0.1", "status": "unknown", "unknown_total": True},
        "research_spend",
        scope="global",
    )
    with pytest.raises(DailyBudgetExceeded, match="reconciliation"):
        DailyReservation.for_durable_run(service.store, service.cfg, "durable")
    service.store.put(
        "other",
        {"day": AT.date().isoformat(), "usd": "0.1", "status": "settled", "unknown_total": False},
        "research_spend",
        scope="global",
    )
    assert DailyReservation.for_durable_run(service.store, service.cfg, "durable").allowance == 5
    monkeypatch.setattr("trader.spending.now", lambda: AT + timedelta(days=1))
    with pytest.raises(DailyBudgetExceeded, match="daily authority"):
        DailyReservation.for_durable_run(service.store, service.cfg, "durable")


def test_durable_funding_checked_inside_reservation_transaction(env, monkeypatch):
    service, _, _ = env

    def denied(store, cfg, current, **kwargs):
        assert store.db.in_transaction
        return "not funded"

    monkeypatch.setattr("trader.paper_dispatch.funding_gate", denied)
    with pytest.raises(DailyBudgetExceeded, match="not funded"):
        DailyReservation.for_durable_run(service.store, service.cfg, "denied")
    assert (
        service.store.db.execute("SELECT COUNT(*) FROM documents WHERE kind='research_spend'").fetchone()[0]
        == 0
    )


def test_daily_reservations_survive_restart_and_unknown_usage(env, monkeypatch):
    service, _, _ = env
    cfg = service.cfg
    cfg.research_daily_cost_usd = dec(6)
    pending = DailyReservation(service.store, cfg, "crashed")
    assert pending.allowance == 5
    reopened = Store(cfg.database)
    try:
        last = DailyReservation(reopened, cfg, "last-dollar")
        assert last.allowance == 1
        with pytest.raises(DailyBudgetExceeded):
            DailyReservation(reopened, cfg, "blocked")
        last.finish(dec(".2"))
        assert spending_status(reopened, dec(6))["remaining_usd"] == dec(".8")
        unknown = DailyReservation(reopened, cfg, "unknown-bill")
        unknown.finish(dec(".1"), unknown=True)
        assert spending_status(reopened, dec(6))["remaining_usd"] == 0
        reopened.experiment = {"id": "another-experiment"}
        with pytest.raises(DailyBudgetExceeded):
            DailyReservation(reopened, cfg, "reset-cannot-erase-cost")
        monkeypatch.setattr("trader.spending.now", lambda: AT + timedelta(days=1))
        with pytest.raises(DailyBudgetExceeded, match="Unknown model usage"):
            DailyReservation(reopened, cfg, "new-day")
        unknown.finish(dec(".1"))
        assert DailyReservation(reopened, cfg, "new-day-after-reconciliation").allowance == 5
    finally:
        reopened.db.close()


@pytest.mark.parametrize("after_testing", [False, True])
def test_daily_reservations_are_atomic_across_connections(env, after_testing):
    service, _, _ = env
    service.cfg.research_daily_cost_usd = dec(5)
    if after_testing:
        testing = DailyReservation(service.store, service.cfg, "settled-testing", testing=True)
        testing.finish(dec(8))
    barrier = Barrier(2)

    def reserve():
        store = Store(service.cfg.database)
        try:
            barrier.wait()
            try:
                return DailyReservation(store, service.cfg, "concurrent").allowance
            except DailyBudgetExceeded:
                return dec(0)
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == [0, 5]


def test_zero_budget_still_reconciles_without_research(env, decision):
    service, broker, _ = env
    cid = service.execute(decision, "entry")["client_order_id"]
    broker.fixture_fill(cid)
    service.cfg.research_daily_cost_usd = dec(0)
    result = Runner(service, None).tick()
    assert service.store.get("snapshot")["positions"][0]["qty"] == "10"
    assert all(item["status"] == "daily model budget exhausted" for item in result["outcomes"])


def test_health_checks_reconciliation_and_downtime(env, monkeypatch):
    service, _, _ = env
    with pytest.raises(ValueError, match="Heartbeat"):
        check_health(service.cfg)
    Runner(service, None).maintain()
    check_health(service.cfg)
    monkeypatch.setattr("trader.health.now", lambda: AT + timedelta(minutes=6))
    with pytest.raises(ValueError, match="stale"):
        check_health(service.cfg)
    monkeypatch.setattr("trader.health.now", lambda: AT)
    service.pause_reconciliation("fixture discrepancy")
    with pytest.raises(ValueError, match="reconciliation"):
        check_health(service.cfg)


def test_listener_maintains_account_while_research_waits(env, monkeypatch):
    service, _, _ = env

    async def scenario():
        researching, maintained = asyncio.Event(), asyncio.Event()

        class Socket:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def send(self, message):
                pass

            async def recv(self):
                return '[{"msg":"authenticated"}]'

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Event().wait()

        async def command(store, config_path, name, timeout, *args):
            if name == "tick":
                researching.set()
                await asyncio.Event().wait()
            if name == "maintain":
                await researching.wait()
                maintained.set()

        monkeypatch.setattr("trader.streams.credentials", lambda: ("fixture", "fixture"))
        monkeypatch.setattr("trader.streams.connect", lambda *args, **kwargs: Socket())
        monkeypatch.setattr("trader.streams.run_command", command)
        task = asyncio.create_task(listen(service.cfg, service.store, "fixture.toml"))
        try:
            await asyncio.wait_for(maintained.wait(), 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_process_timeout_terminates_child(env, monkeypatch):
    service, _, _ = env

    async def scenario():
        class Process:
            returncode = None

            def __init__(self):
                self.stopped = asyncio.Event()

            async def wait(self):
                await self.stopped.wait()
                return self.returncode

            def terminate(self):
                self.returncode = -15
                self.stopped.set()

        process = Process()

        async def create(*args, **kwargs):
            return process

        monkeypatch.setattr("trader.streams.asyncio.create_subprocess_exec", create)
        await run_command(service.store, "fixture.toml", "tick", 0.01)
        assert process.returncode == -15
        assert service.store.events("error")

    asyncio.run(scenario())


@pytest.mark.parametrize("finish_unknown", [False, True])
def test_uncapped_unknown_cost_survives_day_rollover_and_restart(env, monkeypatch, finish_unknown):
    service, _, _ = env
    reservation = DailyReservation(service.store, service.cfg, "uncapped", unrestricted_research=True)
    if finish_unknown:
        reservation.finish(dec("1.25"), unknown=True)
    monkeypatch.setattr("trader.spending.now", lambda: AT + timedelta(days=2))
    reopened = Store(service.cfg.database)
    try:
        reopened.experiment = {"id": "different-experiment"}
        status = spending_status(reopened, dec(6))
        assert status["charged_or_reserved_usd"] == 0
        assert status["unbounded_unknown_runs"] == 1
        with pytest.raises(DailyBudgetExceeded):
            DailyReservation(reopened, service.cfg, "cannot-hide-liability")
        # Simulate receiving final provider usage, not deleting the original reservation.
        reservation.finish(dec("1.50"), unknown=False)
        assert spending_status(reopened, dec(6))["unbounded_unknown_runs"] == 0
        assert DailyReservation(reopened, service.cfg, "after-settlement").allowance == 5
        assert reopened.get(reservation.key, "research_spend", scope="global")["usd"] == "1.50"
    finally:
        reopened.db.close()


def test_uncapped_liability_blocks_new_research_but_not_reconciliation(env, decision, monkeypatch):
    service, broker, _ = env
    cid = service.execute(decision, "entry")["client_order_id"]
    broker.fixture_fill(cid)
    monkeypatch.setattr("trader.spending.now", lambda: AT - timedelta(days=1))
    pending = DailyReservation(service.store, service.cfg, "unknown-old", unrestricted_research=True)
    pending.finish(dec(".1"), unknown=True)
    original = service.store.get(pending.key, "research_spend", scope="global")
    monkeypatch.setattr("trader.spending.now", lambda: AT)
    result = Runner(service, None).tick()
    assert service.store.get("snapshot")["positions"][0]["qty"] == "10"
    assert all(item["status"] == "unbounded model usage unresolved" for item in result["outcomes"])
    assert result["outcomes"]
    assert service.store.get(pending.key, "research_spend", scope="global") == original


def test_testing_override_exceeds_daily_budget_but_preserves_cost_and_crash_liability(env):
    service, _, _ = env
    cfg = service.cfg.model_copy(update={"research_daily_cost_usd": dec(6), "research_max_cost_usd": dec(20)})
    normal = DailyReservation(service.store, cfg, "normal")
    normal.finish(dec(6))
    testing = DailyReservation(service.store, cfg, "testing", testing=True)
    assert testing.allowance == 20
    assert testing.record["testing_budget_override"] is True
    with pytest.raises(DailyBudgetExceeded, match="outstanding"):
        DailyReservation(service.store, cfg, "duplicate-testing", testing=True)
    testing.finish(dec("7.50"))
    assert spending_status(service.store, dec(6))["charged_or_reserved_usd"] == dec("13.50")
    with pytest.raises(DailyBudgetExceeded, match="exhausted"):
        DailyReservation(service.store, cfg, "ordinary")
    assert DailyReservation(service.store, cfg, "next-testing", testing=True).allowance == 20


def test_testing_override_does_not_change_study_or_enable_unbounded_calls(env):
    service, _, _ = env
    with pytest.raises(DailyBudgetExceeded, match="bounded runs"):
        DailyReservation(
            service.store, service.cfg, "unbounded-test", testing=True, unrestricted_research=True
        )
    service.store.put("active", {"status": "operator_approved"}, "paper_study")
    with pytest.raises(DailyBudgetExceeded, match="outside an active study"):
        DailyReservation(service.store, service.cfg, "study-test", testing=True)


def test_settled_bounded_testing_has_separate_ordinary_admission_without_erasing_costs(env):
    service, _, _ = env
    cfg = service.cfg.model_copy(update={"research_daily_cost_usd": dec(6), "research_max_cost_usd": dec(20)})
    testing = DailyReservation(service.store, cfg, "testing-only", testing=True)
    testing.finish(dec("8.5"))
    original = service.store.get(testing.key, "research_spend", scope="global")
    status = spending_status(service.store, dec(6))
    assert status["charged_or_reserved_usd"] == status["settled_testing_usd"] == dec("8.5")
    assert status["remaining_usd"] == 0
    assert status["ordinary_charged_or_reserved_usd"] == 0 and status["ordinary_remaining_usd"] == 6
    normal = DailyReservation(service.store, cfg, "ordinary-after-test")
    assert normal.allowance == 6
    with pytest.raises(DailyBudgetExceeded, match="exhausted"):
        DailyReservation(service.store, cfg, "ordinary-duplicate")
    normal.finish(dec(6))
    assert spending_status(service.store, dec(6))["charged_or_reserved_usd"] == dec("14.5")
    assert spending_status(service.store, dec(6))["ordinary_remaining_usd"] == 0
    assert service.store.get(testing.key, "research_spend", scope="global") == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("testing_budget_override", 1),
        ("testing_budget_override", False),
        ("testing_budget_override", "true"),
        ("testing_budget_override", None),
        ("unrestricted_research", True),
        ("unrestricted_research", None),
        ("unrestricted_research", 0),
        ("unknown_total", None),
        ("unknown_total", 0),
        ("unknown_total", "false"),
        ("status", "unknown"),
    ],
)
def test_only_exact_known_bounded_testing_flags_are_exempt(env, field, value):
    service, _, _ = env
    service.cfg.research_daily_cost_usd = dec(6)
    record = {
        "day": AT.date().isoformat(),
        "usd": "7",
        "testing_budget_override": True,
        "unrestricted_research": False,
        "unknown_total": False,
        "status": "settled",
    }
    if value is None:
        record.pop(field)
    else:
        record[field] = value
    service.store.put("exact-flags", record, "research_spend", scope="global")
    status = spending_status(service.store, dec(6))
    assert status["settled_testing_usd"] == 0 and status["ordinary_charged_or_reserved_usd"] == 7
    assert service.store.get("exact-flags", "research_spend", scope="global") == record
    with pytest.raises(DailyBudgetExceeded):
        DailyReservation(service.store, service.cfg, "ordinary-must-not-ignore-malformed")


@pytest.mark.parametrize("state,unknown", [("reserved", False), ("unknown", True), ("settled", True)])
def test_previous_day_unresolved_testing_blocks_normal_and_testing_and_runner(env, state, unknown):
    from types import SimpleNamespace

    service, _, _ = env
    record = {
        "day": (AT - timedelta(days=1)).date().isoformat(),
        "usd": "20",
        "testing_budget_override": True,
        "unrestricted_research": False,
        "unknown_total": unknown,
        "status": state,
    }
    service.store.put("old-test", record, "research_spend", scope="global")
    assert spending_status(service.store, dec(6))["unresolved_testing_runs"] == 1
    for testing in (False, True):
        with pytest.raises(DailyBudgetExceeded, match="outstanding|unresolved"):
            DailyReservation(service.store, service.cfg, "blocked", testing=testing)
    result = Runner(
        service, SimpleNamespace(invoke=lambda payload: pytest.fail("Unresolved testing must block graph"))
    ).tick()
    expected = (
        "explicit testing usage unresolved" if state == "reserved" else "unknown model usage unresolved"
    )
    assert result["outcomes"] and all(item["status"] == expected for item in result["outcomes"])
    assert service.store.get("old-test", "research_spend", scope="global") == record


def test_runner_ordinary_preread_uses_admission_not_gross_testing_costs(env):
    from types import SimpleNamespace

    service, _, _ = env
    service.cfg.research_daily_cost_usd = dec(6)
    testing = DailyReservation(service.store, service.cfg, "testing", testing=True)
    testing.finish(dec(8))
    calls = []

    def invoke(payload):
        calls.append(payload)
        return {"decision": {"action": "WATCH", "confidence": "0.5"}, "execution": {"status": "watch"}}

    result = Runner(service, SimpleNamespace(invoke=invoke)).tick()
    assert calls and any(item.get("action") == "WATCH" for item in result["outcomes"])
    assert spending_status(service.store, dec(6))["charged_or_reserved_usd"] == 8


@pytest.mark.parametrize("status,unknown", [("unknown", False), (None, False), ("settled", True)])
def test_any_day_untagged_unknown_charge_blocks_both_admission_lanes(env, status, unknown):
    service, _, _ = env
    record = {"day": (AT - timedelta(days=2)).date().isoformat(), "usd": "1", "unknown_total": unknown}
    if status is not None:
        record["status"] = status
    service.store.put("legacy-unknown", record, "research_spend", scope="global")
    summary = spending_status(service.store, dec(6))
    assert summary["charged_or_reserved_usd"] == 0 and summary["unknown_usage_runs"] == 1
    for testing in (False, True):
        with pytest.raises(DailyBudgetExceeded, match="outstanding|Unknown model usage"):
            DailyReservation(service.store, service.cfg, "must-not-admit", testing=testing)
    assert service.store.get("legacy-unknown", "research_spend", scope="global") == record
