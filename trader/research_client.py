"""Bounded, receipt-verified handoff from the trading graph to Temporal research."""

import asyncio
import json
import re
from datetime import timedelta

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from .models import dec, dt, dumps, now
from .paper_dispatch import fingerprint
from .research_journal import Journal, JournalBusy
from .research_manifest import load_manifest, prepare_manifest, run_directory
from .research_runtime import control_snapshot
from .research_workflow import ResearchWorkflow, start_research_workflow, valid_reference


class ResearchPending(Exception):
    """The original durable run is active or blocked; no new run is authorized."""

    def __init__(self, reference, state="starting", stage=None):
        self.reference = reference
        self.state = state
        self.stage = stage
        super().__init__(f"Research {reference['run_key']} is {state}")


class TemporalTransport:
    def __init__(self, address, namespace, task_queue, *, tls=False):
        self.address, self.namespace, self.task_queue, self.tls = (
            address,
            namespace,
            task_queue,
            tls,
        )

    def status(self, reference, *, start):
        async def check():
            client = await Client.connect(self.address, namespace=self.namespace, tls=self.tls)
            if start:
                try:
                    await start_research_workflow(
                        client, reference["run_key"], reference["manifest_hash"], self.task_queue
                    )
                except WorkflowAlreadyStartedError:
                    pass
            handle = client.get_workflow_handle(f"research:{reference['run_key']}")
            try:
                description = await handle.describe()
            except RPCError as exc:
                if exc.status == RPCStatusCode.NOT_FOUND:
                    return {**reference, "state": "history_unavailable", "stage": None}
                raise
            if description.status in {
                WorkflowExecutionStatus.FAILED,
                WorkflowExecutionStatus.CANCELED,
                WorkflowExecutionStatus.TERMINATED,
                WorkflowExecutionStatus.TIMED_OUT,
            }:
                return {
                    **reference,
                    "state": "terminal_review_required",
                    "terminal_status": description.status.name.lower(),
                    "stage": None,
                }
            return await handle.query(ResearchWorkflow.status)

        # A tick only starts or queries the workflow. It never waits for research to finish.
        return asyncio.run(asyncio.wait_for(check(), timeout=10))


