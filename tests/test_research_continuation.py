import copy
import sqlite3
from datetime import timedelta

import pytest
from test_research_team import Evidence

from trader.config import Security, Settings
from trader.models import dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_continuation import (
    export_parent_bundle,
    import_into_desk,
    validate_bundle,
    validate_parent_state,
)
from trader.research_journal import Journal
from trader.research_manifest import run_directory
from trader.research_team import Budget, ResearchDesk
from trader.store import Store


@pytest.fixture
def parent(tmp_path):
    cfg = Settings(universe={"ACME": Security(cik="1", sector="Tech")})
    store = Store(tmp_path / "ledger.sqlite")
    store.experiment = {"id": "experiment", "account_id": "account"}
    context = {
        "run_key": "parent",
        "symbol": "ACME",
        "research_only": True,
        "current_timestamp": now().isoformat(),
        "portfolio": {
            "snapshot": {"account": {"id": "account", "cash": "100"}, "positions": [], "orders": []}
        },
        "runtime_control": {"account_id": "account", "stop_control": {"sha256": "pause"}},
    }
    binding = {
        "run_key": "parent",
        "experiment_id": store.scope,
        "config_sha256": "a" * 64,
        "code_sha256": "b" * 64,
        "context_sha256": fingerprint(context),
    }
    ref = {"run_key": "parent", "manifest_hash": fingerprint(binding)}
    root = tmp_path / "runs"
    directory = run_directory(root, "parent")
    directory.mkdir(parents=True, mode=0o700)
    (directory / "manifest.json").write_text(dumps({"binding": binding, "context": context}))
    provider = Evidence()
    desk = ResearchDesk(cfg, provider, context, provider.get("ACME"), Budget(cfg), None)
    desk.reports = {
        "business": {
            "business_quality": "conditional",
            "growth_evidence": "filing",
            "financial_health": "filing",
            "contrary_evidence": [],
            "evidence_ids": ["fixture-sec"],
            "missing_information": [],
        },
        "catalysts": {
            "developments": [],
            "catalyst_timeline": [],
            "why_now": "review",
            "thesis_development": "conditional",
            "monitoring_triggers": [],
            "requires_deeper_review": True,
            "evidence_ids": ["fixture-sec"],
            "missing_information": [],
        },
        "valuation": {"old": "not accepted"},
        "adversarial": {"old": "not accepted"},
    }
    desk.report_times = {role: now().isoformat() for role in desk.reports}
    desk.report_dependencies = {role: {} for role in desk.reports}
    desk.correction = {"reason": "Repair financing", "reports": copy.deepcopy(desk.reports)}
    tools = {tool.name: tool for tool in desk.tools()}
    tools["value_scenarios"].invoke(
        {
            "cash_flow_per_share": "5",
            "financial_source_id": "fixture-sec",
            "financial_period_end": "2025-12-31",
            "growth_rates": ["0", "0", "0"],
            "discount_rate": ".1",
            "terminal_multiples": ["0", "0", "0"],
            "years": 1,
        }
    )
    saved = desk.checkpoint()
    receipt = {
        "status": "settled",
        "unknown_total": False,
        "outcome": "aborted_accounted",
        "run_key": "parent",
        "manifest_hash": ref["manifest_hash"],
        "spend_key": "spend",
        "known_usd": "0",
        "settlement_usd": "0",
        "operation_key": None,
        "closed_at": now().isoformat(),
    }
    journal = Journal(directory / "operations.sqlite", binding)
    with journal.lease():
        journal.save_state(saved)
        journal.record_accounting_receipt(receipt)
    spend = {
        "status": "settled",
        "unknown_total": False,
        "outcome": "aborted_accounted",
        "run_key": "parent",
        "experiment_id": store.scope,
        "usd": "0",
        "config_sha256": binding["config_sha256"],
        "accounting_receipt_sha256": fingerprint(receipt),
    }
    store.put("spend", spend, "research_spend", scope="global")
    store.put("parent", {"reference": ref, "status": "canceled"}, "research_workflow_terminal")
    for kind in ("research_workflow", "research_workflow_submission"):
        store.put("parent", ref, kind)
    bundle = export_parent_bundle(root, ref, store)
    child = {
        **copy.deepcopy(context),
        "run_key": "child",
        "current_timestamp": now().isoformat(),
        "continuation": {"parent": ref, "bundle_sha256": fingerprint(bundle)},
    }
    return cfg, store, root, bundle, child


