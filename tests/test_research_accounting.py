import hashlib
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from trader.config import Security, Settings
from trader.models import dec, dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_accounting import AccountingEvidenceError, close_expired_run
from trader.research_journal import Journal
from trader.research_manifest import load_manifest, prepare_manifest, run_directory
from trader.store import Store


def fixture_run(
    tmp_path, *, usage=None, wire_model=None, with_response=True, prior_known=True, pending_kind="model"
):
    cfg = Settings(
        database=str(tmp_path / "ledger.sqlite"),
        model="fixture-model",
        input_cost_per_million="1",
        output_cost_per_million="2",
        universe={"ACME": Security(sector="Technology", cik="1")},
    )
    store = Store(cfg.database)
    store.put("active", {"id": "experiment-one"}, scope="global")
    store.experiment = store.get("active", scope="global")
    context = {
        "run_key": "expired-one",
        "symbol": "ACME",
        "research_only": True,
        "portfolio": {},
        "current_timestamp": now().isoformat(),
    }
    root = tmp_path / "runs"
    reference = prepare_manifest(root, cfg, store.scope, context)
    binding = load_manifest(root, reference, cfg, store.scope)["binding"]
    started = now() - timedelta(hours=1)
    day = started.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    spend = {
        "run_key": context["run_key"],
        "experiment_id": store.scope,
        "day": day,
        "started_at": started,
        "usd": dec(5),
        "durable_allowance": dec(5),
        "unrestricted_research": False,
        "testing_budget_override": False,
        "unknown_total": False,
        "status": "reserved",
        "durable": True,
        "config_sha256": fingerprint(cfg),
    }
    store.put("spend-key", spend, "research_spend", scope="global")
    route = cfg.research_model("valuation")
    budget = {
        "models": 1,
        "reserved": dec("0.00001"),
        "limit": dec(5),
        "day": day,
        "unrestricted_research": False,
        "events": [
            (
                "model_cost",
                {
                    "stage": "valuation",
                    "model": route.model,
                    "reasoning_effort": route.reasoning_effort,
                    "input_cost_per_million": route.input_cost_per_million,
                    "output_cost_per_million": route.output_cost_per_million,
                    "usage": {"input_tokens": 10, "output_tokens": 0} if prior_known else {},
                    "estimated_usd": dec("0.00001") if prior_known else None,
                },
            )
        ],
    }
    op_key = (
        dumps(["initial", "model", "valuation", "original-node"])
        if pending_kind == "model"
        else dumps(["acquisition", pending_kind])
    )
    journal = Journal(run_directory(root, context["run_key"]) / "operations.sqlite", binding)
    with journal.lease():
        if pending_kind == "none":
            store.db.close()
            return cfg, root, reference
        journal.begin(
            op_key,
            "a" * 64,
            {"budget": budget, "desk": {}} if pending_kind == "model" else {"acquisition_budget": budget},
        )
        if pending_kind != "model":
            store.db.close()
            return cfg, root, reference
        identifier = journal.record_dispatch(
            op_key,
            "a" * 64,
            {
                "provider": "openai",
                "model": route.model,
                "reasoning_effort": route.reasoning_effort,
                "max_output_tokens": cfg.research_max_output_tokens,
                "service_tier": "default",
                "projected_request": {"original": "private"},
            },
        )
        request_body = {
            "model": route.model,
            "max_output_tokens": cfg.research_max_output_tokens,
            "service_tier": "default",
            "reasoning": {"effort": route.reasoning_effort},
            "store": False,
        }
        wire_request = {
            "method": "POST",
            "url": "https://api.openai.com/v1/responses",
            "body": json.dumps(request_body).encode(),
            "headers_sha256": [(b"x-client-request-id", hashlib.sha256(identifier.encode()).hexdigest())],
        }
        wire_response = {
            "status": 200,
            "headers": [],
            "body": json.dumps(
                {
                    "id": "resp_fixture",
                    "object": "response",
                    "model": wire_model or route.model,
                    "status": "completed",
                    "usage": usage
                    if usage is not None
                    else {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
                }
            ).encode(),
        }
        if with_response:
            journal.exchange(op_key, "a" * 64, identifier, wire_request, lambda: wire_response)
    store.db.close()
    return cfg, root, reference


def test_expired_known_usage_settles_once_without_result_or_provider(tmp_path):
    cfg, root, reference = fixture_run(tmp_path)
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["status"] == "settled"
    assert receipt["outcome"] == "aborted_accounted"
    assert dec(receipt["settlement_usd"]) == dec("0.00009")
    assert receipt["usage"] == {"input_tokens": 20, "output_tokens": 30}
    assert close_expired_run(cfg, cfg.database, root, reference) == receipt
    store = Store(cfg.database)
    assert store.get("spend-key", "research_spend", scope="global")["status"] == "settled"
    assert store.get(reference["run_key"], "research_result") is None
    store.db.close()


@pytest.mark.parametrize(
    "usage", [{}, {"input_tokens": -1, "output_tokens": 2}, {"input_tokens": True, "output_tokens": 2}]
)
def test_missing_or_invalid_usage_retains_full_unknown_liability(tmp_path, usage):
    cfg, root, reference = fixture_run(tmp_path, usage=usage)
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["status"] == "unknown" and receipt["unknown_total"] is True
    assert dec(receipt["settlement_usd"]) == dec(5)
    store = Store(cfg.database)
    spend = store.get("spend-key", "research_spend", scope="global")
    assert spend["status"] == "unknown" and spend["outcome"] == "aborted_accounted"
    store.db.close()


def test_mismatched_provider_model_cannot_close(tmp_path):
    cfg, root, reference = fixture_run(tmp_path, wire_model="other-model")
    with pytest.raises(AccountingEvidenceError, match="different model identity"):
        close_expired_run(cfg, cfg.database, root, reference)
    store = Store(cfg.database)
    assert store.get("spend-key", "research_spend", scope="global")["status"] == "reserved"
    store.db.close()


@pytest.mark.parametrize("kwargs", [{"with_response": False}, {"prior_known": False}])
def test_ambiguous_response_or_prior_usage_keeps_unknown_liability(tmp_path, kwargs):
    cfg, root, reference = fixture_run(tmp_path, **kwargs)
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["status"] == "unknown" and receipt["unknown_total"] is True
    assert dec(receipt["settlement_usd"]) == dec(5)


def test_receipt_survives_ledger_write_failure_and_retries_without_repricing(tmp_path):
    cfg, root, reference = fixture_run(tmp_path)
    store = Store(cfg.database)
    store.db.execute(
        "CREATE TRIGGER refuse_close BEFORE UPDATE ON documents "
        "WHEN old.kind='research_spend' BEGIN SELECT RAISE(ABORT,'offline fixture'); END"
    )
    store.db.close()
    with pytest.raises(Exception, match="offline fixture"):
        close_expired_run(cfg, cfg.database, root, reference)
    store = Store(cfg.database)
    assert store.get("spend-key", "research_spend", scope="global")["status"] == "reserved"
    store.db.execute("DROP TRIGGER refuse_close")
    store.db.close()
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert close_expired_run(cfg, cfg.database, root, reference) == receipt
    store = Store(cfg.database)
    assert store.get("spend-key", "research_spend", scope="global")["status"] == "settled"
    store.db.close()


def test_no_pending_operation_settles_only_saved_known_model_cost(tmp_path):
    cfg, root, reference = fixture_run(tmp_path)
    path = run_directory(root, reference["run_key"]) / "operations.sqlite"
    binding = load_manifest(root, reference, cfg, "experiment-one")["binding"]
    with Journal(path, binding).lease() as journal:
        evidence = journal.accounting_evidence()
        journal.complete(
            evidence["key"], evidence["request_hash"], "saved model result", evidence["prestate"]
        )
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["operation_key"] is None and receipt["usage"] is None
    assert dec(receipt["settlement_usd"]) == dec("0.00001")


def test_expired_reservation_before_first_checkpoint_proves_zero_only_from_empty_journal(tmp_path):
    cfg, root, reference = fixture_run(tmp_path, pending_kind="none")
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["status"] == "settled"
    assert dec(receipt["settlement_usd"]) == dec(0)
    assert close_expired_run(cfg, cfg.database, root, reference) == receipt


def test_missing_snapshot_with_operation_history_retains_unknown_allowance(tmp_path):
    cfg, root, reference = fixture_run(tmp_path, pending_kind="none")
    path = run_directory(root, reference["run_key"]) / "operations.sqlite"
    binding = load_manifest(root, reference, cfg, "experiment-one")["binding"]
    with Journal(path, binding).lease() as journal:
        journal._connection().execute(
            "INSERT INTO operations(key,request_hash,status,state_type,state) "
            "VALUES ('old','hash','complete','json',X'7B7D')"
        )
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["status"] == "unknown"
    assert dec(receipt["settlement_usd"]) == dec(5)


def test_publication_without_snapshot_cannot_prove_zero_usage(tmp_path):
    cfg, root, reference = fixture_run(tmp_path, pending_kind="none")
    store = Store(cfg.database)
    store.put(reference["run_key"], {"decision": {"symbol": "ACME"}}, "research_result")
    store.db.close()
    with pytest.raises(AccountingEvidenceError, match="lacks frozen commit state"):
        close_expired_run(cfg, cfg.database, root, reference)
    store = Store(cfg.database)
    assert store.get("spend-key", "research_spend", scope="global")["status"] == "reserved"
    store.db.close()


@pytest.mark.parametrize("kind", ["sec_facts", "news_page_0", "x_discussion"])
def test_pending_source_request_keeps_unknown_full_allowance(tmp_path, kind):
    cfg, root, reference = fixture_run(tmp_path, pending_kind=kind)
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["status"] == "unknown"
    assert dec(receipt["settlement_usd"]) == dec(5)


def test_partial_final_publication_reconciles_but_cannot_gain_commit_receipt(tmp_path):
    from trader.research_stages import ResearchStages
    from trader.research_workflow import StageConfigurationError

    cfg, root, reference = fixture_run(tmp_path)
    path = run_directory(root, reference["run_key"]) / "operations.sqlite"
    manifest = load_manifest(root, reference, cfg, "experiment-one")
    with Journal(path, manifest["binding"]).lease() as journal:
        evidence = journal.accounting_evidence()
        journal.complete(
            evidence["key"], evidence["request_hash"], "saved model result", evidence["prestate"]
        )
        result = {"decision": {"symbol": "ACME"}, "research_completed_at": now().isoformat()}
        audit = {"symbol": "ACME", "estimated_usd": dec("0.00001")}
        state = evidence["prestate"]
        state["desk"]["continuation"] = {
            "commit_hashes": {"result": fingerprint(result), "audit": fingerprint(audit)}
        }
        journal.save_state(state)
    store = Store(cfg.database)
    store.put(reference["run_key"], result, "research_result")
    store.put(reference["run_key"], audit, "research_audit")
    store.db.close()
    receipt = close_expired_run(cfg, cfg.database, root, reference)
    assert receipt["publication"] == "partial_final_verified"
    assert receipt["status"] == "settled"
    engine = ResearchStages(
        cfg,
        cfg.database,
        lambda store: object(),
        manifest["context"],
        {},
        run_directory(root, reference["run_key"]),
        manifest["binding"],
    )
    with pytest.raises(StageConfigurationError, match="Accounting-closed"):
        engine({**reference, "stage": "commit"})
