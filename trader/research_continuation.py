"""Explicit, immutable research imports; never import provider calls or budget authority."""

import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
from datetime import timedelta

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from .models import TERMINAL, dec, dt, dumps, now
from .paper_dispatch import fingerprint
from .research_manifest import run_directory
from .research_workflow import valid_reference

SCHEMA = "research-continuation-v1"
REUSED = ("business", "catalysts")
SYNTHESIS_SCHEMA = "research-synthesis-v1"
ALL_REPORTS = (*REUSED, "valuation", "adversarial")


def _require(value, message):
    if not value:
        raise ValueError(message)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        _require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), "Regular parent file required")
        return handle.read()


def _json(value):
    return json.loads(dumps(value))


def _hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _reconciled_parent(store, reference, key, spend, receipt):
    """Accept only the existing reviewed append-only provider projection schema."""
    audit_id = spend.get("reconciliation_id")
    if not audit_id:
        _require(dec(spend["usd"]) == dec(receipt["settlement_usd"]), "Parent cost differs")
        return None
    audit = store.get(audit_id, "charge_reconciliation", scope="global")
    _require(isinstance(audit, dict), "Parent reconciliation audit missing")
    before, proof, review = audit.get("before", {}), audit.get("proof", {}), audit.get("review", {})
    amount = dec(spend["usd"])
    _require(amount.is_finite() and amount >= 0, "Invalid reconciled parent cost")
    expected = {
        **before,
        "usd": spend["usd"],
        "status": "settled",
        "unknown_total": False,
        "reconciliation_id": audit_id,
        "reconciled_at": spend.get("reconciled_at"),
        "cost_basis": "provider_export_reconciled",
    }
    _require(
        audit.get("id") == audit_id
        and audit.get("run_key") == reference["run_key"]
        and audit.get("after") == spend == expected
        and not before.get("reconciliation_id")
        and before.get("run_key") == reference["run_key"]
        and before.get("experiment_id") == store.scope
        and before.get("status") == "settled"
        and before.get("unknown_total") is False
        and dec(before["usd"]) == dec(receipt["settlement_usd"])
        and before.get("accounting_receipt_sha256") == fingerprint(receipt)
        and audit.get("retry_authorized") is False
        and audit.get("provider_bill_verified") is True,
        "Parent reconciliation projection differs",
    )
    sources = proof.get("source_sha256", {})
    required = {
        "original-ledger.json",
        "accounting-close.json",
        "verified-proof.json",
        "provider-usage.csv",
        "provider-cost.csv",
        "local-provider-day-usage.json",
    }
    review_sources = {
        "independent-provider-proof-review.json",
        "independent-provider-review.json",
    } & sources.keys()
    _require(len(review_sources) == 1, "Provider review evidence is ambiguous or missing")
    provider_review_hash = sources[next(iter(review_sources))]
    _require(
        required <= sources.keys()
        and all(
            isinstance(name, str)
            and "/" not in name
            and isinstance(value, str)
            and re.fullmatch("[0-9a-f]{64}", value)
            for name, value in sources.items()
        ),
        "Provider proof evidence hashes missing",
    )
    _require(
        proof.get("run_key") == reference["run_key"]
        and proof.get("spend_key") == key
        and proof.get("manifest_hash") == reference["manifest_hash"]
        and dec(proof["known_usd"]) == amount
        and review.get("verdict") == "approved_provider_export_reconciliation"
        and isinstance(review.get("reviewed_by"), str)
        and review["reviewed_by"].strip()
        and review.get("run_key") == reference["run_key"]
        and dec(review["approved_projection_usd"]) == amount
        and review.get("retry_authorized") is False
        and isinstance(audit.get("proof_json"), str)
        and len(audit["proof_json"].encode()) <= 65536
        and review.get("proof_sha256") == _hash_bytes(audit["proof_json"].encode())
        and json.loads(audit["proof_json"]) == proof
        and review.get("provider_review_sha256") == provider_review_hash
        and isinstance(review.get("helper_sha256"), str)
        and re.fullmatch("[0-9a-f]{64}", review["helper_sha256"])
        and dt(review["reviewed_at"]) <= dt(spend["reconciled_at"]) <= now(),
        "Parent provider reconciliation lacks exact review",
    )
    rows = store.db.execute(
        "SELECT body FROM journal WHERE scope=? AND kind='provider_charge_reconciled'",
        (store.scope,),
    ).fetchall()
    matches = [json.loads(row[0]) for row in rows if json.loads(row[0]).get("id") == audit_id]
    _require(matches == [audit], "Parent reconciliation journal differs")
    return copy.deepcopy(audit)


