import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import AT

from trader import research_runtime
from trader.research_manifest import prepare_manifest
from trader.research_workflow import StageBudgetError, StageConfigurationError
from trader.spending import DailyReservation


def arguments(**updates):
    return SimpleNamespace(
        **{
            "address": "127.0.0.1:7233",
            "namespace": "default",
            "tls": False,
            "command": "status",
            "run_key": "one",
            "manifest_hash": "a" * 64,
            **updates,
        }
    )


def test_remote_plaintext_rejected_before_connect(monkeypatch):
    monkeypatch.setattr(research_runtime.Client, "connect", lambda *a, **kw: pytest.fail("connect"))
    with pytest.raises(ValueError, match="require TLS"):
        asyncio.run(research_runtime.run(arguments(address="remote:7233")))


def test_worker_requires_trace_outbox_before_connecting(env, monkeypatch):
    from trader import observability

    service, _, _ = env
    monkeypatch.setenv("OBSERVABILITY_DATABASE_URL", "configured-for-test")
    monkeypatch.setattr(observability, "_sink", None)
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    calls = []
    monkeypatch.setattr(research_runtime, "configure", lambda path: calls.append(path))
    monkeypatch.setattr(research_runtime.Client, "connect", lambda *a, **kw: pytest.fail("server access"))
    with pytest.raises(observability.TracePersistenceError, match="outbox is unavailable"):
        asyncio.run(
            research_runtime.run(arguments(command="worker", config="config.toml", task_queue="test"))
        )
    assert calls == [service.cfg.database]


def test_worker_sigterm_waits_for_shutdown_and_restores_signal_handlers(env, monkeypatch):
    import signal

    service, _, _ = env
    events = []
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(research_runtime, "configure", lambda _: None)
    monkeypatch.setattr(research_runtime, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(research_runtime, "stage_handler", lambda *a: lambda request: None)

    async def connect(*a, **kw):
        return object()

    class Worker:
        def __init__(self, *args, **kwargs):
            assert kwargs["max_concurrent_activities"] == 1

        async def __aenter__(self):
            asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGTERM)
            return self

        async def __aexit__(self, *args):
            events.append("shutdown_started")
            await asyncio.sleep(0)
            events.append("shutdown_finished")

    monkeypatch.setattr(research_runtime.Client, "connect", connect)
    monkeypatch.setattr(research_runtime, "Worker", Worker)
    asyncio.run(
        research_runtime.run(
            arguments(command="worker", config="config.toml", root="private", task_queue="test")
        )
    )
    assert events == ["shutdown_started", "shutdown_finished"]
    assert {sig: signal.getsignal(sig) for sig in before} == before


def test_inspect_cli_reads_existing_journal_without_server_or_retry(env, tmp_path, monkeypatch, capsys):
    import json

    from trader.research_journal import Journal
    from trader.research_manifest import load_manifest, run_directory

    service, _, _ = env
    root = tmp_path / "runs"
    context = {
        "run_key": "inspect-one",
        "symbol": "ACME",
        "research_only": True,
        "current_timestamp": AT.isoformat(),
        "portfolio": {},
    }
    reference = prepare_manifest(root, service.cfg, service.store.scope, context)
    manifest = load_manifest(root, reference, service.cfg, service.store.scope)
    path = run_directory(root, context["run_key"]) / "operations.sqlite"
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(research_runtime.Client, "connect", lambda *a, **kw: pytest.fail("server access"))
    args = [
        "--root",
        str(root),
        "inspect",
        "--run-key",
        context["run_key"],
        "--manifest-hash",
        reference["manifest_hash"],
    ]
    with pytest.raises(ValueError, match="Existing regular"):
        research_runtime.main(args)
    assert not path.exists()
    with Journal(path, manifest["binding"]).lease() as journal:
        journal.begin("model:1", "request-hash", {"prompt": "private prompt"})
    research_runtime.main(args)
    output = capsys.readouterr().out
    assert "private prompt" not in output
    assert json.loads(output)["pending_operations"][0]["key"] == "model:1"


