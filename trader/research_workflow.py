"""Small Temporal coordinator for receipt-backed research stages."""

import asyncio
import re
from collections.abc import Callable
from contextvars import ContextVar
from datetime import timedelta
from threading import Event

from temporalio import activity, workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError

STAGES = ("acquisition", "business", "catalysts", "valuation", "adversarial", "lead", "commit")
CORRECTION_ROLES = {"business", "catalysts", "valuation", "adversarial", "lead"}
RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[
        "StageAmbiguous",
        "StageConfigurationError",
        "StageBudgetError",
        "InvalidReceipt",
    ],
)
_cancellation_event: ContextVar[Event | None] = ContextVar("research_stage_cancellation", default=None)


def cancellation_event() -> Event | None:
    """Return the current activity's cooperative cancellation flag in its handler thread."""
    return _cancellation_event.get()


def cancellation_requested() -> bool:
    event = cancellation_event()
    return event is not None and event.is_set()


class StageAmbiguous(Exception):
    """An external operation may have happened; retry requires reconciliation."""


class StageConfigurationError(Exception):
    """The stage cannot run with the supplied configuration."""


class StageBudgetError(Exception):
    """The stage has no safe spending allowance."""


def valid_stage(stage: object) -> bool:
    return isinstance(stage, str) and (
        stage in STAGES
        or stage.startswith("correction:")
        and stage.removeprefix("correction:") in CORRECTION_ROLES
    )


def valid_reference(reference: object) -> bool:
    return (
        isinstance(reference, dict)
        and set(reference) == {"run_key", "manifest_hash"}
        and isinstance(reference["run_key"], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,127}", reference["run_key"]) is not None
        and isinstance(reference["manifest_hash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", reference["manifest_hash"]) is not None
    )


def valid_receipt(receipt: object) -> bool:
    if not isinstance(receipt, dict):
        return False
    status = receipt.get("status")
    if status not in {"complete", "next", "blocked"}:
        return False
    ref = receipt.get("receipt_ref")
    if not isinstance(ref, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}", ref) is None:
        return False
    if status == "next":
        return set(receipt) == {"status", "receipt_ref", "next_stage"} and valid_stage(receipt["next_stage"])
    return set(receipt) == {"status", "receipt_ref"}


def valid_transition(stage: str, receipt: dict[str, str]) -> bool:
    if receipt["status"] != "next":
        return receipt["status"] == "blocked" or stage in STAGES
    next_stage = receipt["next_stage"]
    if stage in STAGES[:-1]:
        allowed = {STAGES[STAGES.index(stage) + 1]}
        if stage == "lead":
            allowed.update({"correction:business", "correction:catalysts", "correction:valuation"})
    else:
        allowed = {
            "correction:business": {"correction:catalysts", "correction:valuation"},
            "correction:catalysts": {"correction:valuation"},
            "correction:valuation": {"correction:adversarial"},
            "correction:adversarial": {"correction:lead"},
            "correction:lead": {"commit"},
        }.get(stage, set())
    return next_stage in allowed


class ResearchActivities:
    def __init__(self, stage_handler: Callable[[dict[str, str]], dict[str, str]]):
        self.stage_handler = stage_handler

    @activity.defn(name="research_stage")
    async def research_stage(self, request: dict[str, str]) -> dict[str, str]:
        if (
            not isinstance(request, dict)
            or set(request) != {"run_key", "manifest_hash", "stage"}
            or not valid_reference({k: request[k] for k in ("run_key", "manifest_hash")})
            or not valid_stage(request["stage"])
        ):
            raise ApplicationError(
                "invalid stage request", type="StageConfigurationError", non_retryable=True
            )
        stop = Event()
        token = _cancellation_event.set(stop)
        handler_task = asyncio.create_task(asyncio.to_thread(self.stage_handler, request))
        try:
            try:
                while True:
                    if activity.in_activity():
                        activity.heartbeat()
                    try:
                        receipt = await asyncio.wait_for(asyncio.shield(handler_task), timeout=1)
                        break
                    except TimeoutError:
                        continue
            except asyncio.CancelledError:
                stop.set()
                while not handler_task.done():
                    try:
                        await asyncio.wait({handler_task})
                    except asyncio.CancelledError:
                        continue
                if not handler_task.cancelled():
                    handler_task.exception()
                raise
        except (StageAmbiguous, StageConfigurationError, StageBudgetError) as exc:
            raise ApplicationError(
                "stage needs manual resolution", type=type(exc).__name__, non_retryable=True
            ) from exc
        except Exception as exc:
            raise ApplicationError("stage failed", type="StageFailure") from exc
        finally:
            _cancellation_event.reset(token)
        if not valid_receipt(receipt) or not valid_transition(request["stage"], receipt):
            raise ApplicationError("invalid stage receipt", type="InvalidReceipt", non_retryable=True)
        return receipt


@workflow.defn
class ResearchWorkflow:
    def __init__(self):
        self.run_key = ""
        self.manifest_hash = ""
        self.stage = STAGES[0]
        self.state = "starting"
        self.receipt_ref = ""
        self.completed_count = 0
        self._resume = False

    @workflow.run
    async def run(self, reference: dict[str, str]) -> dict[str, str | int]:
        if not valid_reference(reference):
            raise ApplicationError(
                "invalid research reference", type="StageConfigurationError", non_retryable=True
            )
        self.run_key, self.manifest_hash = reference["run_key"], reference["manifest_hash"]
        while True:
            self.state = "running"
            try:
                receipt = await workflow.execute_activity(
                    "research_stage",
                    {**reference, "stage": self.stage},
                    start_to_close_timeout=timedelta(minutes=15),
                    heartbeat_timeout=timedelta(seconds=10),
                    retry_policy=RETRY_POLICY,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
            except ActivityError:
                if workflow.cancellation_reason() is not None:
                    raise asyncio.CancelledError()
                self.state = "blocked"
            else:
                if not valid_receipt(receipt) or not valid_transition(self.stage, receipt):
                    self.state = "blocked"
                else:
                    self.receipt_ref = receipt["receipt_ref"]
                    if receipt["status"] == "blocked":
                        self.state = "blocked"
                    else:
                        self.completed_count += 1
                        if self.stage == "commit" and receipt["status"] == "complete":
                            self.state = "complete"
                            return self.status()
                        if receipt["status"] == "next":
                            self.stage = receipt["next_stage"]
                        elif self.stage in STAGES[:-1]:
                            self.stage = STAGES[STAGES.index(self.stage) + 1]
                        else:
                            self.state = "blocked"
            if self.state == "blocked":
                await workflow.wait_condition(lambda: self._resume)
                self._resume = False

    @workflow.signal
    def resume(self) -> None:
        if self.state == "blocked":
            self._resume = True

    @workflow.query
    def status(self) -> dict[str, str | int]:
        return {
            "run_key": self.run_key,
            "manifest_hash": self.manifest_hash,
            "stage": self.stage,
            "state": self.state,
            "receipt_ref": self.receipt_ref,
            "completed_count": self.completed_count,
        }


async def start_research_workflow(client, run_key: str, manifest_hash: str, task_queue: str):
    reference = {"run_key": run_key, "manifest_hash": manifest_hash}
    if not valid_reference(reference):
        raise ValueError("invalid research reference")
    return await client.start_workflow(
        ResearchWorkflow.run,
        reference,
        id=f"research:{run_key}",
        task_queue=task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )
