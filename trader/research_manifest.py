"""Immutable private inputs for a Temporal run; no credentials or provider calls."""

import hashlib
import json
import os
import stat
from importlib.metadata import version
from pathlib import Path

from .models import dt, dumps
from .paper_dispatch import fingerprint, source_fingerprint
from .research_workflow import valid_reference


def runtime_fingerprint():
    return fingerprint(
        {
            "source": source_fingerprint(),
            "dependencies": {
                name: version(name)
                for name in (
                    "deepagents",
                    "langgraph",
                    "langgraph-checkpoint",
                    "langgraph-checkpoint-sqlite",
                    "langchain",
                    "langchain-core",
                    "langchain-openai",
                    "temporalio",
                    "pydantic",
                )
            },
        }
    )


def run_directory(root, run_key):
    # Hash the logical ID so workflow IDs containing slashes cannot escape the private root.
    return Path(root) / hashlib.sha256(run_key.encode()).hexdigest()


def validate_manifest(manifest, cfg, experiment_id):
    if not isinstance(manifest, dict) or set(manifest) != {"binding", "context"}:
        raise ValueError("Invalid research manifest")
    binding, context = manifest["binding"], manifest["context"]
    if not isinstance(binding, dict) or not isinstance(context, dict):
        raise TypeError("Invalid research manifest values")
    if (
        context.get("research_only") is not True
        or not isinstance(context.get("portfolio"), dict)
        or context.get("symbol") not in cfg.research_universe
        or not isinstance(context.get("run_key"), str)
        or not experiment_id
        or experiment_id == "uninitialized"
    ):
        raise ValueError("Research-only context and initialized experiment required")
    dt(context["current_timestamp"])
    expected = {
        "run_key": context["run_key"],
        "experiment_id": experiment_id,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
    }
    reference = {"run_key": context["run_key"], "manifest_hash": fingerprint(binding)}
    if binding != expected or not valid_reference(reference):
        raise ValueError("Research manifest differs from its runtime, configuration or experiment")
    return reference


def prepare_manifest(root, cfg, experiment_id, context):
    context = json.loads(dumps(context))
    binding = {
        "run_key": context["run_key"],
        "experiment_id": experiment_id,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(context),
        "code_sha256": runtime_fingerprint(),
    }
    manifest = {"binding": binding, "context": context}
    reference = validate_manifest(manifest, cfg, experiment_id)
    directory = run_directory(root, reference["run_key"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError("Research run directory must be private and not a symlink")
    path = directory / "manifest.json"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = load_manifest(root, reference, cfg, experiment_id)
        if existing != manifest:
            raise ValueError("Run ID already has different frozen inputs") from None
    else:
        with os.fdopen(fd, "w") as handle:
            handle.write(dumps(manifest))
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return reference


def load_manifest(root, reference, cfg, experiment_id):
    if not valid_reference(reference):
        raise ValueError("Invalid research reference")
    directory = run_directory(root, reference["run_key"])
    path = directory / "manifest.json"
    if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError("Research run directory is not private")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        if stat.S_IMODE(os.fstat(handle.fileno()).st_mode) & 0o077:
            raise ValueError("Research manifest is not private")
        manifest = json.load(handle)
    if validate_manifest(manifest, cfg, experiment_id) != reference:
        raise ValueError("Research manifest reference changed")
    return manifest
