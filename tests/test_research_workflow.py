import asyncio
from threading import Event

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from trader.research_workflow import (
    ResearchActivities,
    ResearchWorkflow,
    StageAmbiguous,
    cancellation_event,
    cancellation_requested,
    start_research_workflow,
    valid_receipt,
    valid_reference,
    valid_transition,
)

MANIFEST = "a" * 64


async def wait_for(handle, stage, state):
    for _ in range(100):
        status = await handle.query(ResearchWorkflow.status)
        if status["stage"] == stage and status["state"] == state:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow did not reach {stage}/{state}")


def test_receipt_and_reference_boundaries():
    assert valid_reference({"run_key": "paper:PG:one", "manifest_hash": MANIFEST})
    assert not valid_reference(
        {"run_key": "paper:PG:one", "manifest_hash": MANIFEST, "payload": "full filing"}
    )
    assert not valid_reference({"run_key": "x" * 129, "manifest_hash": MANIFEST})
    assert valid_receipt({"status": "next", "receipt_ref": "journal:1", "next_stage": "correction:valuation"})
    assert not valid_receipt({"status": "complete", "receipt_ref": "journal:1", "full_report": {}})
    assert not valid_receipt({"status": "next", "receipt_ref": "journal:1", "next_stage": "unknown"})
    assert not valid_transition(
        "acquisition", {"status": "next", "receipt_ref": "journal:1", "next_stage": "commit"}
    )
    assert valid_transition(
        "lead", {"status": "next", "receipt_ref": "journal:1", "next_stage": "correction:valuation"}
    )


