"""Compose real continuation provenance, authority, import and staged model execution."""

import copy
from datetime import timedelta

import pytest
from test_research_continuation import parent  # noqa: F401
from test_research_team import Evidence, TeamModel

from trader.models import dec, dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_continuation import export_parent_bundle
from trader.research_manifest import run_directory, runtime_fingerprint
from trader.research_stages import ResearchStages
from trader.research_workflow import StageBudgetError


class ImportedEvidence(Evidence):
    def get(self, *args, **kwargs):
        raise AssertionError("Continuation must not repeat company acquisition")


@pytest.fixture
def composed(parent, monkeypatch):  # noqa: F811
    cfg, store, root, bundle, child = parent
    cfg = cfg.model_copy(
        update={
            "database": str(root.parent / "ledger.sqlite"),
            "input_cost_per_million": dec("1"),
            "output_cost_per_million": dec("1"),
        }
    )
    store.put("active", store.experiment, scope="global")
    store.put("snapshot", copy.deepcopy(child["portfolio"]["snapshot"]))
    directory = run_directory(root, child["run_key"])
    directory.mkdir(mode=0o700)
    path = directory / "continuation.json"
    path.write_text(dumps(bundle))
    path.chmod(0o600)
    approval = {
        "parent": bundle["parent"],
        "bundle_sha256": fingerprint(bundle),
        "child_run_key": child["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(child),
        "code_sha256": runtime_fingerprint(),
        "max_cost_usd": str(cfg.research_max_cost_usd),
        "max_combined_cost_usd": str(cfg.research_max_cost_usd),
        "approved_by": "offline-reviewer",
        "approved_at": now().isoformat(),
        "expires_at": (now() + timedelta(hours=1)).isoformat(),
    }
    store.put(child["run_key"], approval, "research_continuation_authorization")
    decision = {
        "symbol": "ACME",
        "action": "WATCH",
        "quantity": "0",
        "limit_price": "0",
        "business_attraction": "Uncertain",
        "price_attraction": "Uncertain",
        "why_now": "Await evidence",
        "catalysts": ["Review"],
        "thesis_development": "Review later",
        "holding_period_days": 0,
        "next_review": (now() + timedelta(days=1)).isoformat(),
        "add_if": "Evidence improves",
        "reduce_if": "Risk grows",
        "exit_if": "Thesis fails",
        "exit_reason": "none",
        "evidence_ids": ["fixture-sec"],
        "assumptions": [],
        "expected_outcome": "Remain on watch",
        "confidence": "0.5",
    }
    model = TeamModel(decision=decision)
    binding = {
        "run_key": child["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "code_sha256": runtime_fingerprint(),
        "context_sha256": fingerprint(child),
    }
    engine = ResearchStages(cfg, cfg.database, lambda _: ImportedEvidence(), child, model, directory, binding)
    monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *a, **k: None)
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *a, **k: None)
    parent_dir = run_directory(root, "parent")
    original = {p.name: p.read_bytes() for p in parent_dir.iterdir() if p.is_file()}
    return store, root, bundle, engine, model, original, approval


def invoke(engine, stage):
    return engine({"run_key": "child", "manifest_hash": engine.manifest_hash, "stage": stage})


def child_spends(store):
    return store.db.execute(
        "SELECT key,body FROM documents WHERE kind='research_spend' "
        "AND json_extract(body,'$.run_key')='child'"
    ).fetchall()


def test_real_export_authority_import_and_fresh_team_finish(composed):
    store, root, bundle, engine, model, original, approval = composed
    for stage in ("acquisition", "import", "valuation", "adversarial", "lead", "commit"):
        receipt = invoke(engine, stage)
        assert invoke(engine, stage) == receipt  # committed stage replay makes no new calls
    assert set(model.calls) == {"Valuation", "Critique", "Decision"}
    audit = store.get("child", "research_audit")
    assert audit["model_calls"] == len(model.calls)
    assert set(audit["reports"]) == {"business", "catalysts", "valuation", "adversarial"}
    assert audit["reports"]["business"] == bundle["snapshot"]["desk"]["reports"]["business"]
    assert audit["reports"]["valuation"] != bundle["snapshot"]["desk"]["reports"]["valuation"]
    assert store.get("child", "research_result")["decision"]["action"] == "WATCH"
    assert len(child_spends(store)) == 1
    assert export_parent_bundle(root, bundle["parent"], store) == bundle
    assert {
        p.name: p.read_bytes() for p in run_directory(root, "parent").iterdir() if p.is_file()
    } == original
    assert store.get("child", "research_continuation_authorization") == approval


def test_funding_refusal_after_claim_replays_without_new_authority_or_reservation(composed, monkeypatch):
    store, root, bundle, engine, model, original, approval = composed
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *a, **k: "funding unavailable")
    for _ in range(2):
        with pytest.raises(StageBudgetError, match="funding unavailable"):
            invoke(engine, "acquisition")
    claim = copy.deepcopy(store.get("parent", "research_continuation_consumed"))
    assert claim is not None
    assert child_spends(store) == []
    assert model.calls == []
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *a, **k: None)
    assert invoke(engine, "acquisition")["next_stage"] == "import"
    assert invoke(engine, "acquisition")["next_stage"] == "import"
    assert invoke(engine, "import")["next_stage"] == "valuation"
    assert len(child_spends(store)) == 1
    assert store.get("parent", "research_continuation_consumed") == claim
    assert store.get("child", "research_continuation_authorization") == approval
    assert len(store.events("research_continuation_consumed")) == 1
    assert export_parent_bundle(root, bundle["parent"], store) == bundle
    assert {
        p.name: p.read_bytes() for p in run_directory(root, "parent").iterdir() if p.is_file()
    } == original
