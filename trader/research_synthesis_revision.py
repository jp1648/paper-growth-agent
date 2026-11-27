"""Bounded revisions of completed reviewed synthesis; never rewrite prior decisions."""

import copy
import json
import re

from .models import dec, dt, now
from .paper_dispatch import fingerprint
from .research_manifest import run_directory

MAX_ANCESTORS = 4


def completed_accounting(store, ref, manifest, snapshot, key, spend, terminal):
    from .research_continuation import _require

    commit = store.get(f"research_stage_receipt:{ref['run_key']}:commit", "research_stage_receipt")
    result = store.get(ref["run_key"], "research_result")
    audit = store.get(ref["run_key"], "research_audit")
    _require(
        commit
        and commit.get("run_key") == ref["run_key"]
        and commit.get("manifest_hash") == ref["manifest_hash"]
        and commit.get("stage") == "commit"
        and commit.get("receipt", {}).get("status") == "complete"
        and terminal in (None, {"reference": ref, "status": "complete"})
        and isinstance(result, dict)
        and isinstance(audit, dict)
        and result.get("decision", {}).get("symbol") == manifest["context"]["symbol"]
        and audit.get("symbol") == manifest["context"]["symbol"],
        "Exact completed synthesis commit/result required",
    )
    _require(
        spend.get("status") == "settled"
        and spend.get("unknown_total") is False
        and spend.get("durable") is True
        and not spend.get("reconciliation_id")
        and spend.get("outcome") != "aborted_accounted"
        and spend.get("config_sha256") == manifest["binding"]["config_sha256"]
        and dec(spend["usd"]) == dec(audit["estimated_usd"]) == dec(snapshot["budget"]["reserved"])
        and snapshot["desk"]["continuation"].get("commit_hashes")
        == {"result": fingerprint(result), "audit": fingerprint(audit)},
        "Completed synthesis accounting/commit hashes differ",
    )
    # Explicit derived view for shared freshness/cost checks, not an invented
    # accounting_close row. The actual immutable commit/result/audit are retained.
    receipt = {
        "source": "verified_complete_commit",
        "status": "settled",
        "unknown_total": False,
        "run_key": ref["run_key"],
        "manifest_hash": ref["manifest_hash"],
        "spend_key": key,
        "known_usd": str(audit["estimated_usd"]),
        "settlement_usd": str(spend["usd"]),
        "closed_at": commit["recorded_at"],
    }
    return receipt, {"commit_receipt": commit, "result": result, "audit": audit}


def lineage(bundle):
    from .research_continuation import SYNTHESIS_SCHEMA, _require

    seen, nodes = set(), []
    while True:
        identity = bundle["parent"]["run_key"]
        _require(
            identity not in seen and len(nodes) < MAX_ANCESTORS, "Repeated or excessive synthesis ancestry"
        )
        seen.add(identity)
        nodes.append(bundle)
        if bundle.get("schema") != SYNTHESIS_SCHEMA:
            break
        bundle = bundle["ancestor_bundle"]
    return nodes