@pytest.mark.parametrize("fails", [False, True])
def test_stage_closes_owned_model_transport_on_success_or_failure(env, monkeypatch, fails):
    service, _, _ = env
    closed = []
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(
        research_runtime,
        "load_manifest",
        lambda *a: {
            "context": {"runtime_control": {}},
            "binding": {},
        },
    )
    monkeypatch.setattr(research_runtime, "reconcile_if_stale", lambda *a: None)
    monkeypatch.setattr(research_runtime, "control_snapshot", lambda *a: {})
    monkeypatch.setattr("trader.research_stages.cached_stage_receipt", lambda *a: None)
    monkeypatch.setattr(
        "trader.research_team.DeepResearchTeam.models",
        lambda *a, **kw: {
            "research_lead": SimpleNamespace(http_client=SimpleNamespace(close=lambda: closed.append(True))),
        },
    )

    def stage(*args, **kwargs):
        def execute(request):
            if fails:
                raise ValueError("fixture stage failed")
            return {"status": "complete"}

        return execute

    monkeypatch.setattr("trader.research_stages.ResearchStages", stage)
    handler = research_runtime.stage_handler("config.toml", "private")
    request = {"run_key": "one", "manifest_hash": "a" * 64, "stage": "business"}
    if fails:
        with pytest.raises(ValueError, match="fixture stage failed"):
            handler(request)
    else:
        assert handler(request) == {"status": "complete"}
    assert closed == [True]


def test_accounting_cli_uses_offline_close_without_server(env, monkeypatch, capsys):
    from trader import research_accounting

    service, _, _ = env
    calls = []
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(research_runtime.Client, "connect", lambda *a, **kw: pytest.fail("server access"))

    def close(cfg, path, root, reference):
        calls.append((cfg, path, root, reference))
        return {"outcome": "aborted_accounted"}

    monkeypatch.setattr(research_accounting, "close_expired_run", close)
    research_runtime.main(
        [
            "--root",
            "private-runs",
            "close-accounting",
            "--run-key",
            "expired-one",
            "--manifest-hash",
            "a" * 64,
        ]
    )
    assert calls == [
        (
            service.cfg,
            service.cfg.database,
            "private-runs",
            {
                "run_key": "expired-one",
                "manifest_hash": "a" * 64,
            },
        )
    ]
    assert "aborted_accounted" in capsys.readouterr().out


def test_status_and_cancel_verify_manifest_without_starting_work(monkeypatch):
    calls = []

    class Handle:
        async def query(self, query):
            return {"state": "blocked", "manifest_hash": "a" * 64}

        async def cancel(self):
            calls.append("cancel")

    class Client:
        def get_workflow_handle(self, identity):
            calls.append(identity)
            return Handle()

    async def connect(*a, **kw):
        return Client()

    monkeypatch.setattr(research_runtime.Client, "connect", connect)
    assert asyncio.run(research_runtime.run(arguments()))["state"] == "blocked"
    asyncio.run(research_runtime.run(arguments(command="cancel")))
    assert calls == ["research:one", "research:one", "cancel"]
    with pytest.raises(ValueError, match="different frozen manifest"):
        asyncio.run(research_runtime.run(arguments(command="cancel", manifest_hash="b" * 64)))
    assert calls.count("cancel") == 1


@pytest.mark.parametrize("command", ["start", "resume", "worker"])
def test_paid_commands_require_explicit_opt_in(command):
    extra = [] if command == "worker" else ["--run-key", "one", "--manifest-hash", "a" * 64]
    with pytest.raises(SystemExit) as error:
        research_runtime.main([command, *extra])
    assert error.value.code == 2


def test_resubmit_cli_requires_explicit_authority_and_only_records_approval(
    env, tmp_path, monkeypatch, capsys
):
    import json

    from trader.research_client import TemporalResearchClient

    service, _, _ = env
    proof = {"verified_by": "operator", "reason": "Reviewed absent submission"}
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(proof))
    args = ["authorize-resubmit", "--run-key", "one", "--manifest-hash", "a" * 64, "--proof", str(path)]
    with pytest.raises(SystemExit) as error:
        research_runtime.main(args)
    assert error.value.code == 2
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(research_runtime, "load_manifest", lambda *a: {})
    calls = []

    def authorize(self, reference, supplied):
        calls.append((reference, supplied))
        return {"approved": True}

    monkeypatch.setattr(TemporalResearchClient, "authorize_resubmit", authorize)
    monkeypatch.setattr(TemporalResearchClient, "poll", lambda *a: pytest.fail("must not dispatch"))
    research_runtime.main([*args, "--allow-paid"])
    assert calls == [({"run_key": "one", "manifest_hash": "a" * 64}, proof)]
    assert json.loads(capsys.readouterr().out) == {"approved": True}


