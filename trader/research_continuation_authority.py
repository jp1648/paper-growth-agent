"""Explicit, single-child authority for importing settled research evidence."""

import json
import sqlite3
import stat
from pathlib import Path

from .models import dec, dt, dumps, now
from .paper_dispatch import fingerprint
from .research_manifest import run_directory, runtime_fingerprint
from .research_workflow import valid_reference


def validate_descriptor(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"parent", "bundle_sha256"}
        or not valid_reference(value["parent"])
        or not isinstance(value["bundle_sha256"], str)
        or len(value["bundle_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in value["bundle_sha256"])
    ):
        raise ValueError("Invalid research continuation descriptor")


def check_continuation(cfg, store, root, context, *, consume=False):
    """Verify immutable imports and current authority, including before paid dispatch."""
    from .research_continuation import validate_bundle, validate_parent_state

    descriptor = context.get("continuation")
    if descriptor is None:
        return None
    validate_descriptor(descriptor)
    parent = descriptor["parent"]
    child = context["run_key"]
    if child == parent["run_key"] or context.get("research_only") is not True:
        raise ValueError("Continuation requires a new research-only identity")
    directory = run_directory(root, child)
    path = directory / "continuation.json"
    if not path.is_file():
        raise ValueError("Continuation evidence is missing")
    if directory.is_symlink() or path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Continuation evidence must be a private regular file")
    bundle = json.loads(path.read_text())
    if not isinstance(store.get("snapshot"), dict):
        raise TypeError("Current reconciled portfolio is required")
    validate_bundle(
        bundle,
        context,
        descriptor["bundle_sha256"],
        experiment_id=store.scope,
        cfg=cfg,
        current_snapshot=store.get("snapshot"),
    )
    validate_parent_state(store, Path(root), bundle)
    approval = store.get(child, "research_continuation_authorization")
    fields = {
        "parent",
        "bundle_sha256",
        "child_run_key",
        "experiment_id",
        "config_sha256",
        "context_sha256",
        "code_sha256",
        "max_cost_usd",
        "max_combined_cost_usd",
        "approved_by",
        "approved_at",
        "expires_at",
    }
    if (
        not isinstance(approval, dict)
        or set(approval) != fields
        or approval["parent"] != parent
        or approval["bundle_sha256"] != descriptor["bundle_sha256"]
        or approval["child_run_key"] != child
        or approval["experiment_id"] != store.scope
        or approval["config_sha256"] != fingerprint(cfg)
        or approval["code_sha256"] != runtime_fingerprint()
        or approval["context_sha256"] != fingerprint(context)
        or not isinstance(approval["approved_by"], str)
        or not approval["approved_by"].strip()
        or not dt(approval["approved_at"]) <= now() < dt(approval["expires_at"])
        or not 0 < cfg.research_max_cost_usd <= dec(approval["max_cost_usd"])
        or dec(bundle["receipt"]["settlement_usd"]) + dec(approval["max_cost_usd"])
        > dec(approval["max_combined_cost_usd"])
    ):
        raise ValueError("Continuation lacks matching unexpired bounded authority")
    claim = {
        "parent": parent,
        "child_run_key": child,
        "authorization_sha256": fingerprint(approval),
        "bundle_sha256": descriptor["bundle_sha256"],
    }
    key = parent["run_key"]
    existing = store.get(key, "research_continuation_consumed")
    if existing is not None and existing.get("claim") != claim:
        raise ValueError("Parent continuation authority already belongs to another child")
    if consume and existing is None:
        record = {"claim": claim, "consumed_at": now().isoformat()}
        with store.db:
            try:
                store.db.execute(
                    "INSERT INTO documents(scope,kind,key,body) VALUES (?,?,?,?)",
                    (store.scope, "research_continuation_consumed", key, dumps(record)),
                )
                store.db.execute(
                    "INSERT INTO journal(scope,at,kind,body) VALUES (?,?,?,?)",
                    (store.scope, record["consumed_at"], "research_continuation_consumed", dumps(record)),
                )
            except sqlite3.IntegrityError:
                concurrent = store.get(key, "research_continuation_consumed")
                if not concurrent or concurrent.get("claim") != claim:
                    raise ValueError("Concurrent child continuation refused") from None
    elif not consume and existing is None:
        raise ValueError("Continuation authority has not been consumed")
    return bundle
