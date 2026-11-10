"""Explicit paper-experiment expiry amendment; original experiment remains immutable."""

import re
from datetime import timedelta

from .models import dt, dumps, now


def effective_end(store, cfg):
    original = store.experiment["ends_at"]
    receipt = store.get("approved", "experiment_extension")
    if receipt is None:
        return original
    from .paper_dispatch import fingerprint, source_fingerprint

    try:
        if not isinstance(receipt, dict):
            raise TypeError("Invalid experiment extension approval")
        if (
            receipt.get("experiment_id") != store.scope
            or receipt.get("original_ends_at") != original
            or receipt.get("config_sha256") != fingerprint(cfg.model_dump(mode="json"))
            or receipt.get("source_sha256") != source_fingerprint()
            or not receipt.get("approved_by")
            or not receipt.get("reason")
            or not receipt.get("study_id")
            or receipt["study_id"] != receipt["schedule"]["study_id"]
            or receipt.get("schedule_sha256") != fingerprint(receipt["schedule"])
            or not re.fullmatch(r"[0-9a-f]{64}", receipt.get("funding_evidence_sha256", ""))
            or "prior_amendment_sha256" not in receipt
            or receipt["prior_amendment_sha256"] is not None
            or not dt(receipt["schedule"]["expected_sessions"][-1]["close"])
            <= dt(receipt["ends_at"])
            <= dt(receipt["schedule"]["expected_sessions"][-1]["close"]) + timedelta(days=1)
            or dt(receipt["approved_at"]) >= dt(original)
            or dt(receipt["approved_at"]) > now()
            or dt(receipt["ends_at"]) <= dt(original)
        ):
            raise ValueError("Invalid experiment extension approval or runtime binding")
        return receipt["ends_at"]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid experiment extension approval or runtime binding") from error


def execution_end(store, cfg):
    """Reconciliation grace never grants extra time for research or orders."""
    end = effective_end(store, cfg)
    receipt = store.get("approved", "experiment_extension")
    return receipt["schedule"]["expected_sessions"][-1]["close"] if receipt is not None else end


def approve_extension(service, receipt):
    """Install one immutable amendment while paused; no reset or activation."""
    store = service.store
    with service.lock():
        service.require_experiment()
        if not service.kill_path.exists():
            raise ValueError("Pause trading before approving an experiment extension")
        with store.db:
            store.db.execute("BEGIN IMMEDIATE")
            prior = store.get("approved", "experiment_extension")
            if prior is not None:
                if prior != receipt:
                    raise ValueError("Experiment extension is immutable")
                effective_end(store, service.cfg)
                return prior
            if now() >= dt(store.experiment["ends_at"]):
                raise ValueError("Cannot retroactively extend an expired experiment")
            if store.get("active", "paper_study") is not None:
                raise ValueError("Amend lifecycle before activating a frozen study")
            store.db.execute(
                "INSERT INTO documents VALUES (?,?,?,?)",
                (store.scope, "experiment_extension", "approved", dumps(receipt)),
            )
            effective_end(store, service.cfg)
            store.db.execute(
                "INSERT INTO journal(scope,at,kind,body) VALUES (?,?,?,?)",
                (store.scope, now().isoformat(), "experiment_extension_approved", dumps(receipt)),
            )
        # shortcut: one amendment per experiment; a later change needs an explicitly reviewed chain.
        return receipt