def validate(case, **kwargs):
    cfg, store, _root, bundle, child = case
    return validate_bundle(bundle, child, fingerprint(bundle), experiment_id=store.scope, cfg=cfg, **kwargs)


def test_export_is_readonly_and_revalidates_originals(parent):
    _cfg, store, root, bundle, _child = parent
    directory = run_directory(root, "parent")
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    ledger = list(store.db.iterdump())
    validate(parent)
    validate_parent_state(store, root, bundle)
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before
    assert list(store.db.iterdump()) == ledger
    assert "provider_dispatches" in bundle["journal_tables_sha256"]
    assert "provider_dispatches" not in bundle


@pytest.mark.parametrize(
    "mutation", ["pending", "missing_response", "terminal", "unknown", "receipt", "journal", "snapshot"]
)
def test_authoritative_parent_drift_fails(parent, mutation):
    _cfg, store, root, bundle, _child = parent
    path = run_directory(root, "parent") / "operations.sqlite"
    if mutation == "terminal":
        store.put(
            "parent", {"reference": bundle["parent"], "status": "running"}, "research_workflow_terminal"
        )
    elif mutation == "unknown":
        body = store.get("spend", "research_spend", scope="global")
        store.put("spend", {**body, "unknown_total": True}, "research_spend", scope="global")
    else:
        with sqlite3.connect(path) as db:
            if mutation == "pending":
                db.execute(
                    "INSERT INTO operations(key,request_hash,status,state_type,state) VALUES('x','x','pending','json',X'00')"
                )
            elif mutation == "missing_response":
                db.execute(
                    "INSERT INTO provider_exchanges(client_request_id,request_type,request) VALUES('x','json',X'00')"
                )
            elif mutation == "receipt":
                db.execute("UPDATE accounting_close SET receipt='{}'")
            elif mutation == "journal":
                db.execute("CREATE TABLE altered(id INTEGER)")
            else:
                db.execute("UPDATE snapshot SET payload=X'00'")
    with pytest.raises((ValueError, TypeError, KeyError)):
        validate_parent_state(store, root, bundle)


@pytest.mark.parametrize(
    "field", ["run_key", "symbol", "portfolio", "runtime_control", "research_only", "current_timestamp"]
)
def test_child_context_cannot_reidentify_evidence(parent, field):
    child = parent[4]
    child[field] = {
        "run_key": "parent",
        "symbol": "OTHER",
        "portfolio": {},
        "runtime_control": {},
        "research_only": False,
        "current_timestamp": (now() + timedelta(days=1)).isoformat(),
    }[field]
    with pytest.raises(ValueError):
        validate(parent)


def test_hash_and_stale_report_refused(parent):
    cfg, store, _root, bundle, child = parent
    with pytest.raises(ValueError, match="hash"):
        validate_bundle(bundle, child, "0" * 64, experiment_id=store.scope, cfg=cfg)
    with pytest.raises(ValueError, match="expired"):
        validate(parent, current=now() + timedelta(hours=cfg.research_cache_hours + 1))


@pytest.mark.parametrize("mutation", ["account", "cash", "position", "orders"])
def test_live_broker_changes_refused(parent, mutation):
    live = copy.deepcopy(parent[4]["portfolio"]["snapshot"])
    if mutation == "account":
        live["account"]["id"] = "different"
    elif mutation == "cash":
        live["account"]["cash"] = "99"
    elif mutation == "position":
        live["positions"] = [{"symbol": "ACME", "qty": "1"}]
    else:
        live["orders"] = [{"id": "pending"}]
    with pytest.raises(ValueError):
        validate(parent, current_snapshot=live)


