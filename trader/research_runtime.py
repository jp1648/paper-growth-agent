"""Operator entry point for research-only Temporal workers and frozen runs."""

import argparse
import asyncio
import hashlib
import json
import signal
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from .config import Settings
from .models import dt, dumps, now
from .observability import configure, emit
from .paper_dispatch import fingerprint, funding_gate, source_fingerprint
from .research_manifest import load_manifest, prepare_manifest, run_directory
from .research_workflow import (
    ResearchActivities,
    ResearchWorkflow,
    StageBudgetError,
    StageConfigurationError,
    start_research_workflow,
)
from .store import Store


def control_snapshot(cfg, store, context=None):
    if not store.experiment:
        raise ValueError("Initialized experiment required")
    account = store.experiment["account_id"]
    registry = store.control_path(cfg.control_dir, account, ".ledger")
    if registry.is_symlink() or registry.read_text() != str(store.path):
        raise ValueError("Account ledger binding changed")
    reconciliation = store.get("reconciliation") or {}
    if reconciliation.get("ok") is not True or not timedelta(0) <= now() - dt(
        reconciliation["at"]
    ) <= timedelta(seconds=cfg.research_max_seconds + 60):
        raise ValueError("Successful account reconciliation required")
    study = store.get("active", "paper_study")
    context = context or {}
    if study is not None:
        schedule = study["schedule"]
        if (
            study.get("status") != "operator_approved"
            or not study.get("approved_by")
            or not study.get("acceptance_evidence_sha256")
            or study.get("experiment_id") != store.scope
            or study.get("config_sha256") != fingerprint(cfg)
            or study.get("source_sha256") != source_fingerprint()
            or study.get("schedule_sha256") != fingerprint(schedule)
            or context.get("study_id") != schedule["study_id"]
            or dt(study["approved_at"]) > now()
        ):
            raise ValueError("Research requires matching approved study authority")
        key = dumps([context["study_id"], context["run_key"]])
        invocation = store.get(key, "paper_invocation")
        if (
            not invocation
            or invocation.get("symbol") != context["symbol"]
            or invocation.get("status") != "claimed"
        ):
            raise ValueError("Research requires the original dispatcher invocation")
        if context.get("study_stratum") == "scheduled":
            claim = store.get(key, "paper_slot")
            slots = [s for s in schedule["opportunities"] if s["id"] == context["run_key"]]
            if (
                len(slots) != 1
                or slots[0]["symbol"] != context["symbol"]
                or not claim
                or claim.get("status") != "claimed"
                or claim.get("outcome") == "missed"
                or claim.get("symbol") != context["symbol"]
                or claim.get("schedule_sha256") != study["schedule_sha256"]
                or not dt(slots[0]["claim_from"]) <= dt(claim["at"]) < dt(slots[0]["claim_before"])
            ):
                raise ValueError("Research requires the original scheduled slot claim")
        elif context.get("study_stratum") == "safety":
            job = store.get(context["run_key"], "job")
            if (
                not context["run_key"].startswith(context["study_id"] + ":safety:")
                or not job
                or job.get("status") not in {"running", "pending"}
            ):
                raise ValueError("Research requires the original held-position review job")
        else:
            raise ValueError("Unknown study research stratum")
    elif context.get("study_id") is not None:
        raise ValueError("Original research study is no longer active")
    pause = store.control_path(cfg.control_dir, account, ".paused")
    if pause.is_symlink():
        raise ValueError("Invalid stop control")
    return {
        "account_id": account,
        "study_sha256": fingerprint(study) if study is not None else None,
        "stop_control": {
            "sha256": hashlib.sha256(pause.read_bytes()).hexdigest(),
            "mtime_ns": pause.stat().st_mtime_ns,
        }
        if pause.exists()
        else None,
    }


