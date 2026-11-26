import copy
from datetime import timedelta

import pytest

from trader.config import Settings
from trader.models import dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_continuation_authority import check_continuation
from trader.research_manifest import run_directory
from trader.store import Store


def setup(tmp_path, monkeypatch, child="child-one"):
    monkeypatch.setattr("trader.research_continuation_authority.runtime_fingerprint", lambda: "c" * 64)
    monkeypatch.setattr("trader.research_continuation.validate_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr("trader.research_continuation.validate_parent_state", lambda *args: None)
    cfg = Settings(database=str(tmp_path / "ledger.sqlite"))
    store = Store(cfg.database)
    store.put("active", {"id": "experiment"}, scope="global")
    store.experiment = store.get("active", scope="global")
    store.put("snapshot", {"account": {}, "positions": []})
    bundle = {"receipt": {"settlement_usd": "5"}, "spend": {"body": {"usd": "1"}}}
    root = tmp_path / "runs"
    directory = run_directory(root, child)
    directory.mkdir(parents=True, mode=0o700)
    path = directory / "continuation.json"
    path.write_text(dumps(bundle))
    path.chmod(0o600)
    parent = {"run_key": "parent", "manifest_hash": "a" * 64}
    context = {
        "run_key": child,
        "research_only": True,
        "continuation": {"parent": parent, "bundle_sha256": fingerprint(bundle)},
    }
    approval = {
        "parent": parent,
        "bundle_sha256": fingerprint(bundle),
        "child_run_key": child,
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": "c" * 64,
        "max_cost_usd": str(cfg.research_max_cost_usd),
        "max_combined_cost_usd": str(cfg.research_max_cost_usd + 1),
        "approved_by": "reviewer",
        "approved_at": (now() - timedelta(minutes=1)).isoformat(),
        "expires_at": (now() + timedelta(hours=1)).isoformat(),
    }
    store.put(child, approval, "research_continuation_authorization")
    return cfg, store, root, context, approval, bundle


def test_single_child_consumption_is_audited_idempotent_and_dispatch_requires_it(tmp_path, monkeypatch):
    cfg, store, root, context, _, bundle = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="not been consumed"):
        check_continuation(cfg, store, root, context)
    assert check_continuation(cfg, store, root, context, consume=True) == bundle
    original = store.get("parent", "research_continuation_consumed")
    assert check_continuation(cfg, store, root, context, consume=True) == bundle
    assert check_continuation(cfg, store, root, context) == bundle
    assert store.get("parent", "research_continuation_consumed") == original
    assert (
        store.db.execute(
            "SELECT count(*) FROM journal WHERE kind='research_continuation_consumed'"
        ).fetchone()[0]
        == 1
    )
    store.db.close()


@pytest.mark.parametrize(
    "change",
    [
        {"max_cost_usd": "0"},
        {"max_combined_cost_usd": "0"},
        {"approved_by": ""},
        {"context_sha256": "d" * 64},
        {"code_sha256": "d" * 64},
        {"config_sha256": "d" * 64},
        {"experiment_id": "other"},
        {"expires_at": "2000-01-01T00:00:00+00:00"},
    ],
)
def test_authority_drift_fails_before_consumption(tmp_path, monkeypatch, change):
    cfg, store, root, context, approval, _ = setup(tmp_path, monkeypatch)
    store.put(context["run_key"], {**approval, **change}, "research_continuation_authorization")
    with pytest.raises(ValueError, match="authority"):
        check_continuation(cfg, store, root, context, consume=True)
    assert store.get("parent", "research_continuation_consumed") is None
    store.db.close()


def test_second_child_cannot_consume_parent_or_reprice_claim(tmp_path, monkeypatch):
    cfg, store, root, context, approval, _ = setup(tmp_path, monkeypatch)
    check_continuation(cfg, store, root, context, consume=True)
    second = copy.deepcopy(context)
    second["run_key"] = "child-two"
    directory = run_directory(root, second["run_key"])
    directory.mkdir(mode=0o700)
    target = directory / "continuation.json"
    target.write_bytes((run_directory(root, context["run_key"]) / "continuation.json").read_bytes())
    target.chmod(0o600)
    store.put(
        second["run_key"],
        {**approval, "child_run_key": second["run_key"], "context_sha256": fingerprint(second)},
        "research_continuation_authorization",
    )
    with pytest.raises(ValueError, match="another child"):
        check_continuation(cfg, store, root, second, consume=True)
    store.put(
        context["run_key"], {**approval, "approved_by": "different"}, "research_continuation_authorization"
    )
    with pytest.raises(ValueError, match="another child"):
        check_continuation(cfg, store, root, context)
    store.db.close()