def test_import_does_not_transplant_budget_reports_or_dispatches(parent, monkeypatch):
    cfg, _store, _root, bundle, child = parent
    validate(parent, current_snapshot=child["portfolio"]["snapshot"])
    provider = Evidence()
    desk = ResearchDesk(cfg, provider, child, provider.get("ACME"), Budget(cfg), None)
    calls = []
    original = desk.verify_calculations

    def replay(calculations):
        calls.append(copy.deepcopy(calculations))
        return original(calculations)

    monkeypatch.setattr(desk, "verify_calculations", replay)
    result = import_into_desk(desk, bundle)
    assert calls == [bundle["snapshot"]["desk"]["calculations"]]
    assert set(desk.reports) == {"business", "catalysts"}
    assert desk.correction is None and desk.agent_prompts == {}
    assert desk.budget.models == desk.budget.tools == desk.budget.reserved == 0
    assert result["context"] == child
    assert result["desk"]["continuation"]["parent_lineage"]["correction_reason"] == "Repair financing"
    assert desk.sources == bundle["snapshot"]["desk"]["sources"]
    with pytest.raises(ValueError, match="fresh"):
        import_into_desk(desk, bundle)


def test_failed_calculation_replay_restores_fresh_child(parent, monkeypatch):
    cfg, _store, _root, bundle, child = parent
    provider = Evidence()
    desk = ResearchDesk(cfg, provider, child, provider.get("ACME"), Budget(cfg), None)

    def fail(_):
        raise ValueError("saved arithmetic changed")

    monkeypatch.setattr(desk, "verify_calculations", fail)
    with pytest.raises(ValueError, match="arithmetic"):
        import_into_desk(desk, bundle)
    assert not desk.reports and not desk.calculations and not desk.continuation
    assert desk.context == child


def test_historical_filled_orders_are_not_open_authority(parent):
    live = copy.deepcopy(parent[4]["portfolio"]["snapshot"])
    live["orders"] = [{"id": "old-paper-fill", "status": "filled"}]
    validate(parent, current_snapshot=live)


def test_tampered_calculation_rejected_by_actual_replay(parent):
    cfg, _store, _root, bundle, child = parent
    corrupted = copy.deepcopy(bundle)
    calculation = next(iter(corrupted["snapshot"]["desk"]["calculations"].values()))
    calculation["base_value"] = "999"
    provider = Evidence()
    desk = ResearchDesk(cfg, provider, child, provider.get("ACME"), Budget(cfg), None)
    with pytest.raises(ValueError, match="provenance|values"):
        import_into_desk(desk, corrupted)
    assert not desk.calculations and not desk.reports


@pytest.mark.parametrize("mutation", ["vendor", "company", "future_source", "future_report", "stale_report"])
def test_rehashed_bundle_cannot_evade_semantic_checks(parent, mutation):
    cfg, store, _root, bundle, child = parent
    data = bundle["snapshot"]["desk"]
    if mutation == "vendor":
        data["sources"]["fixture-sec"]["content"]["requested_symbol"] = "OTHER"
    elif mutation == "company":
        data["companies"]["ACME"]["symbol"] = "OTHER"
    elif mutation == "future_source":
        data["sources"]["fixture-sec"]["published_at"] = (now() + timedelta(days=1)).isoformat()
    else:
        data["report_times"]["business"] = (
            now() + timedelta(days=1)
            if mutation == "future_report"
            else now() - timedelta(hours=cfg.research_cache_hours + 1)
        ).isoformat()
    bundle["artifact_sha256"]["snapshot"] = fingerprint(bundle["snapshot"])
    child["continuation"]["bundle_sha256"] = fingerprint(bundle)
    with pytest.raises(ValueError):
        validate_bundle(bundle, child, fingerprint(bundle), experiment_id=store.scope, cfg=cfg)
