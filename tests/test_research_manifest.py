import json

import pytest
from conftest import AT

from trader.research_manifest import load_manifest, prepare_manifest, run_directory


def context():
    return {
        "run_key": "research/../one",
        "symbol": "ACME",
        "research_only": True,
        "portfolio": {},
        "current_timestamp": AT.isoformat(),
    }


def test_manifest_is_private_immutable_and_bound_to_runtime(env, tmp_path, monkeypatch):
    service, _, _ = env
    root = tmp_path / "runs"
    reference = prepare_manifest(root, service.cfg, service.store.scope, context())
    assert prepare_manifest(root, service.cfg, service.store.scope, context()) == reference
    directory = run_directory(root, reference["run_key"])
    assert directory.parent == root and directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert load_manifest(root, reference, service.cfg, service.store.scope)["context"] == context()
    with pytest.raises(ValueError, match="differs"):
        load_manifest(root, reference, service.cfg, "changed-experiment")
    with pytest.raises(ValueError, match="reference changed"):
        prepare_manifest(root, service.cfg, service.store.scope, {**context(), "fresh": True})
    monkeypatch.setattr("trader.research_manifest.runtime_fingerprint", lambda: "a" * 64)
    with pytest.raises(ValueError, match="differs"):
        load_manifest(root, reference, service.cfg, service.store.scope)


def test_manifest_rejects_modified_inputs_and_public_or_symlink_files(env, tmp_path):
    service, _, _ = env
    root = tmp_path / "runs"
    reference = prepare_manifest(root, service.cfg, service.store.scope, context())
    path = run_directory(root, reference["run_key"]) / "manifest.json"
    original = path.read_text()
    modified = json.loads(original)
    modified["context"]["symbol"] = "OTHER"
    path.write_text(json.dumps(modified))
    with pytest.raises(ValueError, match="differs"):
        load_manifest(root, reference, service.cfg, service.store.scope)
    path.write_text(original)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="not private"):
        load_manifest(root, reference, service.cfg, service.store.scope)
    path.unlink()
    other = tmp_path / "elsewhere"
    other.write_text(original)
    path.symlink_to(other)
    with pytest.raises(OSError):
        load_manifest(root, reference, service.cfg, service.store.scope)


def test_manifest_never_authorizes_execution(env, tmp_path):
    service, _, _ = env
    with pytest.raises(ValueError, match="Research-only"):
        prepare_manifest(tmp_path, service.cfg, service.store.scope, {**context(), "research_only": False})
    incomplete = context()
    incomplete.pop("portfolio")
    with pytest.raises(ValueError, match="Research-only"):
        prepare_manifest(tmp_path, service.cfg, service.store.scope, incomplete)