def test_runtime_rechecks_stop_control_reconciliation_and_funding(env, tmp_path, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(research_runtime, "now", lambda: AT)
    service.kill_path.write_text("Trading remains paused during supervised research")
    from trader.models import now

    context = {
        "run_key": "one",
        "symbol": "ACME",
        "research_only": True,
        "portfolio": {},
        "current_timestamp": now().isoformat(),
        "runtime_control": research_runtime.control_snapshot(service.cfg, service.store),
    }
    root = tmp_path / "runs"
    reference = prepare_manifest(root, service.cfg, service.store.scope, context)
    with monkeypatch.context() as admission:
        admission.setattr("trader.paper_dispatch.funding_gate", lambda *a, **kw: None)
        DailyReservation.for_durable_run(service.store, service.cfg, "one")
    checks = []

    def funding(store, cfg, current, *, reservation_key):
        checks.append(reservation_key)

    monkeypatch.setattr(research_runtime, "funding_gate", funding)
    research_runtime.check_runtime("config.toml", root, reference)
    assert len(checks) == 1 and service.kill_path.exists()
    monkeypatch.setattr(research_runtime, "funding_gate", lambda *a, **kw: "funding revoked")
    with pytest.raises(StageBudgetError, match="revoked"):
        research_runtime.check_runtime("config.toml", root, reference)
    service.store.put("reconciliation", {"ok": False})
    with pytest.raises(StageConfigurationError):
        research_runtime.check_runtime("config.toml", root, reference)
    service.store.put("reconciliation", {"ok": True, "at": (AT - timedelta(days=1)).isoformat()})
    with pytest.raises(StageConfigurationError):
        research_runtime.check_runtime("config.toml", root, reference)
    service.store.put("reconciliation", {"ok": True, "at": AT.isoformat()})
    service.kill_path.write_text("New operator stop")
    with pytest.raises(StageConfigurationError):
        research_runtime.check_runtime("config.toml", root, reference)


def test_stage_refreshes_stale_reconciliation(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr(research_runtime, "now", lambda: AT)
    monkeypatch.setattr("trader.alpaca.AlpacaBroker", lambda cfg: service.broker)
    monkeypatch.setattr("trader.alpaca.AlpacaData", lambda cfg: service.data)
    monkeypatch.setattr("trader.service.Service", lambda *a: service)
    service.store.put("reconciliation", {"ok": True, "at": (AT - timedelta(days=1)).isoformat()})
    research_runtime.reconcile_if_stale(service.cfg, service.store)
    assert service.store.get("reconciliation")["at"] == AT.isoformat()


@pytest.mark.parametrize("temporal_server", [False, True])
def test_operator_runtime_full_stages_with_real_funding_gate(
    env, tmp_path, monkeypatch, decision, temporal_server
):
    import json

    from test_research_team import Evidence, TeamModel

    from trader.models import dec
    from trader.paper_dispatch import fingerprint
    from trader.research_workflow import STAGES

    service, _, _ = env
    service.cfg.research_max_cost_usd = dec(10)
    service.cfg.input_cost_per_million = service.cfg.output_cost_per_million = dec(1)
    model = TeamModel(
        decision={
            **decision.model_dump(mode="json"),
            "action": "WATCH",
            "quantity": "0",
            "evidence_ids": ["fixture-sec"],
        }
    )
    monkeypatch.setattr(research_runtime.Settings, "load", lambda _: service.cfg)
    monkeypatch.setattr(research_runtime, "now", lambda: AT)
    monkeypatch.setattr("trader.research_stages.now", lambda: AT)
    monkeypatch.setattr("trader.research_team.DeepResearchTeam.models", lambda self, **kwargs: model)
    monkeypatch.setattr("trader.evidence.EvidenceProvider", lambda *args: Evidence())
    monkeypatch.setattr("trader.alpaca.AlpacaData", lambda cfg: service.data)
    service.kill_path.write_text("Paper trading remains paused")
    funding = {
        "basis": "provider_confirmed_available_credit",
        "evidence_sha256": "a" * 64,
        "verified_by": "fixture-only",
        "observed_at": AT.isoformat(),
        "expires_at": (AT + timedelta(days=3)).isoformat(),
        "available_usd": "25",
        "buffer_usd": "10",
        "spending_sha256": fingerprint([]),
        "account_id": "fixture",
        "project_id": "fixture",
        "allocation": {
            "status": "operator_approved",
            "approved_by": "fixture-only",
            "approved_at": AT.isoformat(),
            "valid_until": (AT + timedelta(days=2)).isoformat(),
            "account_id": "fixture",
            "project_id": "fixture",
            "exclusive_account_use_evidence_sha256": "b" * 64,
            "funding_evidence_sha256": "a" * 64,
            "spending_baseline": {},
            "usd": "15",
        },
    }
    service.store.put("confirmed", funding, "research_funding", scope="global")
    context = {
        "run_key": "operator-runtime",
        "symbol": "ACME",
        "research_only": True,
        "current_timestamp": AT.isoformat(),
        "portfolio": {},
        "runtime_control": research_runtime.control_snapshot(service.cfg, service.store),
    }
    root = tmp_path / "runs"
    reference = prepare_manifest(root, service.cfg, service.store.scope, context)
    handler = research_runtime.stage_handler("config.toml", root)
    if temporal_server:
        from temporalio.testing import WorkflowEnvironment
        from temporalio.worker import Worker

        from trader.research_workflow import ResearchActivities, ResearchWorkflow, start_research_workflow

        async def execute():
            async with await WorkflowEnvironment.start_local() as temporal:
                activities = ResearchActivities(handler)
                async with Worker(
                    temporal.client,
                    task_queue="real-funding",
                    workflows=[ResearchWorkflow],
                    activities=[activities.research_stage],
                ):
                    handle = await start_research_workflow(
                        temporal.client, reference["run_key"], reference["manifest_hash"], "real-funding"
                    )
                    assert (await asyncio.wait_for(handle.result(), timeout=30))["state"] == "complete"

        asyncio.run(execute())
    else:
        for stage in STAGES:
            receipt = handler({**reference, "stage": stage})
        assert receipt["status"] == "complete"
    spend = json.loads(
        service.store.db.execute(
            "SELECT body FROM documents WHERE scope='global' AND kind='research_spend'"
        ).fetchone()[0]
    )
    assert spend["status"] == "settled" and spend["unknown_total"] is False
    assert service.store.get(context["run_key"], "research_result")["decision"]["action"] == "WATCH"
    assert service.kill_path.exists() and service.broker.submits == 0
    assert service.store.get("ACME", "thesis") is None


@pytest.mark.parametrize("stratum", ["scheduled", "safety"])
def test_worker_study_authority_requires_original_claim(env, monkeypatch, stratum):
    from trader.models import dumps
    from trader.paper_dispatch import fingerprint, source_fingerprint

    service, _, _ = env
    monkeypatch.setattr(research_runtime, "now", lambda: AT)
    run_key = "study:scheduled:01" if stratum == "scheduled" else "study:safety:01:ACME"
    context = {"symbol": "ACME", "run_key": run_key, "study_id": "study", "study_stratum": stratum}
    schedule = {
        "study_id": "study",
        "opportunities": [
            {
                "id": run_key,
                "symbol": "ACME",
                "claim_from": (AT - timedelta(minutes=1)).isoformat(),
                "claim_before": (AT + timedelta(minutes=1)).isoformat(),
            }
        ],
    }
    study = {
        "status": "operator_approved",
        "approved_by": "fixture-only",
        "approved_at": AT.isoformat(),
        "acceptance_evidence_sha256": "a" * 64,
        "experiment_id": service.store.scope,
        "config_sha256": fingerprint(service.cfg),
        "source_sha256": source_fingerprint(),
        "schedule": schedule,
        "schedule_sha256": fingerprint(schedule),
    }
    service.store.put("active", study, "paper_study")
    with pytest.raises(ValueError, match="matching approved study"):
        research_runtime.control_snapshot(service.cfg, service.store)
    with pytest.raises(ValueError, match="dispatcher invocation"):
        research_runtime.control_snapshot(service.cfg, service.store, context)
    service.store.claim_paper_slot("study", run_key, {"symbol": "ACME", "at": AT}, kind="paper_invocation")
    with pytest.raises(ValueError, match="original"):
        research_runtime.control_snapshot(service.cfg, service.store, context)
    if stratum == "scheduled":
        service.store.claim_paper_slot(
            "study", run_key, {"symbol": "ACME", "at": AT, "schedule_sha256": study["schedule_sha256"]}
        )
    else:
        service.store.put(run_key, {"status": "pending", "at": AT}, "job")
    assert research_runtime.control_snapshot(service.cfg, service.store, context)[
        "study_sha256"
    ] == fingerprint(study)
    if stratum == "scheduled":
        claim = service.store.get(dumps(["study", run_key]), "paper_slot")
        claim["outcome"] = "missed"
        service.store.put(dumps(["study", run_key]), claim, "paper_slot")
    else:
        service.store.put(run_key, {"status": "failed", "at": AT}, "job")
    with pytest.raises(ValueError, match="original"):
        research_runtime.control_snapshot(service.cfg, service.store, context)


def test_temporal_configuration_requires_tls_for_remote_hosts():
    from trader.config import Settings

    with pytest.raises(ValueError, match="require TLS"):
        Settings(temporal_address="remote:7233")
    assert Settings(temporal_address="remote:7233", temporal_tls=True).temporal_tls
    assert Settings(temporal_address="127.0.0.1:7233").temporal_tls is False