def test_restarts_same_workflow_and_replays_history():
    async def exercise():
        calls = []
        allow_business = False

        def stage_handler(request):
            nonlocal allow_business
            stage = request["stage"]
            calls.append(stage)
            if stage == "business" and not allow_business:
                return {"status": "blocked", "receipt_ref": "journal:business-blocked"}
            return {"status": "complete", "receipt_ref": f"journal:{stage}"}

        async with await WorkflowEnvironment.start_local() as env:
            client = env.client
            activities = ResearchActivities(stage_handler)
            async with Worker(
                client,
                task_queue="research-test",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                handle = await start_research_workflow(client, "paper:PG:one", MANIFEST, "research-test")
                blocked = await wait_for(handle, "business", "blocked")
                assert blocked["completed_count"] == 1
                with pytest.raises(WorkflowAlreadyStartedError):
                    await start_research_workflow(client, "paper:PG:one", MANIFEST, "research-test")
            allow_business = True
            async with Worker(
                client,
                task_queue="research-test",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                assert (await handle.query(ResearchWorkflow.status))["stage"] == "business"
                await handle.signal(ResearchWorkflow.resume)
                finished = await handle.result()
                assert finished["state"] == "complete" and finished["completed_count"] == 7
                assert calls.count("acquisition") == 1 and calls.count("business") == 2
                with pytest.raises(WorkflowAlreadyStartedError):
                    await start_research_workflow(client, "paper:PG:one", MANIFEST, "research-test")
                history = await handle.fetch_history()
            await Replayer(workflows=[ResearchWorkflow]).replay_workflow(history)

    asyncio.run(exercise())


def test_ambiguous_activity_waits_for_manual_resume():
    async def exercise():
        calls = []

        def stage_handler(request):
            calls.append(request["stage"])
            if len(calls) == 1:
                raise StageAmbiguous("unknown external outcome")
            return {"status": "complete", "receipt_ref": f"journal:{request['stage']}"}

        async with await WorkflowEnvironment.start_local() as env:
            activities = ResearchActivities(stage_handler)
            async with Worker(
                env.client,
                task_queue="research-ambiguous",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                handle = await start_research_workflow(
                    env.client, "paper:PG:ambiguous", MANIFEST, "research-ambiguous"
                )
                assert (await wait_for(handle, "acquisition", "blocked"))["completed_count"] == 0
                assert calls == ["acquisition"]
                await handle.signal(ResearchWorkflow.resume)
                assert (await handle.result())["state"] == "complete"
                assert calls.count("acquisition") == 2

    asyncio.run(exercise())


def test_correction_branch_and_bounded_transient_retries():
    async def exercise():
        calls = []
        fail_acquisition = True

        def stage_handler(request):
            nonlocal fail_acquisition
            stage = request["stage"]
            calls.append(stage)
            if stage == "acquisition" and fail_acquisition:
                raise RuntimeError("temporary source outage")
            if stage == "lead":
                return {
                    "status": "next",
                    "receipt_ref": "journal:lead",
                    "next_stage": "correction:valuation",
                }
            if stage == "correction:valuation":
                return {
                    "status": "next",
                    "receipt_ref": "journal:correction-valuation",
                    "next_stage": "correction:adversarial",
                }
            if stage == "correction:adversarial":
                return {
                    "status": "next",
                    "receipt_ref": "journal:correction-adversarial",
                    "next_stage": "correction:lead",
                }
            if stage == "correction:lead":
                return {"status": "next", "receipt_ref": "journal:correction-lead", "next_stage": "commit"}
            return {"status": "complete", "receipt_ref": f"journal:{stage}"}

        async with await WorkflowEnvironment.start_local() as env:
            activities = ResearchActivities(stage_handler)
            async with Worker(
                env.client,
                task_queue="research-retry",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                handle = await start_research_workflow(
                    env.client, "paper:PG:retry", MANIFEST, "research-retry"
                )
                assert (await wait_for(handle, "acquisition", "blocked"))["completed_count"] == 0
                assert calls == ["acquisition"] * 3
                fail_acquisition = False
                await handle.signal(ResearchWorkflow.resume)
                finished = await handle.result()
                assert finished["state"] == "complete" and finished["completed_count"] == 10
                assert calls.count("acquisition") == 4 and calls.count("correction:valuation") == 1

    asyncio.run(exercise())


def test_cancel_waits_for_handler_thread_before_releasing_lease():
    async def exercise():
        entered = Event()
        finish_current_operation = Event()
        lease_active = Event()
        operations = []

        def stage_handler(request):
            lease_active.set()
            stop = cancellation_event()
            assert stop is not None
            try:
                operations.append("current_started")
                entered.set()
                finish_current_operation.wait()
                operations.append("current_finished")
                if cancellation_requested():
                    return {"status": "blocked", "receipt_ref": "journal:cancelled"}
                operations.append("next_operation")
                return {"status": "complete", "receipt_ref": "journal:done"}
            finally:
                lease_active.clear()

        activity = ResearchActivities(stage_handler)
        task = asyncio.create_task(
            activity.research_stage(
                {"run_key": "paper:PG:cancel", "manifest_hash": MANIFEST, "stage": "acquisition"}
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.05)
        assert lease_active.is_set() and not task.done()
        finish_current_operation.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert operations == ["current_started", "current_finished"]
        assert not lease_active.is_set()

    asyncio.run(exercise())


def test_server_cancel_waits_for_inflight_handler():
    async def exercise():
        entered = Event()
        finish_current_operation = Event()
        lease_active = Event()
        stop_events = []
        operations = []

        def stage_handler(request):
            stop = cancellation_event()
            assert stop is not None
            stop_events.append(stop)
            lease_active.set()
            try:
                operations.append("current_started")
                entered.set()
                finish_current_operation.wait()
                operations.append("current_finished")
                if cancellation_requested():
                    return {"status": "blocked", "receipt_ref": "journal:cancelled"}
                operations.append("next_operation")
                return {"status": "complete", "receipt_ref": "journal:done"}
            finally:
                lease_active.clear()

        async with await WorkflowEnvironment.start_local() as env:
            activities = ResearchActivities(stage_handler)
            async with Worker(
                env.client,
                task_queue="research-server-cancel",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                handle = await start_research_workflow(
                    env.client, "paper:PG:server-cancel", MANIFEST, "research-server-cancel"
                )
                assert await asyncio.to_thread(entered.wait, 2)
                try:
                    await handle.cancel()
                    for _ in range(600):
                        if stop_events[0].is_set():
                            break
                        await asyncio.sleep(0.05)
                    assert stop_events[0].is_set()
                    assert lease_active.is_set() and operations == ["current_started"]
                finally:
                    finish_current_operation.set()
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(handle.result(), 15)
                assert operations == ["current_started", "current_finished"]
                assert not lease_active.is_set()

    asyncio.run(exercise())