def reconcile_if_stale(cfg, store):
    reconciliation = store.get("reconciliation") or {}
    if reconciliation.get("ok") is True and timedelta(0) <= now() - dt(reconciliation["at"]) <= timedelta(
        seconds=60
    ):
        return
    from .alpaca import AlpacaBroker, AlpacaData
    from .service import Service

    service = Service(cfg, store, AlpacaBroker(cfg), AlpacaData(cfg))
    service.require_experiment()
    service.reconcile()


def check_runtime(config_path, root, reference):
    """Recheck authority in each model/tool thread, before recording a pending operation."""
    cfg = Settings.load(config_path)
    store = Store(cfg.database)
    try:
        try:
            manifest = load_manifest(root, reference, cfg, store.scope)
            if manifest["context"].get("runtime_control") != control_snapshot(
                cfg, store, manifest["context"]
            ):
                raise ValueError("Research stop control changed")
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise StageConfigurationError("Research runtime or account controls changed") from exc
        records = store.db.execute(
            "SELECT key FROM documents WHERE scope='global' AND kind='research_spend' "
            "AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
            (reference["run_key"], store.scope),
        ).fetchall()
        if len(records) != 1:
            raise StageBudgetError("Unique original spending reservation required")
        reason = funding_gate(store, cfg, now(), reservation_key=records[0][0])
        if reason:
            raise StageBudgetError(reason)
        # Prove the required local trace can persist before an operation becomes pending.
        emit("research_dispatch_preflight", reference, required=True, run_key=reference["run_key"])
    finally:
        store.db.close()


def stage_handler(config_path, root):
    def handle(request):
        from .alpaca import AlpacaData
        from .evidence import EvidenceProvider
        from .research_stages import ResearchStages, cached_stage_receipt
        from .research_team import DeepResearchTeam

        cfg = Settings.load(config_path)
        store = Store(cfg.database)
        try:
            reference = {key: request[key] for key in ("run_key", "manifest_hash")}
            try:
                manifest = load_manifest(root, reference, cfg, store.scope)
                replay = cached_stage_receipt(store, reference, request["stage"])
                if replay is not None:
                    return replay
                reconcile_if_stale(cfg, store)
                if manifest["context"].get("runtime_control") != control_snapshot(
                    cfg, store, manifest["context"]
                ):
                    raise ValueError("Research stop control changed")
            except (ValueError, TypeError, KeyError, OSError) as exc:
                raise StageConfigurationError("Frozen research inputs are unavailable or changed") from exc
            models = DeepResearchTeam(cfg, store, None).models(durable=True)
        finally:
            store.db.close()
        try:
            return ResearchStages(
                cfg,
                cfg.database,
                lambda active_store: EvidenceProvider(cfg, active_store, AlpacaData(cfg)),
                manifest["context"],
                models,
                run_directory(root, reference["run_key"]),
                manifest["binding"],
                before_dispatch=lambda: check_runtime(config_path, root, reference),
            )(request)
        finally:
            if isinstance(models, dict):
                for model in models.values():
                    client = getattr(model, "http_client", None)
                    if client is not None:
                        client.close()

    return handle


