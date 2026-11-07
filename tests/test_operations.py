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
        assert DailyReservation(reopened, cfg, "new-day").allowance == 5
    finally:
        reopened.db.close()


def test_daily_reservations_are_atomic_across_connections(env):
    service, _, _ = env
    service.cfg.research_daily_cost_usd = dec(5)
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
