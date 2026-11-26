"""Real two-generation provenance, isolated synthesis import and safety regressions."""

import copy
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from test_research_continuation import parent  # noqa: F401
from test_research_continuation_integration import composed, invoke  # noqa: F401
from test_research_team import Evidence, TeamModel

from trader.models import dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_continuation import (
    accounted_ancestry_cost,
    export_parent_bundle,
    export_synthesis_bundle,
    import_into_desk,
    validate_bundle,
    validate_parent_state,
)
from trader.research_journal import Journal
from trader.research_manifest import run_directory, runtime_fingerprint
from trader.research_stages import ResearchStages
from trader.research_team import Budget, ResearchDesk
from trader.research_workflow import StageConfigurationError


@pytest.fixture
def synthesis(composed):  # noqa: F811
    store, root, _original, engine, model, _, _ = composed
    model.gap = True  # retain a real material-gap flag through fresh synthesis
    for stage in ("acquisition", "import", "valuation", "adversarial"):
        invoke(engine, stage)
    directory = run_directory(root, "child")
    (directory / "manifest.json").write_text(dumps({"binding": engine.binding, "context": engine.context}))
    journal = Journal(directory / "operations.sqlite", engine.binding)
    with journal.lease():
        # TeamModel bypasses HTTP; record explicit synthetic exchanges for its
        # completed dispatches so real export's unknown-wire guard stays intact.
        import sqlite3

        with sqlite3.connect(directory / "operations.sqlite") as db:
            dispatches = db.execute(
                "SELECT key,request_hash,client_request_id FROM provider_dispatches"
            ).fetchall()
        for op, request_hash, identifier in dispatches:
            journal.exchange(
                op, request_hash, identifier, {"offline_fixture": True}, lambda: {"offline_response": True}
            )
        snapshot = journal.load_state()
        cost = str(snapshot["budget"]["reserved"])
        key, raw = store.db.execute(
            "SELECT key,body FROM documents WHERE kind='research_spend' AND json_extract(body,'$.run_key')='child'"
        ).fetchone()
        spend = json.loads(raw)
        ref = {"run_key": "child", "manifest_hash": engine.manifest_hash}
        receipt = {
            "status": "settled",
            "unknown_total": False,
            "outcome": "aborted_accounted",
            "run_key": "child",
            "manifest_hash": ref["manifest_hash"],
            "spend_key": key,
            "known_usd": cost,
            "settlement_usd": cost,
            "operation_key": None,
            "closed_at": now().isoformat(),
        }
        journal.record_accounting_receipt(receipt)
    spend.update(
        status="settled",
        unknown_total=False,
        outcome="aborted_accounted",
        usd=cost,
        accounting_receipt_sha256=fingerprint(receipt),
    )
    store.put(key, spend, "research_spend", scope="global")
    store.put("child", {"reference": ref, "status": "canceled"}, "research_workflow_terminal")
    for kind in ("research_workflow", "research_workflow_submission"):
        store.put("child", ref, kind)
    data = snapshot["desk"]
    addendum = {
        "text": "Conditional model synthesis; source facts unchanged; no execution authority.",
        "created_at": now().isoformat(),
        "source_ids": ["fixture-sec"],
        "calculation_ids": list(data["calculations"]),
    }
    review = {
        "verdict": "approved_conditional_synthesis",
        "reviewed_by": "independent offline reviewer",
        "reviewed_at": now().isoformat(),
        "parent": ref,
        "addendum_sha256": fingerprint(addendum),
        "snapshot_sha256": fingerprint(snapshot),
        "source_sha256": {i: fingerprint(data["sources"][i]) for i in addendum["source_ids"]},
        "calculation_sha256": {i: fingerprint(data["calculations"][i]) for i in addendum["calculation_ids"]},
        "execution_authorized": False,
    }
    bundle = export_synthesis_bundle(root, ref, store, addendum, review)
    context = {
        **copy.deepcopy(engine.context),
        "run_key": "synthesis-child",
        "current_timestamp": now().isoformat(),
        "continuation": {"mode": "reviewed_synthesis", "parent": ref, "bundle_sha256": fingerprint(bundle)},
    }
    return store, root, engine, bundle, context


def test_export_import_preserves_originals_and_fresh_budget(synthesis):
    store, root, engine, bundle, context = synthesis
    with pytest.raises(ValueError, match="chains"):
        export_parent_bundle(root, bundle["parent"], store)
    validate_bundle(
        bundle,
        context,
        fingerprint(bundle),
        experiment_id=store.scope,
        cfg=engine.cfg,
        current_snapshot=store.get("snapshot"),
    )
    validate_parent_state(store, root, bundle)
    desk = ResearchDesk(
        engine.cfg,
        Evidence(),
        context,
        bundle["snapshot"]["desk"]["companies"]["ACME"],
        Budget(engine.cfg),
        None,
    )
    before = copy.deepcopy(bundle)
    import_into_desk(desk, bundle)
    assert desk.reports == bundle["snapshot"]["desk"]["reports"]
    assert desk.report_times == bundle["snapshot"]["desk"]["report_times"]
    assert desk.budget.models == desk.budget.tools == desk.budget.reserved == 0
    assert desk.correction is None and "final_result" not in desk.continuation
    assert desk.continuation["reviewed_synthesis"]["addendum"] == bundle["analytical_addendum"]
    assert bundle == before
    assert accounted_ancestry_cost(bundle) == sum(
        Decimal(b["spend"]["body"]["usd"]) for b in (bundle, bundle["ancestor_bundle"])
    )


