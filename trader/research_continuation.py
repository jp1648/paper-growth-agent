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


def export_parent_bundle(root, reference, store):
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
    _require(not context.get("continuation"), "Continuation chains are unsupported")
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
            _require(raw and receipt_row, "Parent snapshot/accounting receipt missing")
            serde = JsonPlusSerializer(pickle_fallback=False)
            snapshot = _json(serde.loads_typed(tuple(raw)))
            receipt = json.loads(receipt_row[0])
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
            and dec(spend["usd"]) == dec(receipt["settlement_usd"]) == dec(receipt["known_usd"])
            and receipt.get("operation_key") is None
            and not spend.get("reconciliation_id"),
            "Parent accounting is not an unchanged known settlement",
        )
        _require(
            not store.get(reference["run_key"], "research_result"),
            "Completed parent result cannot be imported",
        )
        budget = snapshot["budget"]
        _require(dec(budget["reserved"]) == dec(spend["usd"]), "Parent snapshot cost differs")
        costs = [v for k, v in budget["events"] if k == "model_cost"]
        _require(
            len(costs) == budget["models"] and all(v.get("estimated_usd") is not None for v in costs),
            "Parent usage incomplete",
        )
        _require(
            sum((dec(v["estimated_usd"]) for v in costs), dec(0)) == dec(spend["usd"]),
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
        bundle["artifact_sha256"] = {
            k: fingerprint(bundle[k])
            for k in ("manifest", "snapshot", "receipt", "spend", "terminal", "journal_tables_sha256")
        }
        return bundle
    finally:
        os.close(lock)


def validate_parent_state(store, root, bundle):
    """Revalidate authoritative originals, including every journal table, without mutation."""
    _require(
        fingerprint(export_parent_bundle(root, bundle["parent"], store)) == fingerprint(bundle),
        "Parent artifacts changed",
    )


def validate_bundle(
    bundle, child_context, expected_sha256, *, experiment_id, cfg, current=None, current_snapshot=None
):
    current = current or now()
    _require(
        bundle.get("schema") == SCHEMA and fingerprint(bundle) == expected_sha256,
        "Continuation bundle hash differs",
    )
    parent = bundle["manifest"]["context"]
    binding = bundle["manifest"]["binding"]
    _require(
        bundle["artifact_sha256"]
        == {
            k: fingerprint(bundle[k])
            for k in ("manifest", "snapshot", "receipt", "spend", "terminal", "journal_tables_sha256")
        },
        "Continuation artifact hash differs",
    )
    _require(
        binding["experiment_id"] == experiment_id
        and fingerprint(binding) == bundle["parent"]["manifest_hash"],
        "Continuation experiment differs",
    )
    _require(
        child_context.get("continuation") == {"parent": bundle["parent"], "bundle_sha256": expected_sha256},
        "Continuation descriptor differs",
    )
    _require(
        child_context.get("run_key")
        and child_context["run_key"] != parent["run_key"]
        and not parent.get("continuation"),
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
    for role in REUSED:
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
    try:
        for field in ("sources", "companies", "documents", "texts", "unavailable_documents"):
            setattr(desk, field, copy.deepcopy(data[field]))
        desk.verify_calculations(data["calculations"])
        reports = {role: desk.validate(ROLES[role][0], data["reports"][role]) for role in REUSED}
        desk.reports = reports
        desk.report_times = {role: data["report_times"][role] for role in REUSED}
        desk.report_dependencies = {
            role: copy.deepcopy(data["report_dependencies"].get(role, {})) for role in REUSED
        }
        for dependencies in desk.report_dependencies.values():
            _require(
                all(
                    role in reports and digest == _hash_bytes(dumps(reports[role]).encode())
                    for role, digest in dependencies.items()
                ),
                "Imported dependency differs",
            )
        desk.reused = list(REUSED)
        budget = bundle["snapshot"]["budget"]
        desk.continuation = {
            "parent_lineage": {
                "reference": copy.deepcopy(bundle["parent"]),
                "bundle_sha256": fingerprint(bundle),
                "estimated_usd": budget["reserved"],
                "model_calls": budget["models"],
                "tool_calls": budget["tools"],
                "reused_roles": list(REUSED),
                "status": "unaccepted_prerequisite_research",
                "correction_reason": copy.deepcopy((data.get("correction") or {}).get("reason")),
                "instruction": "Complete valuation from inherited original evidence/calculations; fresh critique and final decision remain mandatory. Prior valuations and critiques are not current reports or investment approval.",
            }
        }
        _require(desk.context == original["context"], "Child context changed during import")
        _require(
            desk.budget.models == desk.budget.tools == 0 and desk.budget.reserved == 0,
            "Import charged provider work",
        )
        return desk.checkpoint()
    except BaseException:
        desk.restore(original)
        raise
