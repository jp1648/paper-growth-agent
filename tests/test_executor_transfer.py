import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "transfer", Path(__file__).parents[1] / "deploy/transfer-executor.py"
)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


@pytest.fixture
def cloud(monkeypatch):
    state = {
        "generation": "10",
        "source_status": "TERMINATED",
        "source_id": "111",
        "target_status": "RUNNING",
        "writes": 0,
        "owner": {
            "version": 1,
            "project_id": "project-a",
            "project_number": "123",
            "instance_name": "old-vm",
            "instance_id": "111",
            "zone": "projects/123/zones/us-east1-b",
        },
        "source_reads": 0,
    }

    def call(*args):
        if args[:3] == ("storage", "objects", "describe"):
            return json.dumps(
                {
                    "bucket": "private-recovery",
                    "name": "executor-owner.json",
                    "generation": state["generation"],
                }
            )
        if args[:2] == ("storage", "cat"):
            assert args[2].endswith("#" + state["generation"])
            return json.dumps(state["owner"])
        if args[:3] == ("compute", "instances", "describe"):
            old = args[3] == "old-vm"
            if old:
                state["source_reads"] += 1
            if old and state.get("source_timeout"):
                raise subprocess.TimeoutExpired(args, 60)
            status = state["source_status"] if old else state["target_status"]
            if old and state.get("restart") and state["source_reads"] > 1:
                status = "RUNNING"
            return json.dumps(
                {
                    "id": state["source_id"] if old else "222",
                    "status": status,
                    "zone": "https://www.googleapis.com/compute/v1/projects/project-a/zones/us-east1-b",
                }
            )
        if args[:2] == ("projects", "describe"):
            return json.dumps({"projectNumber": "123"})
        if args[:2] == ("storage", "cp"):
            state["writes"] += 1
            assert args[-1] == "--if-generation-match=10"
            if state.get("conflict"):
                raise subprocess.CalledProcessError(1, args)
            p = Path(args[2])
            assert p.stat().st_mode & 0o077 == 0
            state["owner"] = json.loads(p.read_text())
            state["generation"] = "11"
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(transfer, "cloud", call)
    return state


def run(**kwargs):
    return transfer.transfer("private-recovery", "project-a", "us-east1-b", "new-vm", **kwargs)


def test_plan_is_read_only_and_binds_exact_object_generation(cloud):
    result = run()
    assert result["status"] == "planned" and result["generation"] == "10"
    assert cloud["writes"] == 0


@pytest.mark.parametrize("status", ["RUNNING", "STOPPING", "SUSPENDED", "REPAIRING", "UNKNOWN"])
def test_only_terminated_source_permits_transfer(cloud, status):
    cloud["source_status"] = status
    assert run()["status"] == "blocked"
    with pytest.raises(ValueError, match="TERMINATED"):
        run(apply=True, expected_generation="10")
    assert cloud["writes"] == 0


@pytest.mark.parametrize(
    "failure",
    ["different_id", "timeout", "restart", "target_stopped", "missing_generation", "stale_generation"],
)
def test_uncertain_or_changed_state_cannot_take_over(cloud, failure):
    updates = {
        "different_id": {"source_id": "999"},
        "timeout": {"source_timeout": True},
        "restart": {"restart": True},
        "target_stopped": {"target_status": "TERMINATED"},
    }
    cloud.update(updates.get(failure, {}))
    generation = None if failure == "missing_generation" else "9" if failure == "stale_generation" else "10"
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        run(apply=True, expected_generation=generation)
    assert cloud["writes"] == 0


def test_generation_conflict_has_no_blind_retry(cloud):
    cloud["conflict"] = True
    with pytest.raises(subprocess.CalledProcessError):
        run(apply=True, expected_generation="10")
    assert cloud["writes"] == 1 and cloud["owner"]["instance_id"] == "111"


def test_verified_transfer_retains_previous_identity_and_checks_readback(cloud):
    result = run(apply=True, expected_generation="10")
    assert result["status"] == "transferred" and result["generation"] == "11"
    assert cloud["source_reads"] == 2 and cloud["writes"] == 1
    assert result["owner"]["previous_owner"]["instance_id"] == "111"
    assert result["owner"]["previous_status"] == "TERMINATED"
    assert run()["status"] == "unchanged"
    assert cloud["writes"] == 1


def test_committed_transfer_with_lost_response_requires_read_only_reconciliation(cloud, monkeypatch):
    original = transfer.cloud

    def lose_response(*args):
        result = original(*args)
        if args[:2] == ("storage", "cp"):
            raise subprocess.TimeoutExpired(args, 60)
        return result

    monkeypatch.setattr(transfer, "cloud", lose_response)
    with pytest.raises(subprocess.TimeoutExpired):
        run(apply=True, expected_generation="10")
    assert cloud["writes"] == 1
    assert cloud["owner"]["instance_id"] == "222"
    with pytest.raises(ValueError, match="generation changed"):
        run(apply=True, expected_generation="10")
    result = run()
    assert result["status"] == "unchanged"
    assert result["generation"] == "11"
    assert cloud["owner"]["previous_owner"]["instance_id"] == "111"
    assert cloud["writes"] == 1


def test_owner_changed_after_commit_is_not_reported_as_success(cloud, monkeypatch):
    original = transfer.cloud

    def replace_after_commit(*args):
        result = original(*args)
        if args[:2] == ("storage", "cp"):
            cloud["owner"]["instance_id"] = "333"
            cloud["generation"] = "12"
        return result

    monkeypatch.setattr(transfer, "cloud", replace_after_commit)
    with pytest.raises(ValueError, match="Ownership changed after transfer"):
        run(apply=True, expected_generation="10")
    assert cloud["writes"] == 1
    assert cloud["owner"]["instance_id"] == "333"
    assert cloud["generation"] == "12"
