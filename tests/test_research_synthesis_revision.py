"""Completed synthesis revisions preserve costs, original decisions and single-child authority."""

import copy
import hashlib
import sqlite3
from datetime import timedelta

import pytest
from test_research_continuation import parent  # noqa: F401
from test_research_continuation_integration import composed, invoke  # noqa: F401
from test_research_synthesis_continuation import (
    synthesis,  # noqa: F401
)
from test_research_synthesis_continuation import (
    test_real_synthesis_stage_only_calls_lead_and_keeps_original_reports as complete_synthesis,
)
from test_research_team import Evidence, TeamModel

from trader.models import dec, dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_continuation import accounted_ancestry_cost, validate_bundle, validate_parent_state
from trader.research_journal import Journal
from trader.research_manifest import run_directory, runtime_fingerprint
from trader.research_stages import ResearchStages
from trader.research_synthesis_revision import export_revision_bundle, lineage
from trader.research_team import ResearchDesk


@pytest.fixture
def completed(synthesis):  # noqa: F811
    complete_synthesis(synthesis)
    store, root, old_engine, _imported, context = synthesis
    binding = {
        "run_key": context["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(old_engine.cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
    }
    ref = {"run_key": context["run_key"], "manifest_hash": fingerprint(binding)}
    directory = run_directory(root, context["run_key"])
    (directory / "manifest.json").write_text(dumps({"binding": binding, "context": context}))
    for kind in ("research_workflow", "research_workflow_submission"):
        store.put(ref["run_key"], ref, kind)
    journal = Journal(directory / "operations.sqlite", binding)
    with journal.lease():
        with sqlite3.connect(directory / "operations.sqlite") as db:
            dispatches = db.execute(
                "SELECT key,request_hash,client_request_id FROM provider_dispatches"
            ).fetchall()
        for key, request_hash, identifier in dispatches:
            journal.exchange(
                key, request_hash, identifier, {"offline_fixture": True}, lambda: {"offline_response": True}
            )
        snapshot = journal.load_state()
    result = store.get(ref["run_key"], "research_result")
    review = {
        "verdict": "changes_required_for_complete_decision_acceptance",
        "reviewed_by": "independent-test",
        "reviewed_at": now().isoformat(),
        "result_sha256": fingerprint(result),
        "decision_sha256": fingerprint(result["decision"]),
        "finding": "Policy comparison needs a supported reversal.",
    }
    old = snapshot["desk"]["agent_prompts"]["initial:research_lead"]
    prompt = {
        "verdict": "approved_effective_prompt_change",
        "reviewed_by": "independent-test",
        "reviewed_at": now().isoformat(),
        "parent": ref,
        "result_sha256": fingerprint(result),
        "independent_review_sha256": fingerprint(review),
        "old_effective_prompt_sha256": hashlib.sha256(old.encode()).hexdigest(),
        "new_effective_prompt_sha256": hashlib.sha256(
            (old + "\nOffline reviewed revision policy change.").encode()
        ).hexdigest(),
        "change_summary": "Require supported action-reversal comparison",
        "execution_authorized": False,
    }
    revision = {"revision_of": ref, "independent_review": review, "prompt_change_review": prompt}
    bundle = export_revision_bundle(root, ref, store, revision)
    return store, root, old_engine, context, bundle


def test_completed_revision_roundtrip_and_original_immutability(completed):
    store, root, engine, old_context, bundle = completed
    validate_parent_state(store, root, bundle)
    context = {
        **copy.deepcopy(old_context),
        "run_key": "revision-child",
        "current_timestamp": now().isoformat(),
        "continuation": {
            "mode": "reviewed_synthesis",
            "parent": bundle["parent"],
            "revision_of": bundle["parent"],
            "bundle_sha256": fingerprint(bundle),
        },
    }
    validate_bundle(
        bundle,
        context,
        fingerprint(bundle),
        experiment_id=store.scope,
        cfg=engine.cfg,
        current_snapshot=store.get("snapshot"),
    )
    assert len(lineage(bundle)) == 3
    assert accounted_ancestry_cost(bundle) == sum(
        dec(node["snapshot"]["budget"]["reserved"]) for node in lineage(bundle)
    )
    assert bundle["receipt"]["source"] == "verified_complete_commit"
    with sqlite3.connect(run_directory(root, bundle["parent"]["run_key"]) / "operations.sqlite") as db:
        assert db.execute("SELECT count(*) FROM accounting_close").fetchone()[0] == 0


@pytest.mark.parametrize(
    "bad",
    ["commit", "result", "unknown", "canceled", "prompt_unchanged", "review", "claim", "ancestor", "pending"],
)
def test_revision_export_refuses_unsafe_completed_history(completed, bad):
    store, root, _engine, _context, bundle = completed
    ref = bundle["parent"]
    revision = copy.deepcopy(bundle["revision"])
    if bad == "commit":
        record = store.get("research_stage_receipt:" + ref["run_key"] + ":commit", "research_stage_receipt")
        record["manifest_hash"] = "f" * 64
        store.put("research_stage_receipt:" + ref["run_key"] + ":commit", record, "research_stage_receipt")
    elif bad == "result":
        result = store.get(ref["run_key"], "research_result")
        result["decision"]["why_now"] = "changed"
        store.put(ref["run_key"], result, "research_result")
    elif bad == "unknown":
        record = copy.deepcopy(bundle["spend"]["body"])
        record["unknown_total"] = True
        store.put(bundle["spend"]["key"], record, "research_spend", scope="global")
    elif bad == "canceled":
        store.put(ref["run_key"], {"reference": ref, "status": "canceled"}, "research_workflow_terminal")
    elif bad == "prompt_unchanged":
        revision["prompt_change_review"]["new_effective_prompt_sha256"] = revision["prompt_change_review"][
            "old_effective_prompt_sha256"
        ]
    elif bad == "review":
        revision["independent_review"]["verdict"] = "approved"
    elif bad == "claim":
        store.put(
            bundle["ancestor_bundle"]["parent"]["run_key"], {"claim": {}}, "research_continuation_consumed"
        )
    elif bad == "ancestor":
        store.put(
            "parent", {"reference": {"run_key": "parent"}, "status": "running"}, "research_workflow_terminal"
        )
    else:
        with sqlite3.connect(run_directory(root, ref["run_key"]) / "operations.sqlite") as db:
            db.execute("UPDATE operations SET status='pending'")
    with pytest.raises(ValueError):
        export_revision_bundle(root, ref, store, revision)


def test_lineage_refuses_cycle_and_excessive_depth(completed):
    bundle = copy.deepcopy(completed[-1])
    bundle["ancestor_bundle"] = bundle
    with pytest.raises(ValueError, match="Repeated"):
        lineage(bundle)
    bundle = copy.deepcopy(completed[-1])
    for i in range(3):
        bundle = {"schema": bundle["schema"], "parent": {"run_key": str(i)}, "ancestor_bundle": bundle}
    with pytest.raises(ValueError, match="excessive"):
        lineage(bundle)


def test_real_revision_single_call_commit_and_replay(completed, monkeypatch):
    store, root, engine, context, bundle = completed
    before = copy.deepcopy(bundle)
    context = {
        **copy.deepcopy(context),
        "run_key": "revised-decision",
        "current_timestamp": now().isoformat(),
        "continuation": {
            "mode": "reviewed_synthesis",
            "parent": bundle["parent"],
            "revision_of": bundle["parent"],
            "bundle_sha256": fingerprint(bundle),
        },
    }
    cfg = engine.cfg
    directory = run_directory(root, context["run_key"])
    directory.mkdir(mode=0o700)
    p = directory / "continuation.json"
    p.write_text(dumps(bundle))
    p.chmod(0o600)
    authority = {
        "parent": bundle["parent"],
        "bundle_sha256": fingerprint(bundle),
        "child_run_key": context["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
        "max_cost_usd": str(cfg.research_max_cost_usd),
        "max_combined_cost_usd": str(accounted_ancestry_cost(bundle) + cfg.research_max_cost_usd),
        "approved_by": "offline-independent",
        "approved_at": now().isoformat(),
        "expires_at": (now() + timedelta(hours=1)).isoformat(),
    }
    store.put(context["run_key"], authority, "research_continuation_authorization")
    original = ResearchDesk.agent_prompt

    def changed(desk, role, prompt, **kwargs):
        value = original(desk, role, prompt, **kwargs)
        return value + "\nOffline reviewed revision policy change." if role == "research_lead" else value

    monkeypatch.setattr(ResearchDesk, "agent_prompt", changed)
    model = TeamModel(decision=copy.deepcopy(engine.models.decision))
    binding = {
        "run_key": context["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
    }
    run = ResearchStages(cfg, cfg.database, lambda _: Evidence(), context, model, directory, binding)
    for stage in ("acquisition", "synthesis_import", "lead", "commit"):
        request = {"run_key": context["run_key"], "manifest_hash": run.manifest_hash, "stage": stage}
        first = run(request)
        assert run(request) == first
    assert model.calls == ["Decision"]
    result = store.get(context["run_key"], "research_result")
    assert result["team_reports"] == bundle["snapshot"]["desk"]["reports"]
    assert (
        result["reviewed_synthesis"]["revision"]["previous_decision"]
        == bundle["completion"]["result"]["decision"]
    )
    validate_parent_state(store, root, bundle)
    assert bundle == before


@pytest.mark.parametrize(
    "bad", ["missing_revision_of", "different_revision_of", "reuse_ancestor_identity", "different_portfolio"]
)
def test_revision_descriptor_and_raw_portfolio_fail_closed(completed, bad):
    store, _root, engine, context, bundle = completed
    context = {
        **copy.deepcopy(context),
        "run_key": "new-revision",
        "current_timestamp": now().isoformat(),
        "continuation": {
            "mode": "reviewed_synthesis",
            "parent": bundle["parent"],
            "revision_of": bundle["parent"],
            "bundle_sha256": fingerprint(bundle),
        },
    }
    if bad == "missing_revision_of":
        context["continuation"].pop("revision_of")
    elif bad == "different_revision_of":
        context["continuation"]["revision_of"] = bundle["ancestor_bundle"]["parent"]
    elif bad == "reuse_ancestor_identity":
        context["run_key"] = "parent"
    else:
        context["portfolio"]["snapshot"]["account"]["cash"] = "0"
    with pytest.raises(ValueError):
        validate_bundle(
            bundle,
            context,
            fingerprint(bundle),
            experiment_id=store.scope,
            cfg=engine.cfg,
            current_snapshot=store.get("snapshot"),
        )