@pytest.mark.parametrize(
    "change",
    [
        "claim",
        "ancestor_spend",
        "ancestor_terminal",
        "review",
        "sources",
        "snapshot",
        "third_generation",
        "dependencies",
        "addendum_changed",
        "pending_wire",
        "duplicate_claim",
    ],
)
def test_synthesis_refuses_changed_history_or_unreviewed_reasoning(synthesis, change):
    store, root, engine, bundle, _context = synthesis
    addendum = copy.deepcopy(bundle["analytical_addendum"])
    review = copy.deepcopy(bundle["independent_review"])
    if change == "claim":
        store.put("parent", {"claim": {}}, "research_continuation_consumed")
    elif change == "ancestor_spend":
        v = copy.deepcopy(bundle["ancestor_bundle"]["spend"]["body"])
        v["usd"] = "999"
        store.put("spend", v, "research_spend", scope="global")
    elif change == "ancestor_terminal":
        store.put(
            "parent",
            {"reference": bundle["ancestor_bundle"]["parent"], "status": "running"},
            "research_workflow_terminal",
        )
    elif change == "review":
        review["execution_authorized"] = True
    elif change == "sources":
        addendum["source_ids"] = ["missing"]
    elif change == "addendum_changed":
        addendum["text"] += " Changed after review"
    elif change == "pending_wire":
        import sqlite3

        with sqlite3.connect(run_directory(root, "child") / "operations.sqlite") as db:
            db.execute(
                "UPDATE provider_exchanges SET response=NULL WHERE client_request_id=(SELECT client_request_id FROM provider_exchanges LIMIT 1)"
            )
    elif change == "duplicate_claim":
        store.log("research_continuation_consumed", bundle["ancestor_claim"])
    elif change == "snapshot":
        review["snapshot_sha256"] = "0" * 64
    else:
        directory = run_directory(root, "child")
        if change == "third_generation":
            m = json.loads((directory / "manifest.json").read_text())
            m["context"]["continuation"]["mode"] = "reviewed_synthesis"
            (directory / "manifest.json").write_text(dumps(m))
        else:
            j = Journal(directory / "operations.sqlite", engine.binding)
            with j.lease():
                s = j.load_state()
                s["desk"]["report_dependencies"]["adversarial"] = {}
                j.save_state(s)
    with pytest.raises((ValueError, KeyError)):
        export_synthesis_bundle(root, bundle["parent"], store, addendum, review)


def test_real_synthesis_stage_only_calls_lead_and_keeps_original_reports(synthesis):
    store, root, engine, bundle, context = synthesis
    cfg = engine.cfg
    directory = run_directory(root, context["run_key"])
    directory.mkdir(mode=0o700)
    p = directory / "continuation.json"
    p.write_text(dumps(bundle))
    p.chmod(0o600)
    approval = {
        "parent": bundle["parent"],
        "bundle_sha256": fingerprint(bundle),
        "child_run_key": context["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
        "max_cost_usd": str(cfg.research_max_cost_usd),
        "max_combined_cost_usd": str(accounted_ancestry_cost(bundle) + cfg.research_max_cost_usd),
        "approved_by": "reviewer",
        "approved_at": now().isoformat(),
        "expires_at": (now() + timedelta(hours=1)).isoformat(),
    }
    store.put(context["run_key"], approval, "research_continuation_authorization")
    decision = copy.deepcopy(engine.models.decision)
    decision.update(action="BUY", quantity="1", limit_price="10", holding_period_days=30)
    model = TeamModel(decision=decision)
    binding = {
        "run_key": context["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
    }
    run = ResearchStages(cfg, cfg.database, lambda _: Evidence(), context, model, directory, binding)

    def call(stage):
        return run({"run_key": context["run_key"], "manifest_hash": run.manifest_hash, "stage": stage})

    for forbidden in ("import", "valuation", "adversarial", "correction:valuation"):
        with pytest.raises(StageConfigurationError):
            call(forbidden)
    for stage in ("acquisition", "synthesis_import", "lead", "commit"):
        result = call(stage)
        assert call(stage) == result
    assert model.calls == ["Decision"]
    assert store.get(context["run_key"], "research_result")["decision"]["action"] == "WATCH"
    assert (
        store.get(context["run_key"], "research_result")["reviewed_synthesis"]["addendum"]
        == bundle["analytical_addendum"]
    )
    assert (
        store.get(context["run_key"], "research_result")["team_reports"]
        == bundle["snapshot"]["desk"]["reports"]
    )
    validate_parent_state(store, root, bundle)
