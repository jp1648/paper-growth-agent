"""Receipt-backed, one-stage-at-a-time execution of a bounded research run."""

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

from langchain_core.messages import HumanMessage

from .evidence import EvidenceProvider
from .models import Decision, dec, dumps, now
from .paper_dispatch import fingerprint, funding_gate
from .research_checkpoint import PausedOperation, ResearchCancelled, ResearchCheckpoint
from .research_journal import AmbiguousOperation, BindingMismatch, Journal, JournalError
from .research_team import ROLES, Budget, ResearchDesk, ResearchLimit, ResearchStageYield
from .research_workflow import (
    StageAmbiguous,
    StageBudgetError,
    StageConfigurationError,
    cancellation_event,
    valid_receipt,
    valid_stage,
    valid_transition,
)
from .spending import DailyBudgetExceeded, DailyReservation
from .store import Store
from .x_api import XDiscovery

ROLE_ORDER = tuple(ROLES)
ASSIGNMENT = "Complete your assigned research using original evidence and the available prior reports."
RECEIPT_KIND = "research_stage_receipt"


def cached_stage_receipt(store, reference, stage):
    """Read a completed stage without constructing a provider or model."""
    record = store.get(f"research_stage_receipt:{reference['run_key']}:{stage}", RECEIPT_KIND)
    if record is None:
        return None
    receipt = record.get("receipt")
    if (
        record.get("run_key") != reference["run_key"]
        or record.get("manifest_hash") != reference["manifest_hash"]
        or record.get("stage") != stage
        or not valid_receipt(receipt)
        or not valid_transition(stage, receipt)
    ):
        raise StageConfigurationError("Saved stage receipt differs from frozen run")
    return receipt


