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


def fixture_run(tmp_path, *, usage=None, wire_model=None, with_response=True, prior_known=True):
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
    op_key = dumps(["initial", "model", "valuation", "original-node"])
    journal = Journal(run_directory(root, context["run_key"]) / "operations.sqlite", binding)
    with journal.lease():
        journal.begin(op_key, "a" * 64, {"budget": budget, "desk": {}})
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
