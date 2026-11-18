"""Plan or conditionally transfer pinned ownership; never stop/start a VM or clear trading pause."""

import argparse
import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def cloud(*args):
    return subprocess.run(["gcloud", *args], capture_output=True, text=True, check=True, timeout=60).stdout


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value):
        raise ValueError("Invalid cloud resource identifier")
    return value


def read_owner(bucket):
    uri = f"gs://{identifier(bucket)}/executor-owner.json"
    metadata = json.loads(cloud("storage", "objects", "describe", uri, "--format=json"))
    generation = metadata["generation"]
    if (
        metadata.get("bucket") != bucket
        or metadata.get("name") != "executor-owner.json"
        or not isinstance(generation, str)
        or not generation.isdecimal()
    ):
        raise ValueError("Ownership object metadata mismatch")
    owner = json.loads(cloud("storage", "cat", uri + "#" + generation))
    if type(owner.get("version")) is not int or owner["version"] != 1:
        raise ValueError("Unsupported ownership record")
    for field in ("project_id", "instance_name"):
        identifier(owner[field])
    for field in ("instance_id", "project_number"):
        if not isinstance(owner[field], str) or not owner[field].isdecimal():
            raise ValueError("Invalid pinned identity")
    zone = owner["zone"].split("/")
    if len(zone) != 4 or zone[:3] != ["projects", owner["project_number"], "zones"]:
        raise ValueError("Invalid pinned zone")
    identifier(zone[-1])
    return owner, generation


def instance(project, zone, name):
    return json.loads(
        cloud(
            "compute",
            "instances",
            "describe",
            identifier(name),
            "--project=" + identifier(project),
            "--zone=" + identifier(zone),
            "--format=json",
        )
    )


def transfer(bucket, project, zone, name, *, apply=False, expected_generation=None):
    owner, generation = read_owner(bucket)
    if expected_generation is not None and expected_generation != generation:
        raise ValueError("Ownership generation changed; prepare a new plan")
    if apply and expected_generation is None:
        raise ValueError("Apply requires the reviewed expected generation")
    target = instance(project, zone, name)
    number = str(json.loads(cloud("projects", "describe", project, "--format=json"))["projectNumber"])
    if not number.isdecimal() or not str(target["id"]).isdecimal():
        raise ValueError("Invalid target identity")
    if target["zone"] != f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{zone}":
        raise ValueError("Target zone mismatch")
    candidate = {
        "version": 1,
        "instance_id": str(target["id"]),
        "instance_name": name,
        "project_number": number,
        "project_id": project,
        "zone": f"projects/{number}/zones/{zone}",
    }
    if all(owner.get(k) == v for k, v in candidate.items()):
        return {"status": "unchanged", "generation": generation, "owner": candidate}
    source = instance(owner["project_id"], owner["zone"].split("/")[-1], owner["instance_name"])
    if str(source["id"]) != owner["instance_id"]:
        raise ValueError("Source VM identity changed; shutdown is not proven")
    blockers = []
    if source["status"] != "TERMINATED":
        blockers.append("Pinned source VM is not TERMINATED")
    if target["status"] != "RUNNING":
        blockers.append("Target VM is not RUNNING")
    result = {
        "status": "blocked" if blockers else "planned",
        "generation": generation,
        "source_status": source["status"],
        "owner": candidate,
        "blockers": blockers,
    }
    if not apply:
        return result
    if blockers:
        raise ValueError("; ".join(blockers))
    candidate.update(
        previous_generation=generation,
        previous_owner={k: owner[k] for k in candidate},
        previous_status="TERMINATED",
        assigned_at=datetime.now(UTC).isoformat(),
    )
    with tempfile.TemporaryDirectory(prefix="trader-owner-") as directory:
        path = Path(directory) / "owner.json"
        path.write_text(json.dumps(candidate, indent=2) + "\n")
        path.chmod(0o600)
        # Recheck both endpoints; lifecycle changes are not an atomic GCS transaction.
        current_target = instance(project, zone, name)
        if (
            str(current_target["id"]) != candidate["instance_id"]
            or current_target["status"] != "RUNNING"
            or current_target["zone"] != target["zone"]
        ):
            raise ValueError("Target identity or running state changed before transfer")
        # Keep the source shutdown check last; unknown state is never permission to take over.
        source = instance(owner["project_id"], owner["zone"].split("/")[-1], owner["instance_name"])
        if str(source["id"]) != owner["instance_id"] or source["status"] != "TERMINATED":
            raise ValueError("Source shutdown changed before transfer")
        cloud(
            "storage",
            "cp",
            str(path),
            f"gs://{bucket}/executor-owner.json",
            "--if-generation-match=" + generation,
        )
    saved, new_generation = read_owner(bucket)
    if saved != candidate:
        raise ValueError("Ownership changed after transfer; inspect state before any retry")
    return {"status": "transferred", "generation": new_generation, "owner": candidate}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("bucket", "project", "zone", "name"):
        parser.add_argument("--" + field, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-generation")
    print(json.dumps(transfer(**vars(parser.parse_args())), indent=2))


if __name__ == "__main__":
    main()