def _hash(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def _check_cancelled(stop):
    if stop is not None and stop.is_set():
        raise ResearchCancelled("Research activity cancelled before dispatch")


class ResearchStages:
    """Execute one durable stage per call, with no broker or thesis mutation.

    ``store_path`` is opened in the activity handler thread. ``binding`` must
    contain the frozen run, experiment, config, code and context hashes. The caller
    supplies models and can inject fake acquisition/desk factories for tests.
    """

    def __init__(
        self,
        cfg,
        store_path,
        provider_factory,
        context,
        models,
        private_dir,
        binding,
        *,
        x_factory=XDiscovery,
        desk_factory=ResearchDesk,
        store_factory=Store,
        before_dispatch=None,
    ):
        required = {"run_key", "experiment_id", "config_sha256", "code_sha256", "context_sha256"}
        if set(binding) != required:
            raise ValueError(
                "Research binding needs exact run, experiment, config, code and context identity"
            )
        if context.get("run_key") != binding["run_key"] or context.get("symbol") is None:
            raise ValueError("Research context does not match run binding")
        if fingerprint(cfg) != binding["config_sha256"] or fingerprint(context) != binding["context_sha256"]:
            raise ValueError("Research config or context differs from frozen binding")
        if context.get("trigger") == "new news evidence":
            context = {**context, "trigger": "fresh full research"}
        self.cfg, self.store_path, self.provider_factory = cfg, store_path, provider_factory
        self.context, self.models = copy.deepcopy(context), models
        self.private_dir, self.binding = Path(private_dir), copy.deepcopy(binding)
        self.manifest_hash = fingerprint(binding)
        self.x_factory, self.desk_factory, self.store_factory = x_factory, desk_factory, store_factory
        self.before_dispatch = before_dispatch

    def __call__(self, request):
        if (
            not isinstance(request, dict)
            or set(request) != {"run_key", "manifest_hash", "stage"}
            or request["run_key"] != self.binding["run_key"]
            or request["manifest_hash"] != self.manifest_hash
            or not valid_stage(request["stage"])
        ):
            raise StageConfigurationError("Stage request differs from frozen research identity")
        stage = request["stage"]
        stop = cancellation_event()
        store = self.store_factory(self.store_path)
        provider = None
        try:
            if store.scope != self.binding["experiment_id"]:
                raise StageConfigurationError("Research experiment changed")
            journal = Journal(self.private_dir / "operations.sqlite", self.binding)
            with journal.lease():
                provider = self.provider_factory(store)
                old = self._receipt(store, stage)
                if old is not None:
                    return old
                if store.db.execute(
                    "SELECT 1 FROM documents WHERE scope='global' AND kind='research_spend' "
                    "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=? "
                    "AND json_extract(body,'$.outcome')='aborted_accounted' LIMIT 1",
                    (self.context["run_key"], store.scope),
                ).fetchone():
                    raise StageConfigurationError("Accounting-closed research cannot resume")
                self._require_predecessor(store, stage)
                if stage == "commit" and self._settled_commit_exists(store, journal):
                    return self._save_receipt(store, stage, {"status": "complete"})
                prior_reservation = store.db.execute(
                    "SELECT 1 FROM documents WHERE scope='global' AND kind='research_spend' "
                    "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=? LIMIT 1",
                    (self.context["run_key"], store.scope),
                ).fetchone()
                if prior_reservation is None:
                    reason = funding_gate(store, self.cfg, now())
                    if reason:
                        raise StageBudgetError(reason)
                reservation = DailyReservation.for_durable_run(store, self.cfg, self.context["run_key"])
                reason = funding_gate(store, self.cfg, now(), reservation_key=reservation.key)
                if reason:
                    raise StageBudgetError(reason)
                if stage == "acquisition":
                    receipt = self._acquisition(store, journal, reservation, provider, stop)
                else:
                    saved = journal.load_state()
                    if not isinstance(saved, dict) or saved.get("version") != 1:
                        raise StageConfigurationError("Acquisition checkpoint is missing")
                    desk, opened = self._restore_desk(journal, saved, reservation, stage, provider, stop)
                    try:
                        receipt = self._run_stage(stage, desk, journal, store, reservation)
                    finally:
                        for checkpoint in opened:
                            checkpoint.close()
                if not valid_receipt(receipt) or not valid_transition(stage, receipt):
                    raise StageConfigurationError("Stage produced an invalid transition")
                return self._save_receipt(store, stage, receipt)
        except (PausedOperation, AmbiguousOperation) as exc:
            raise StageAmbiguous("An external operation needs reconciliation") from exc
        except ResearchCancelled as exc:
            raise StageAmbiguous("Research stage was stopped before another operation") from exc
        except (BindingMismatch, JournalError, ValueError, sqlite3.IntegrityError) as exc:
            raise StageConfigurationError("Research stage identity or state is invalid") from exc
        except (DailyBudgetExceeded, ResearchLimit) as exc:
            raise StageBudgetError("Research budget or reservation is unavailable") from exc
        finally:
            client = getattr(provider, "client", None)
            if client is not None:
                client.close()
            store.db.close()

    def _receipt_ref(self, stage):
        return f"research_stage_receipt:{self.context['run_key']}:{stage}"

    def _require_predecessor(self, store, stage):
        records = [
            json.loads(row[0])
            for row in store.db.execute(
                "SELECT body FROM documents WHERE scope=? AND kind=?",
                (store.scope, RECEIPT_KIND),
            )
        ]
        relevant = [record for record in records if record.get("run_key") == self.context["run_key"]]
        if stage == "acquisition":
            if relevant:
                raise StageConfigurationError("Acquisition receipt is missing from an advanced run")
            return
        prior = [
            record
            for record in relevant
            if record.get("manifest_hash") == self.manifest_hash
            and record.get("receipt", {}).get("next_stage") == stage
            and valid_receipt(record.get("receipt"))
            and valid_transition(record.get("stage"), record["receipt"])
        ]
        if len(prior) != 1:
            raise StageConfigurationError("Stage lacks one verified predecessor receipt")

    def _receipt(self, store, stage):
        return cached_stage_receipt(
            store,
            {"run_key": self.context["run_key"], "manifest_hash": self.manifest_hash},
            stage,
        )

    def _save_receipt(self, store, stage, receipt):
        receipt = {**receipt, "receipt_ref": self._receipt_ref(stage)}
        record = {
            "run_key": self.context["run_key"],
            "manifest_hash": self.manifest_hash,
            "stage": stage,
            "receipt": receipt,
            "recorded_at": now().isoformat(),
        }
        with store.db:
            try:
                store.db.execute(
                    "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                    (store.scope, RECEIPT_KIND, receipt["receipt_ref"], dumps(record)),
                )
            except sqlite3.IntegrityError:
                old = self._receipt(store, stage)
                if old != receipt:
                    raise StageConfigurationError("Conflicting stage receipt") from None
        return receipt

    def _acquire_call(self, journal, name, payload, handler, budget, stop, plan=None):
        _check_cancelled(stop)
        key = dumps(["acquisition", name])
        request_hash = _hash(payload)
        state = {"acquisition_budget": budget.checkpoint(), "acquisition_plan": plan}

        def preflight():
            budget.check()
            if self.before_dispatch is not None:
                self.before_dispatch()

        cached = journal.begin(key, request_hash, state, preflight=preflight)
        if cached is not None:
            return cached["result"]
        result = handler()
        journal.complete(
            key,
            request_hash,
            result,
            {"acquisition_budget": budget.checkpoint(), "acquisition_plan": plan},
        )
        return result

    def _acquisition(self, store, journal, reservation, provider, stop):
        saved = journal.load_state()
        if not isinstance(saved, dict) or saved.get("version") != 1:
            budget = Budget(self.cfg, reservation.allowance)
            if isinstance(saved, dict) and "acquisition_budget" in saved:
                budget.restore(saved["acquisition_budget"])
            else:
                journal.save_state({"acquisition_budget": budget.checkpoint()})
            symbol = self.context["symbol"]
            plan = saved.get("acquisition_plan") if isinstance(saved, dict) else None
            if plan is None:
                plan = {
                    "cutoff": now().isoformat(),
                    "fresh": bool(self.context.get("fresh", False)),
                }
                journal.save_state({"acquisition_budget": budget.checkpoint(), "acquisition_plan": plan})

            def acquire(name, payload, handler):
                return self._acquire_call(journal, name, payload, handler, budget, stop, plan)

            production = isinstance(provider, EvidenceProvider)
            evidence = copy.deepcopy(
                provider.get(
                    symbol,
                    fresh=plan["fresh"],
                    cutoff=plan["cutoff"],
                    acquisition_callback=acquire,
                )
                if production
                else acquire(
                    "evidence",
                    {"symbol": symbol, "fresh": plan["fresh"]},
                    lambda: provider.get(symbol, fresh=plan["fresh"]),
                )
            )
            x = self.x_factory(self.cfg, store)
            x_sources = (
                x.snapshot(symbol, acquisition_callback=acquire)
                if isinstance(x, XDiscovery)
                else acquire("x_snapshot", {"symbol": symbol}, lambda: x.snapshot(symbol))
            )
            evidence["sources"].extend(x_sources)
            budget.check()
            desk = self.desk_factory(
                self.cfg,
                provider,
                self.context,
                evidence,
                budget,
                self.models,
                defer_correction=True,
            )
            journal.save_state(desk.checkpoint())
        return {
            "status": "next",
            "next_stage": ROLE_ORDER[0],
            "receipt_ref": self._receipt_ref("acquisition"),
        }

    def _restore_desk(self, journal, saved, reservation, stage, provider, stop):
        symbol = self.context["symbol"]
        evidence = saved["desk"]["companies"][symbol]
        budget = Budget(self.cfg, reservation.allowance)
        opened = []
        phase = "correction" if stage.startswith("correction:") and stage != "correction:lead" else "initial"

        def checkpoint_factory(role):
            checkpoint = ResearchCheckpoint(
                journal,
                self.private_dir / "graph.sqlite",
                phase=phase,
                role=role,
                thread_id=self.context["run_key"],
                stop_event=stop,
                before_dispatch=self.before_dispatch,
            )
            opened.append(checkpoint)
            return checkpoint

        desk = self.desk_factory(
            self.cfg,
            provider,
            self.context,
            evidence,
            budget,
            self.models,
            checkpoint_factory=checkpoint_factory,
            defer_correction=True,
        )
        desk.restore(saved)
        return desk, opened

    def _delegate_once(self, desk, journal, phase, role):
        delegations = desk.continuation.setdefault("delegations", {})
        done = delegations.setdefault(phase, [])
        if role in done:
            return
        _check_cancelled(cancellation_event())
        if self.before_dispatch is not None:
            self.before_dispatch()
        desk.budget.tool()
        desk.budget.events.append(("research_delegation_reserved", {"phase": phase, "role": role}))
        done.append(role)
        journal.save_state(desk.checkpoint())

    def _run_stage(self, stage, desk, journal, store, reservation):
        if stage in ROLE_ORDER or stage.startswith("correction:") and stage != "correction:lead":
            phase = "correction" if stage.startswith("correction:") else "initial"
            role = stage.removeprefix("correction:")
            if phase == "correction" and (
                desk.correction is None or role not in desk.correction["invalidated"]
            ):
                raise StageConfigurationError("Correction role was not requested")
            if role not in desk.reports:
                self._delegate_once(desk, journal, phase, role)
                desk.specialist(role, {"messages": [HumanMessage(content=ASSIGNMENT)]})
                journal.save_state(desk.checkpoint())
            if role not in desk.reports:
                raise StageConfigurationError("Specialist did not produce a report")
            if phase == "initial":
                next_stage = ROLE_ORDER[ROLE_ORDER.index(role) + 1] if role != ROLE_ORDER[-1] else "lead"
            else:
                remaining = desk.correction["invalidated"]
                next_stage = (
                    "correction:" + remaining[remaining.index(role) + 1]
                    if role != remaining[-1]
                    else "correction:lead"
                )
            return {"status": "next", "next_stage": next_stage, "receipt_ref": self._receipt_ref(stage)}
        if stage in {"lead", "correction:lead"}:
            if stage == "correction:lead" and (desk.correction is None or set(desk.reports) != set(ROLES)):
                raise StageConfigurationError("Corrected reports and critique are incomplete")
            if stage == "lead" and desk.correction and set(desk.reports) != set(ROLES):
                next_stage = "correction:" + desk.correction["invalidated"][0]
            else:
                if "final_result" not in desk.continuation:
                    try:
                        desk.continuation["final_result"] = desk.run_lead()
                    except ResearchStageYield:
                        if not desk.correction or set(desk.reports) == set(ROLES):
                            raise StageConfigurationError(
                                "Correction yield lacked invalidated reports"
                            ) from None
                        journal.save_state(desk.checkpoint())
                        next_stage = "correction:" + desk.correction["invalidated"][0]
                    else:
                        journal.save_state(desk.checkpoint())
                        next_stage = "commit"
                else:
                    next_stage = "commit"
            return {"status": "next", "next_stage": next_stage, "receipt_ref": self._receipt_ref(stage)}
        if stage == "commit":
            self._commit(store, journal, desk, reservation)
            return {"status": "complete", "receipt_ref": self._receipt_ref(stage)}
        raise StageConfigurationError("Unknown research stage")

    def _audit(self, desk):
        budget = desk.budget
        return {
            "sources": list(desk.sources.values()),
            "reports": desk.reports,
            "calculations": desk.calculations,
            "arithmetic_records": desk.arithmetic_records(),
            "model_calls": budget.models,
            "tool_calls": budget.tools,
            "estimated_usd": budget.reserved,
            "stopped": budget.failed,
            "unrestricted_research": False,
            "testing_budget_override": False,
            "symbol": self.context["symbol"],
            "quote": self.context.get("quote"),
            "research_reference": self.context.get("research_reference"),
            "finalize_from": None,
            "team_report_times": desk.report_times,
            "events": copy.deepcopy(budget.events),
        }

    def _commit(self, store, journal, desk, reservation):
        result = desk.continuation.get("final_result")
        if not isinstance(result, dict) or result.get("decision", {}).get("symbol") != self.context["symbol"]:
            raise StageConfigurationError("Final research decision is missing or changed symbol")
        result = {
            **result,
            "quote": self.context.get("quote"),
            "research_reference": self.context.get("research_reference"),
        }
        proposal = Decision.model_validate(result["decision"])
        if result["critique"]["unresolved_material_gaps"] and proposal.action in {"BUY", "ADD"}:
            result["decision"] = {**result["decision"], "action": "WATCH", "quantity": "0"}
            if not any(kind == "adversarial_veto" for kind, _ in desk.budget.events):
                desk.budget.events.append(
                    ("adversarial_veto", {"symbol": self.context["symbol"], "critique": result["critique"]})
                )
        ids = {source["id"] for source in result["evidence"]["sources"]}
        for key in ("research", "valuation", "catalysts", "critique", "decision"):
            cited = result[key]["evidence_ids"]
            if not cited or not set(cited).issubset(ids):
                raise StageConfigurationError("Final research cites unknown evidence")
        unknown = sum(kind == "model_cost" for kind, _ in desk.budget.events) < desk.budget.models or any(
            kind == "model_cost" and event["estimated_usd"] is None for kind, event in desk.budget.events
        )
        if unknown:
            journal.save_state(desk.checkpoint())
            raise StageAmbiguous("Unknown model usage requires reconciliation before completion")
        completed_at = desk.continuation.get("research_completed_at")
        if completed_at is None:
            completed_at = now().isoformat()
            desk.continuation["research_completed_at"] = completed_at
            journal.save_state(desk.checkpoint())
        result = {**result, "research_completed_at": completed_at}
        audit = self._audit(desk)
        hashes = {
            "result": _hash(result),
            "audit": _hash(audit),
        }
        existing_hashes = desk.continuation.get("commit_hashes")
        if existing_hashes is not None and existing_hashes != hashes:
            raise StageConfigurationError("Committed research changed after its first attempt")
        desk.continuation["commit_hashes"] = hashes
        journal.save_state(desk.checkpoint())
        key = self.context["run_key"]
        with store.db:
            for kind, value in (("research_result", result), ("research_audit", audit)):
                previous = store.get(key, kind)
                if previous is None:
                    store.db.execute(
                        "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                        (store.scope, kind, key, dumps(value)),
                    )
                elif kind == "research_result" and previous != json.loads(dumps(value)):
                    raise StageConfigurationError("Saved research result conflicts with final decision")
                elif kind == "research_audit" and previous != json.loads(dumps(value)):
                    raise StageConfigurationError("Saved research audit conflicts with current state")
        reservation.finish(desk.budget.reserved, unknown=False)
        journal.save_state(desk.checkpoint())

    def _settled_commit_exists(self, store, journal):
        key = self.context["run_key"]
        result, audit = store.get(key, "research_result"), store.get(key, "research_audit")
        if not result or not audit or audit.get("symbol") != self.context["symbol"]:
            return False
        saved = journal.load_state()
        hashes = (
            saved.get("desk", {}).get("continuation", {}).get("commit_hashes")
            if isinstance(saved, dict)
            else None
        )
        if not hashes or hashes != {
            "result": _hash(result),
            "audit": _hash(audit),
        }:
            raise StageConfigurationError("Settled commit does not match its frozen research state")

        records = [
            json.loads(row[0])
            for row in store.db.execute(
                "SELECT body FROM documents WHERE scope='global' AND kind='research_spend' "
                "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
                (key, store.scope),
            )
        ]
        if len(records) != 1:
            raise StageConfigurationError("Committed research has no unique spending record")
        record = records[0]
        if record.get("outcome") == "aborted_accounted":
            raise StageConfigurationError("Accounting-closed research cannot publish a commit receipt")
        if not record.get("durable") or record.get("config_sha256") != fingerprint(self.cfg):
            raise StageConfigurationError("Committed research spending identity changed")
        if record.get("status") == "unknown" or record.get("unknown_total"):
            raise StageAmbiguous("Unknown model usage requires reconciliation")
        if record.get("status") == "settled":
            if dec(record["usd"]) != dec(audit["estimated_usd"]):
                raise StageConfigurationError("Settled spend differs from audited research cost")
            return True
        if record.get("status") == "reserved":
            return False
        raise StageConfigurationError("Committed research has invalid spending status")