class TemporalResearchClient:
    def __init__(
        self,
        cfg,
        store,
        *,
        root,
        address,
        namespace="default",
        task_queue="trader-research",
        tls=False,
        transport=None,
    ):
        self.cfg, self.store, self.root = cfg, store, root
        self.transport = transport or TemporalTransport(address, namespace, task_queue, tls=tls)

    def _identity(self, state):
        study = self.store.get("active", "paper_study")
        return {
            "experiment_id": self.store.scope,
            "run_key": state["run_key"],
            "symbol": state["symbol"],
            "study_id": state.get("study_id"),
            "study_stratum": state.get("study_stratum"),
            "schedule_sha256": study.get("schedule_sha256") if study else None,
        }

    def reference_for(self, state):
        frozen = self.store.get(state["run_key"], "research_request")
        if frozen is None:
            return None
        if frozen.get("identity") != self._identity(state):
            raise ValueError("Research run key belongs to different study, slot or symbol")
        context = frozen["context"]
        reference = prepare_manifest(self.root, self.cfg, self.store.scope, context)
        existing = self.store.get(state["run_key"], "research_workflow")
        if existing is not None and existing != reference:
            raise ValueError("Research workflow reference changed")
        if existing is None:
            self.store.put(state["run_key"], reference, "research_workflow")
        return reference

    def same_invocation(self, state, claim):
        if not isinstance(claim, dict) or claim.get("status") != "claimed":
            return False
        if claim.get("symbol") != state.get("symbol"):
            return False
        if claim.get("schedule_sha256") != self._identity(state)["schedule_sha256"]:
            return False
        if self.reference_for(state) is not None:
            return True
        if any(
            self.store.get(state["run_key"], kind) is not None
            for kind in ("research_result", "research_audit", "graph_decision")
        ):
            return False
        if self.store.db.execute(
            "SELECT 1 FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
            (state["run_key"], self.store.scope),
        ).fetchone():
            return False
        if (run_directory(self.root, state["run_key"]) / "operations.sqlite").exists():
            return False
        if state.get("study_stratum") == "scheduled":
            pending = self.store.get(state["run_key"], "paper_slot_pending")
            return bool(pending and pending.get("status") == "starting" and pending.get("state") == state)
        if state.get("study_stratum") == "safety":
            job = self.store.get(state["run_key"], "job")
            return bool(job and job.get("status") == "running" and job.get("state") == state)
        return False

    def start(self, context):
        identity = self._identity(context)
        existing = self.store.get(context["run_key"], "research_request")
        if existing is None:
            frozen_context = json.loads(dumps({**context, "research_only": True}))
            frozen_context["runtime_control"] = control_snapshot(self.cfg, self.store, frozen_context)
            frozen = {"identity": identity, "context": frozen_context}
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                    (self.store.scope, "research_request", context["run_key"], dumps(frozen)),
                )
        elif existing["identity"] != identity:
            raise ValueError("Research run key belongs to different frozen request")
        reference = self.reference_for(context)
        return self.poll(reference)

    def poll(self, reference):
        if not valid_reference(reference):
            raise ValueError("Invalid research reference")
        manifest = load_manifest(self.root, reference, self.cfg, self.store.scope)
        if self.reference_for(manifest["context"]) != reference:
            raise ValueError("Research reference has no matching frozen request")
        receipt_key = f"research_stage_receipt:{reference['run_key']}:commit"
        if self.store.get(receipt_key, "research_stage_receipt") is not None:
            return self._verified_result(reference, manifest)
        submitted = self.store.get(reference["run_key"], "research_workflow_submission")
        first = submitted is None
        if first:
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                    (
                        self.store.scope,
                        "research_workflow_submission",
                        reference["run_key"],
                        dumps(reference),
                    ),
                )
        elif submitted != reference:
            raise ValueError("Research workflow submission identity changed")
        else:
            approvals = [
                item
                for item in self.store.items("research_workflow_resubmission")
                if item.get("reference") == reference
                and self.store.get(item["key"], "research_workflow_resubmission_consumed") is None
            ]
            if approvals:
                if len(approvals) != 1:
                    raise ValueError("Multiple workflow resubmission approvals")
                approval = approvals[0]
                with self.store.db:
                    self.store.db.execute(
                        "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                        (
                            self.store.scope,
                            "research_workflow_resubmission_consumed",
                            approval["key"],
                            dumps({"reference": reference, "consumed_at": now()}),
                        ),
                    )
                first = True
        try:
            status = self.transport.status(reference, start=first)
        except Exception as exc:  # dispatch outcome may be unknown
            raise ResearchPending(reference, "transport_unavailable") from exc
        if (
            not isinstance(status, dict)
            or status.get("run_key") != reference["run_key"]
            or status.get("manifest_hash") != reference["manifest_hash"]
        ):
            raise ValueError("Workflow status has different research identity")
        if status.get("state") != "complete":
            if status.get("state") == "terminal_review_required":
                self.store.put(
                    reference["run_key"],
                    {"reference": reference, "status": status.get("terminal_status")},
                    "research_workflow_terminal",
                )
            raise ResearchPending(reference, status.get("state", "starting"), status.get("stage"))
        return self._verified_result(reference, manifest)

    def authorize_resubmit(self, reference, proof):
        """Record one operator-verified same-ID retry after definitive non-dispatch."""
        if not valid_reference(reference) or not isinstance(proof, dict):
            raise ValueError("Valid research reference and proof required")
        required = {
            "verified_by",
            "reason",
            "observed_at",
            "evidence_sha256",
            "namespace",
            "workflow_id",
            "manifest_hash",
        }
        if (
            set(proof) != required
            or not proof["verified_by"]
            or not proof["reason"]
            or proof["namespace"] != self.transport.namespace
            or proof["workflow_id"] != f"research:{reference['run_key']}"
            or proof["manifest_hash"] != reference["manifest_hash"]
            or not isinstance(proof["evidence_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", proof["evidence_sha256"]) is None
            or not timedelta(0) <= now() - dt(proof["observed_at"]) <= timedelta(minutes=5)
        ):
            raise ValueError("Workflow non-dispatch proof is incomplete or stale")
        if self.store.get(reference["run_key"], "research_workflow") != reference:
            raise ValueError("Resubmission has no matching frozen workflow")
        if self.store.get(reference["run_key"], "research_workflow_submission") != reference:
            raise ValueError("Original submission marker is missing")
        if (run_directory(self.root, reference["run_key"]) / "operations.sqlite").exists():
            raise ValueError("Research journal exists; workflow may have dispatched")
        spent = self.store.db.execute(
            "SELECT 1 FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
            (reference["run_key"], self.store.scope),
        ).fetchone()
        if spent:
            raise ValueError("Research reservation exists; workflow may have dispatched")
        status = self.transport.status(reference, start=False)
        if (
            not isinstance(status, dict)
            or status.get("run_key") != reference["run_key"]
            or status.get("manifest_hash") != reference["manifest_hash"]
            or status.get("state") != "history_unavailable"
        ):
            raise ValueError("Temporal history has not been proved absent")
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            if (run_directory(self.root, reference["run_key"]) / "operations.sqlite").exists():
                raise ValueError("Research journal appeared during approval")
            if self.store.db.execute(
                "SELECT 1 FROM documents WHERE scope='global' AND kind='research_spend' "
                "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
                (reference["run_key"], self.store.scope),
            ).fetchone():
                raise ValueError("Research reservation appeared during approval")
            existing = self.store.items("research_workflow_resubmission")
            if any(
                x.get("reference") == reference
                and self.store.get(x["key"], "research_workflow_resubmission_consumed") is None
                for x in existing
            ):
                raise ValueError("Resubmission approval is already unused")
            attempt = 1 + sum(x.get("reference") == reference for x in existing)
            key = f"{reference['run_key']}:{attempt}"
            receipt = {
                "key": key,
                "reference": reference,
                "proof": proof,
                "mechanical_evidence": {"journal_absent": True, "reservation_absent": True},
            }
            self.store.db.execute(
                "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                (self.store.scope, "research_workflow_resubmission", key, dumps(receipt)),
            )
        return receipt

    def _verified_result(self, reference, manifest):
        key = reference["run_key"]
        receipt = self.store.get(f"research_stage_receipt:{key}:commit", "research_stage_receipt")
        if (
            not receipt
            or receipt.get("run_key") != key
            or receipt.get("manifest_hash") != reference["manifest_hash"]
            or receipt.get("stage") != "commit"
            or receipt.get("receipt", {}).get("status") != "complete"
        ):
            raise ResearchPending(reference, "commit_receipt_pending", "commit")
        result = self.store.get(key, "research_result")
        audit = self.store.get(key, "research_audit")
        if not result or not audit or audit.get("symbol") != manifest["context"]["symbol"]:
            raise ValueError("Committed research result or audit is missing")
        records = [
            json.loads(row[0])
            for row in self.store.db.execute(
                "SELECT body FROM documents WHERE scope='global' AND kind='research_spend' "
                "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
                (key, self.store.scope),
            )
        ]
        if len(records) != 1:
            raise ValueError("Committed research has no unique spending record")
        spend = records[0]
        if (
            spend.get("status") != "settled"
            or spend.get("unknown_total")
            or not spend.get("durable")
            or spend.get("outcome") == "aborted_accounted"
            or spend.get("config_sha256") != manifest["binding"]["config_sha256"]
            or dec(spend["usd"]) != dec(audit["estimated_usd"])
        ):
            raise ValueError("Committed research spending is not settled and audited")
        journal = Journal(run_directory(self.root, key) / "operations.sqlite", manifest["binding"])
        try:
            with journal.lease():
                saved = journal.load_state()
        except JournalBusy as exc:
            raise ResearchPending(reference, "commit_verification_pending", "commit") from exc
        hashes = saved.get("desk", {}).get("continuation", {}).get("commit_hashes") if saved else None
        expected = {
            "result": fingerprint(result),
            "audit": fingerprint(audit),
        }
        if hashes != expected:
            raise ValueError("Committed research hashes differ from durable journal")
        return result