def export_parent_bundle(root, reference, store):
    return _export_parent_bundle(root, reference, store, allow_continuation=False)


def _export_parent_bundle(root, reference, store, *, allow_continuation, allow_completed=False):
    """Read a terminal parent's original binding, journal and authoritative accounting.

    No comparison against the child's new runtime/config fingerprint is appropriate.
    Original manifest and journal identities must still agree exactly. Existing locks
    are opened, never created, and the journal connection is strictly read-only.
    """
    _require(valid_reference(reference), "Invalid parent reference")
    directory = run_directory(root, reference["run_key"])
    _require(not directory.is_symlink(), "Symlink parent directory")
    manifest = json.loads(_read(directory / "manifest.json"))
    binding, context = manifest["binding"], manifest["context"]
    _require(
        set(binding) == {"run_key", "experiment_id", "config_sha256", "code_sha256", "context_sha256"}
        and fingerprint(binding) == reference["manifest_hash"]
        and binding["run_key"] == reference["run_key"]
        and binding["experiment_id"] == store.scope
        and binding["context_sha256"] == fingerprint(context)
        and all(re.fullmatch("[0-9a-f]{64}", binding[k]) for k in ("code_sha256", "config_sha256")),
        "Parent manifest binding differs",
    )
    _require(allow_continuation or not context.get("continuation"), "Continuation chains are unsupported")
    _require(
        context.get("research_only") is True
        and context.get("runtime_control", {}).get("account_id") == store.experiment.get("account_id"),
        "Parent account/research scope differs",
    )
    path = directory / "operations.sqlite"
    lock = os.open(str(path) + ".lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        _require(stat.S_ISREG(os.fstat(lock).st_mode), "Regular parent lock required")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _require(not path.is_symlink(), "Symlink parent journal")
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            identity = db.execute("SELECT binding FROM identity WHERE id=1").fetchone()
            _require(identity and json.loads(identity[0]) == binding, "Parent journal binding differs")
            _require(
                db.execute("SELECT count(*) FROM operations WHERE status!='complete'").fetchone()[0] == 0,
                "Pending parent operation",
            )
            _require(
                db.execute("SELECT count(*) FROM provider_exchanges WHERE response IS NULL").fetchone()[0]
                == 0
                and db.execute(
                    "SELECT count(*) FROM provider_dispatches d LEFT JOIN provider_exchanges e ON e.client_request_id=d.client_request_id WHERE e.client_request_id IS NULL"
                ).fetchone()[0]
                == 0,
                "Unresolved parent provider dispatch",
            )
            _require(
                db.execute("SELECT count(*) FROM non_dispatch_resolutions").fetchone()[0] == 0,
                "Parent retry resolution unsupported",
            )
            raw = db.execute("SELECT type,payload FROM snapshot WHERE id=1").fetchone()
            receipt_row = db.execute("SELECT receipt FROM accounting_close WHERE id=1").fetchone()
            _require(raw and (receipt_row or allow_completed), "Parent snapshot/accounting receipt missing")
            serde = JsonPlusSerializer(pickle_fallback=False)
            snapshot = _json(serde.loads_typed(tuple(raw)))
            receipt = json.loads(receipt_row[0]) if receipt_row else None
            tables = {}
            for (table,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall():
                _require(re.fullmatch("[a-z_]+", table), "Unexpected parent table name")
                rows = db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                normalized = [
                    [{"sha256": _hash_bytes(c)} if isinstance(c, bytes) else c for c in row] for row in rows
                ]
                tables[table] = fingerprint(normalized)
        terminal = store.get(reference["run_key"], "research_workflow_terminal")
        if not allow_completed:
            _require(
                terminal == {"reference": reference, "status": "canceled"},
                "Parent is not authoritatively canceled",
            )
        for kind in ("research_workflow", "research_workflow_submission"):
            _require(store.get(reference["run_key"], kind) == reference, "Parent submission identity differs")
        records = store.db.execute(
            "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' AND json_extract(body,'$.run_key')=? AND json_extract(body,'$.experiment_id')=?",
            (reference["run_key"], store.scope),
        ).fetchall()
        _require(len(records) == 1, "Parent spending is not unique")
        key, body = records[0]
        spend = json.loads(body)
        completion = None
        if allow_completed:
            from .research_synthesis_revision import completed_accounting

            _require(receipt is None, "Completed parent has abort-accounting receipt")
            receipt, completion = completed_accounting(
                store, reference, manifest, snapshot, key, spend, terminal
            )
            terminal = {"reference": reference, "status": "complete", "proof": "bound_commit_receipt"}
            reconciliation = None
        else:
            _require(
                spend.get("status") == receipt.get("status") == "settled"
                and spend.get("unknown_total") is False
                and receipt.get("unknown_total") is False
                and spend.get("outcome") == receipt.get("outcome") == "aborted_accounted"
                and spend.get("config_sha256") == binding["config_sha256"]
                and receipt.get("manifest_hash") == reference["manifest_hash"]
                and receipt.get("run_key") == reference["run_key"]
                and receipt.get("spend_key") == key
                and spend.get("accounting_receipt_sha256") == fingerprint(receipt)
                and dec(receipt["settlement_usd"]) == dec(receipt["known_usd"])
                and receipt.get("operation_key") is None,
                "Parent accounting is not a known settlement",
            )
            _require(
                not store.get(reference["run_key"], "research_result"),
                "Completed parent result cannot be imported",
            )
            reconciliation = _reconciled_parent(store, reference, key, spend, receipt)
        budget = snapshot["budget"]
        _require(dec(budget["reserved"]) == dec(receipt["known_usd"]), "Parent snapshot cost differs")
        costs = [v for k, v in budget["events"] if k == "model_cost"]
        _require(
            len(costs) == budget["models"] and all(v.get("estimated_usd") is not None for v in costs),
            "Parent usage incomplete",
        )
        _require(
            sum((dec(v["estimated_usd"]) for v in costs), dec(0)) == dec(receipt["known_usd"]),
            "Parent usage total differs",
        )
        bundle = {
            "schema": SCHEMA,
            "parent": copy.deepcopy(reference),
            "manifest": manifest,
            "snapshot": snapshot,
            "receipt": receipt,
            "spend": {"key": key, "body": spend},
            "terminal": terminal,
            "journal_tables_sha256": tables,
        }
        if reconciliation is not None:
            bundle["reconciliation"] = reconciliation
        if completion is not None:
            bundle["completion"] = completion
        bundle["artifact_sha256"] = {
            k: fingerprint(bundle[k])
            for k in (
                "manifest",
                "snapshot",
                "receipt",
                "spend",
                "terminal",
                "journal_tables_sha256",
                *(("reconciliation",) if "reconciliation" in bundle else ()),
                *(("completion",) if "completion" in bundle else ()),
                *(("revision",) if "revision" in bundle else ()),
                *(
                    (
                        "ancestor_bundle",
                        "ancestor_claim",
                        "ancestor_authorization",
                        "analytical_addendum",
                        "independent_review",
                    )
                    if bundle.get("schema") == SYNTHESIS_SCHEMA
                    else ()
                ),
            )
        }
        return bundle
    finally:
        os.close(lock)


def validate_parent_state(store, root, bundle):
    """Revalidate authoritative originals, including every journal table, without mutation."""
    if bundle.get("schema") == "research-current-refresh-v1":
        from .research_refresh import export_refresh_bundle

        actual = export_refresh_bundle(root, bundle["parent"], store, bundle["evidence_delta"])
        _require(fingerprint(actual) == fingerprint(bundle), "Refresh parent artifacts changed")
        return
    if "revision" in bundle:
        from .research_synthesis_revision import export_revision_bundle

        actual = export_revision_bundle(root, bundle["parent"], store, bundle["revision"])
        _require(fingerprint(actual) == fingerprint(bundle), "Parent artifacts changed")
        return
    _require(
        fingerprint(
            export_synthesis_bundle(
                root, bundle["parent"], store, bundle["analytical_addendum"], bundle["independent_review"]
            )
            if bundle.get("schema") == SYNTHESIS_SCHEMA
            else export_parent_bundle(root, bundle["parent"], store)
        )
        == fingerprint(bundle),
        "Parent artifacts changed",
    )


def validate_bundle(
    bundle, child_context, expected_sha256, *, experiment_id, cfg, current=None, current_snapshot=None
):
    current = current or now()
    if bundle.get("schema") == "research-current-refresh-v1":
        from .research_refresh import validate_refresh_bundle

        return validate_refresh_bundle(
            bundle,
            child_context,
            expected_sha256,
            experiment_id=experiment_id,
            cfg=cfg,
            current=current,
            current_snapshot=current_snapshot,
        )
    synthesis = bundle.get("schema") == SYNTHESIS_SCHEMA
    _require(
        bundle.get("schema") in {SCHEMA, SYNTHESIS_SCHEMA} and fingerprint(bundle) == expected_sha256,
        "Continuation bundle hash differs",
    )
    parent = bundle["manifest"]["context"]
    binding = bundle["manifest"]["binding"]
    _require(
        bundle["artifact_sha256"]
        == {
            k: fingerprint(bundle[k])
            for k in (
                "manifest",
                "snapshot",
                "receipt",
                "spend",
                "terminal",
                "journal_tables_sha256",
                *(("reconciliation",) if "reconciliation" in bundle else ()),
                *(("completion",) if "completion" in bundle else ()),
                *(("revision",) if "revision" in bundle else ()),
                *(
                    (
                        "ancestor_bundle",
                        "ancestor_claim",
                        "ancestor_authorization",
                        "analytical_addendum",
                        "independent_review",
                    )
                    if bundle.get("schema") == SYNTHESIS_SCHEMA
                    else ()
                ),
            )
        },
        "Continuation artifact hash differs",
    )
    _require(
        binding["experiment_id"] == experiment_id
        and fingerprint(binding) == bundle["parent"]["manifest_hash"],
        "Continuation experiment differs",
    )
    _require(
        child_context.get("continuation")
        == {
            "parent": bundle["parent"],
            "bundle_sha256": expected_sha256,
            **({"mode": "reviewed_synthesis"} if synthesis else {}),
            **({"revision_of": bundle["parent"]} if "revision" in bundle else {}),
        },
        "Continuation descriptor differs",
    )
    _require(
        child_context.get("run_key")
        and child_context["run_key"] != parent["run_key"]
        and (synthesis or not parent.get("continuation")),
        "New nonchained child identity required",
    )
    _require(
        parent.get("research_only") is True and child_context.get("research_only") is True,
        "Research-only continuation required",
    )
    _require(
        child_context.get("symbol") == parent.get("symbol") and parent["symbol"] in cfg.research_universe,
        "Continuation issuer differs",
    )
    for field in ("portfolio", "runtime_control"):
        _require(
            child_context.get(field) == parent.get(field) and isinstance(parent.get(field), dict),
            "Continuation " + field + " differs",
        )
    _require(
        dt(parent["current_timestamp"]) <= dt(child_context["current_timestamp"]) <= current,
        "Continuation timestamp differs",
    )
    if current_snapshot is not None:
        prior_snapshot = parent["portfolio"].get("snapshot")
        _require(
            isinstance(prior_snapshot, dict) and isinstance(current_snapshot, dict), "Broker snapshot missing"
        )
        prior_account, live_account = prior_snapshot["account"], current_snapshot["account"]
        account_id = parent["runtime_control"].get("account_id")
        _require(
            account_id and prior_account.get("id") == live_account.get("id") == account_id,
            "Broker account differs",
        )
        _require(dec(prior_account["cash"]) == dec(live_account["cash"]), "Broker cash differs")

        def holdings(snapshot):
            positions = snapshot["positions"]
            values = [(p["symbol"], dec(p["qty"])) for p in positions]
            _require(len({symbol for symbol, _ in values}) == len(values), "Duplicate broker position")
            return sorted(values)

        _require(holdings(prior_snapshot) == holdings(current_snapshot), "Broker positions differ")
        orders = current_snapshot.get("orders", [])
        _require(
            isinstance(orders, list)
            and all(isinstance(order, dict) and order.get("status") in TERMINAL for order in orders),
            "Open or malformed broker orders block continuation",
        )
    saved = bundle["snapshot"]
    _require(
        saved.get("version") == 1
        and saved["context"]["run_key"] == parent["run_key"]
        and saved["context"]["symbol"] == parent["symbol"],
        "Parent desk identity differs",
    )
    _require(
        timedelta(0)
        <= current - dt(bundle["receipt"]["closed_at"])
        < timedelta(hours=cfg.research_cache_hours),
        "Parent continuation expired",
    )
    data = saved["desk"]
    _require(set(data["companies"]) == {parent["symbol"]}, "Inherited company identity differs")
    company = data["companies"][parent["symbol"]]
    _require(company.get("symbol", parent["symbol"]) == parent["symbol"], "Inherited company symbol differs")
    for key, source in data["sources"].items():
        _require(
            source.get("id") == key and source.get("symbol", parent["symbol"]) == parent["symbol"],
            "Inherited source identity differs",
        )
        if isinstance(source.get("content"), dict) and source["content"].get("requested_symbol"):
            _require(
                source["content"]["requested_symbol"] == parent["symbol"], "Inherited vendor issuer differs"
            )
        for field in ("published_at", "retrieved_at"):
            if source.get(field):
                _require(dt(source[field]) <= dt(bundle["receipt"]["closed_at"]), "Future inherited source")
    if synthesis:
        _validate_synthesis_artifacts(bundle, current)
        if "revision" in bundle:
            from .research_synthesis_revision import lineage

            _require(
                child_context["run_key"] not in {node["parent"]["run_key"] for node in lineage(bundle)},
                "Revision child repeats ancestor identity",
            )
    for role in ALL_REPORTS if synthesis else REUSED:
        _require(role in data["reports"] and role in data["report_times"], "Reusable prerequisite missing")
        _require(
            timedelta(0)
            <= current - dt(data["report_times"][role])
            < timedelta(hours=cfg.research_cache_hours),
            "Reusable prerequisite stale or future",
        )
    return bundle


def import_into_desk(desk, bundle):
    """Populate a fresh child desk only; arithmetic replay is local, never provider work.

    Caller validates the bundle and live parent, and consumes the child authorization
    transactionally. Existing original evidence timestamps are retained verbatim.
    """
    from .research_team import ROLES

    if bundle.get("schema") == "research-current-refresh-v1":
        from .research_refresh import import_refresh

        return import_refresh(desk, bundle)
    _require(
        not desk.reports and not desk.calculations and not desk.correction and not desk.continuation,
        "Child desk is not fresh",
    )
    _require(
        desk.budget.models == desk.budget.tools == 0 and desk.budget.reserved == 0,
        "Child budget is not fresh",
    )
    original = desk.checkpoint()
    data = bundle["snapshot"]["desk"]
    reused = ALL_REPORTS if bundle.get("schema") == SYNTHESIS_SCHEMA else REUSED
    try:
        for field in ("sources", "companies", "documents", "texts", "unavailable_documents"):
            setattr(desk, field, copy.deepcopy(data[field]))
        desk.verify_calculations(data["calculations"])
        if bundle.get("schema") == SYNTHESIS_SCHEMA:
            desk.restore_arithmetic(
                [
                    {"event": kind, "result": copy.deepcopy(value)}
                    for kind, value in bundle["snapshot"]["budget"]["events"]
                    if kind in {"cash_flow_sum", "decimal_product"}
                ]
            )
        reports = {role: desk.validate(ROLES[role][0], data["reports"][role]) for role in reused}
        desk.reports = reports
        desk.report_times = {role: data["report_times"][role] for role in reused}
        desk.report_dependencies = {
            role: copy.deepcopy(data["report_dependencies"].get(role, {})) for role in reused
        }
        for dependencies in desk.report_dependencies.values():
            _require(
                all(
                    role in reports and digest == _hash_bytes(dumps(reports[role]).encode())
                    for role, digest in dependencies.items()
                ),
                "Imported dependency differs",
            )
        desk.reused = list(reused)
        budget = bundle["snapshot"]["budget"]
        desk.continuation = {
            "parent_lineage": {
                "reference": copy.deepcopy(bundle["parent"]),
                "bundle_sha256": fingerprint(bundle),
                "estimated_usd": budget["reserved"],
                "accounted_usd": bundle["spend"]["body"]["usd"],
                "cost_basis": bundle["spend"]["body"].get("cost_basis", "local_estimate"),
                "reconciliation_id": bundle["spend"]["body"].get("reconciliation_id"),
                "model_calls": budget["models"],
                "tool_calls": budget["tools"],
                "reused_roles": list(reused),
                "status": "unaccepted_prerequisite_research",
                "correction_reason": copy.deepcopy((data.get("correction") or {}).get("reason")),
                "instruction": "Complete valuation from inherited original evidence/calculations; fresh critique and final decision remain mandatory. Prior valuations and critiques are not current reports or investment approval.",
            }
        }
        if bundle.get("schema") == SYNTHESIS_SCHEMA:
            desk.continuation["reviewed_synthesis"] = {
                "addendum": copy.deepcopy(bundle["analytical_addendum"]),
                "review": copy.deepcopy(bundle["independent_review"]),
                "status": "original_reports_unaccepted_reviewed_addendum_separate",
            }
            if "revision" in bundle:
                desk.continuation["reviewed_synthesis"]["revision"] = {
                    **copy.deepcopy(bundle["revision"]),
                    "previous_decision": copy.deepcopy(bundle["completion"]["result"]["decision"]),
                    "status": "previous_completed_decision_unaccepted",
                }
            desk.continuation["parent_lineage"]["ancestor"] = {
                "reference": copy.deepcopy(bundle["ancestor_bundle"]["parent"]),
                "bundle_sha256": fingerprint(bundle["ancestor_bundle"]),
                "accounted_usd": bundle["ancestor_bundle"]["spend"]["body"]["usd"],
            }
            desk.continuation["parent_lineage"]["instruction"] = (
                "Synthesize the dated original reports and separately reviewed analyst addendum. No inherited investment approval, fresh research, tools or order authority. Preserve original gap flags."
            )
        _require(desk.context == original["context"], "Child context changed during import")
        _require(
            desk.budget.models == desk.budget.tools == 0 and desk.budget.reserved == 0,
            "Import charged provider work",
        )
        return desk.checkpoint()
    except BaseException:
        desk.restore(original)
        raise


def accounted_ancestry_cost(bundle):
    """Call only after complete bundle and authoritative parent validation."""
    cost = dec(bundle["spend"]["body"]["usd"])
    if bundle.get("schema") in {SYNTHESIS_SCHEMA, "research-current-refresh-v1"}:
        cost += accounted_ancestry_cost(bundle["ancestor_bundle"])
    return cost


def _validate_synthesis_artifacts(bundle, current):
    if "revision" in bundle:
        from .research_synthesis_revision import validate_revision_artifacts

        validate_revision_artifacts(bundle, current)
        return
    ancestor = bundle["ancestor_bundle"]
    descriptor = bundle["manifest"]["context"].get("continuation")
    _require(
        ancestor.get("schema") == SCHEMA
        and not ancestor["manifest"]["context"].get("continuation")
        and descriptor == {"parent": ancestor["parent"], "bundle_sha256": fingerprint(ancestor)}
        and ancestor["parent"]["run_key"] != bundle["parent"]["run_key"],
        "Synthesis requires exactly one ordinary continuation ancestor",
    )
    data = bundle["snapshot"]["desk"]
    old = ancestor["snapshot"]["desk"]
    for role in REUSED:
        _require(
            data["reports"][role] == old["reports"][role]
            and data["report_times"][role] == old["report_times"][role],
            "Ancestor prerequisite report changed",
        )
    _require(set(data["reports"]) == set(ALL_REPORTS), "Synthesis needs four completed reports")
    _require(
        data["report_dependencies"].get("adversarial")
        == {name: _hash_bytes(dumps(data["reports"][name]).encode()) for name in (*REUSED, "valuation")},
        "Synthesis critique is not bound to current reports",
    )
    addendum, review = bundle["analytical_addendum"], bundle["independent_review"]
    _require(
        set(addendum) == {"text", "created_at", "source_ids", "calculation_ids"}
        and isinstance(addendum["text"], str)
        and addendum["text"].strip()
        and len(dumps(addendum).encode()) <= 40000
        and max(dt(stamp) for stamp in data["report_times"].values())
        <= dt(addendum["created_at"])
        <= current,
        "Invalid dated synthesis addendum",
    )
    for field, records in (("source_ids", data["sources"]), ("calculation_ids", data["calculations"])):
        ids = addendum[field]
        _require(
            isinstance(ids, list)
            and ids
            and all(isinstance(i, str) for i in ids)
            and len(ids) == len(set(ids))
            and set(ids) <= records.keys(),
            "Unknown addendum references",
        )
    expected_sources = {i: fingerprint(data["sources"][i]) for i in addendum["source_ids"]}
    expected_calculations = {i: fingerprint(data["calculations"][i]) for i in addendum["calculation_ids"]}
    _require(
        set(review)
        == {
            "verdict",
            "reviewed_by",
            "reviewed_at",
            "parent",
            "addendum_sha256",
            "snapshot_sha256",
            "source_sha256",
            "calculation_sha256",
            "execution_authorized",
        }
        and review["verdict"] == "approved_conditional_synthesis"
        and isinstance(review["reviewed_by"], str)
        and review["reviewed_by"].strip()
        and dt(addendum["created_at"]) <= dt(review["reviewed_at"]) <= current
        and review["parent"] == bundle["parent"]
        and review["addendum_sha256"] == fingerprint(addendum)
        and review["snapshot_sha256"] == fingerprint(bundle["snapshot"])
        and review["source_sha256"] == expected_sources
        and review["calculation_sha256"] == expected_calculations
        and review["execution_authorized"] is False,
        "Independent synthesis review does not bind exact evidence",
    )


def export_synthesis_bundle(root, reference, store, addendum, review):
    """Export this fixed two-generation synthesis case; ordinary exports still reject chains."""
    bundle = _export_parent_bundle(root, reference, store, allow_continuation=True)
    descriptor = bundle["manifest"]["context"].get("continuation")
    _require(
        isinstance(descriptor, dict) and set(descriptor) == {"parent", "bundle_sha256"},
        "Synthesis parent must be an ordinary continuation",
    )
    ancestor = export_parent_bundle(root, descriptor["parent"], store)
    _require(fingerprint(ancestor) == descriptor["bundle_sha256"], "Original ancestor changed")
    frozen = json.loads(_read(run_directory(root, reference["run_key"]) / "continuation.json"))
    _require(frozen == ancestor, "Original imported ancestor differs")
    authorization, claim = _verify_ancestor_claim(store, reference, bundle, ancestor)
    bundle.update(
        schema=SYNTHESIS_SCHEMA,
        ancestor_bundle=ancestor,
        ancestor_claim=claim,
        ancestor_authorization=authorization,
        analytical_addendum=copy.deepcopy(addendum),
        independent_review=copy.deepcopy(review),
    )
    _validate_synthesis_artifacts(bundle, now())
    bundle["artifact_sha256"] = {
        k: fingerprint(v) for k, v in bundle.items() if k not in {"schema", "parent", "artifact_sha256"}
    }
    return bundle


def _verify_ancestor_claim(store, reference, bundle, ancestor):
    authorization = store.get(reference["run_key"], "research_continuation_authorization")
    claim = store.get(ancestor["parent"]["run_key"], "research_continuation_consumed")
    expected_claim = {
        "parent": ancestor["parent"],
        "child_run_key": reference["run_key"],
        "authorization_sha256": fingerprint(authorization),
        "bundle_sha256": fingerprint(ancestor),
    }
    _require(
        isinstance(authorization, dict)
        and isinstance(claim, dict)
        and claim.get("claim") == expected_claim
        and authorization.get("parent") == ancestor["parent"]
        and authorization.get("bundle_sha256") == fingerprint(ancestor)
        and authorization.get("child_run_key") == reference["run_key"]
        and authorization.get("experiment_id") == store.scope
        and authorization.get("context_sha256") == bundle["manifest"]["binding"]["context_sha256"]
        and authorization.get("config_sha256") == bundle["manifest"]["binding"]["config_sha256"]
        and authorization.get("code_sha256") == bundle["manifest"]["binding"]["code_sha256"]
        and isinstance(authorization.get("approved_by"), str)
        and authorization["approved_by"].strip()
        and 0 < dec(authorization["max_cost_usd"])
        and dec(bundle["receipt"]["known_usd"]) <= dec(authorization["max_cost_usd"])
        and accounted_ancestry_cost(ancestor) + dec(authorization["max_cost_usd"])
        <= dec(authorization["max_combined_cost_usd"])
        and dt(authorization["approved_at"]) <= dt(claim["consumed_at"]) < dt(authorization["expires_at"]),
        "Ancestor child claim or authorization differs",
    )
    entries = store.db.execute(
        "SELECT body FROM journal WHERE scope=? AND kind='research_continuation_consumed'", (store.scope,)
    ).fetchall()
    matches = [
        json.loads(row[0])
        for row in entries
        if json.loads(row[0]).get("claim", {}).get("parent") == ancestor["parent"]
    ]
    _require(matches == [claim], "Ancestor consumed claim journal differs")
    return authorization, claim