async def run(args):
    if not args.tls and args.address not in {"localhost:7233", "127.0.0.1:7233", "[::1]:7233"}:
        raise ValueError("Remote Temporal connections require TLS")
    if args.command == "worker":
        configure(Settings.load(args.config).database)
        emit("research_worker_startup", {"task_queue": args.task_queue}, required=True)
    client = await Client.connect(args.address, namespace=args.namespace, tls=args.tls)
    if args.command == "worker":
        activities = ResearchActivities(stage_handler(args.config, args.root))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            for sig in previous:
                loop.add_signal_handler(sig, stop.set)
            async with Worker(
                client,
                task_queue=args.task_queue,
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
                max_concurrent_activities=1,
            ):
                await stop.wait()
        finally:
            for sig, handler in previous.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, handler)
        return None
    reference = {"run_key": args.run_key, "manifest_hash": args.manifest_hash}
    if args.command in {"start", "resume"}:
        cfg = Settings.load(args.config)
        store = Store(cfg.database)
        try:
            load_manifest(args.root, reference, cfg, store.scope)
        finally:
            store.db.close()
    if args.command == "start":
        handle = await start_research_workflow(client, args.run_key, args.manifest_hash, args.task_queue)
        return {"workflow_id": handle.id, **reference}
    handle = client.get_workflow_handle(f"research:{args.run_key}")
    status = await handle.query(ResearchWorkflow.status)
    if status["manifest_hash"] != args.manifest_hash:
        raise ValueError("Workflow has a different frozen manifest")
    if args.command == "resume":
        await handle.signal(ResearchWorkflow.resume)
    elif args.command == "cancel":
        await handle.cancel()
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resumable research only; never submits broker orders")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--root", default="state/research-runs")
    parser.add_argument("--address", default="127.0.0.1:7233")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--task-queue", default="trader-research")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare", help="Reconcile account and freeze research-only inputs; no model calls"
    )
    prepare.add_argument("--context", required=True)
    recovery = sub.add_parser(
        "authorize-resubmit", help="Approve one same-ID submission after verified absent history"
    )
    recovery.add_argument("--run-key", required=True)
    recovery.add_argument("--manifest-hash", required=True)
    recovery.add_argument("--proof", required=True, help="Operator-reviewed evidence JSON")
    recovery.add_argument("--allow-paid", required=True, action="store_true")
    for command in ("start", "status", "inspect", "close-accounting", "resume", "cancel", "worker"):
        child = sub.add_parser(command)
        if command != "worker":
            child.add_argument("--run-key", required=True)
            child.add_argument("--manifest-hash", required=True)
        if command in {"start", "resume", "worker"}:
            child.add_argument("--allow-paid", required=True, action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        cfg = Settings.load(args.config)
        store = Store(cfg.database)
        try:
            context = json.loads(Path(args.context).read_text())
            reconcile_if_stale(cfg, store)
            context["runtime_control"] = control_snapshot(cfg, store, context)
            result = prepare_manifest(args.root, cfg, store.scope, context)
        finally:
            store.db.close()
    elif args.command == "close-accounting":
        from .research_accounting import close_expired_run

        cfg = Settings.load(args.config)
        result = close_expired_run(
            cfg,
            cfg.database,
            args.root,
            {"run_key": args.run_key, "manifest_hash": args.manifest_hash},
        )
    elif args.command == "inspect":
        from .research_journal import Journal

        cfg = Settings.load(args.config)
        store = Store(cfg.database)
        try:
            reference = {"run_key": args.run_key, "manifest_hash": args.manifest_hash}
            manifest = load_manifest(args.root, reference, cfg, store.scope)
            path = run_directory(args.root, args.run_key) / "operations.sqlite"
            if not path.is_file() or path.is_symlink():
                raise ValueError("Existing regular research journal required")
            with Journal(path, manifest["binding"]).lease() as journal:
                result = {**reference, **journal.inspect_operations()}
        finally:
            store.db.close()
    elif args.command == "authorize-resubmit":
        from .research_client import TemporalResearchClient

        cfg = Settings.load(args.config)
        store = Store(cfg.database)
        try:
            reference = {"run_key": args.run_key, "manifest_hash": args.manifest_hash}
            load_manifest(args.root, reference, cfg, store.scope)
            if not args.tls and args.address not in {"localhost:7233", "127.0.0.1:7233", "[::1]:7233"}:
                raise ValueError("Remote Temporal connections require TLS")
            client = TemporalResearchClient(
                cfg,
                store,
                root=args.root,
                address=args.address,
                namespace=args.namespace,
                task_queue=args.task_queue,
                tls=args.tls,
            )
            result = client.authorize_resubmit(reference, json.loads(Path(args.proof).read_text()))
        finally:
            store.db.close()
    else:
        result = asyncio.run(run(args))
    if result is not None:
        print(dumps(result))


if __name__ == "__main__":
    main()