def validate_revision_artifacts(bundle, current):
    from .research_continuation import SYNTHESIS_SCHEMA, _require, _validate_synthesis_artifacts

    lineage(bundle)
    ancestor = bundle["ancestor_bundle"]
    _require(ancestor.get("schema") == SYNTHESIS_SCHEMA, "Revision requires reviewed synthesis ancestry")
    _validate_synthesis_artifacts(ancestor, current)
    descriptor = bundle["manifest"]["context"].get("continuation")
    expected = {
        "mode": "reviewed_synthesis",
        "parent": ancestor["parent"],
        "bundle_sha256": fingerprint(ancestor),
    }
    if "revision" in ancestor:
        expected["revision_of"] = ancestor["parent"]
    _require(descriptor == expected, "Revision imported ancestry differs")
    data, previous = bundle["snapshot"]["desk"], ancestor["snapshot"]["desk"]
    for field in ("reports", "report_times", "report_dependencies", "sources", "calculations"):
        _require(data[field] == previous[field], "Revision changed inherited research")
    _require(
        bundle["analytical_addendum"] == ancestor["analytical_addendum"]
        and bundle["independent_review"] == ancestor["independent_review"],
        "Revision changed reviewed addendum",
    )
    revision = bundle["revision"]
    _require(
        set(revision) == {"revision_of", "independent_review", "prompt_change_review"}
        and revision["revision_of"] == bundle["parent"],
        "Revision identity differs",
    )
    review, prompt = revision["independent_review"], revision["prompt_change_review"]
    result = bundle["completion"]["result"]
    _require(
        review.get("verdict") == "changes_required_for_complete_decision_acceptance"
        and isinstance(review.get("reviewed_by"), str)
        and review["reviewed_by"].strip()
        and review.get("result_sha256") == fingerprint(result)
        and review.get("decision_sha256") == fingerprint(result["decision"])
        and dt(bundle["receipt"]["closed_at"]) <= dt(review["reviewed_at"]) <= current,
        "Independent changes-required review must bind completed decision",
    )
    old_prompt = data["agent_prompts"].get("initial:research_lead")
    from .research_continuation import _hash_bytes

    _require(isinstance(old_prompt, str) and old_prompt, "Original effective synthesis prompt missing")
    fields = {
        "verdict",
        "reviewed_by",
        "reviewed_at",
        "parent",
        "result_sha256",
        "independent_review_sha256",
        "old_effective_prompt_sha256",
        "new_effective_prompt_sha256",
        "change_summary",
        "execution_authorized",
    }
    _require(
        set(prompt) == fields
        and prompt["verdict"] == "approved_effective_prompt_change"
        and isinstance(prompt["reviewed_by"], str)
        and prompt["reviewed_by"].strip()
        and prompt["parent"] == bundle["parent"]
        and prompt["result_sha256"] == fingerprint(result)
        and prompt["independent_review_sha256"] == fingerprint(review)
        and prompt["old_effective_prompt_sha256"] == _hash_bytes(old_prompt.encode())
        and isinstance(prompt["new_effective_prompt_sha256"], str)
        and re.fullmatch("[0-9a-f]{64}", prompt["new_effective_prompt_sha256"])
        and prompt["new_effective_prompt_sha256"] != prompt["old_effective_prompt_sha256"]
        and isinstance(prompt["change_summary"], str)
        and prompt["change_summary"].strip()
        and dt(review["reviewed_at"]) <= dt(prompt["reviewed_at"]) <= current
        and prompt["execution_authorized"] is False,
        "Independently reviewed effective prompt change required",
    )


def export_revision_bundle(root, reference, store, revision, *, _seen=()):
    from .research_continuation import (
        SYNTHESIS_SCHEMA,
        _export_parent_bundle,
        _read,
        _require,
        _verify_ancestor_claim,
        export_synthesis_bundle,
    )

    _require(
        reference["run_key"] not in _seen and len(_seen) < MAX_ANCESTORS,
        "Repeated or excessive synthesis ancestry",
    )
    bundle = _export_parent_bundle(root, reference, store, allow_continuation=True, allow_completed=True)
    descriptor = bundle["manifest"]["context"].get("continuation", {})
    _require(
        descriptor.get("mode") == "reviewed_synthesis", "Completed ordinary research is not revision eligible"
    )
    ancestor = json.loads(_read(run_directory(root, reference["run_key"]) / "continuation.json"))
    if "revision" in ancestor:
        actual = export_revision_bundle(
            root, ancestor["parent"], store, ancestor["revision"], _seen=(*_seen, reference["run_key"])
        )
    else:
        actual = export_synthesis_bundle(
            root, ancestor["parent"], store, ancestor["analytical_addendum"], ancestor["independent_review"]
        )
    _require(
        actual == ancestor
        and descriptor.get("bundle_sha256") == fingerprint(ancestor)
        and descriptor.get("parent") == ancestor["parent"],
        "Revision original imported bundle changed",
    )
    authorization, claim = _verify_ancestor_claim(store, reference, bundle, ancestor)
    bundle.update(
        schema=SYNTHESIS_SCHEMA,
        ancestor_bundle=ancestor,
        ancestor_authorization=authorization,
        ancestor_claim=claim,
        analytical_addendum=copy.deepcopy(ancestor["analytical_addendum"]),
        independent_review=copy.deepcopy(ancestor["independent_review"]),
        revision=copy.deepcopy(revision),
    )
    validate_revision_artifacts(bundle, now())
    bundle["artifact_sha256"] = {
        k: fingerprint(v) for k, v in bundle.items() if k not in {"schema", "parent", "artifact_sha256"}
    }
    return bundle
