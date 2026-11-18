import copy

import pytest
from conftest import AT

from trader.paper_dispatch import fingerprint
from trader.research_client import ResearchPending, TemporalResearchClient
from trader.research_journal import Journal
from trader.research_manifest import load_manifest, run_directory


class Transport:
    def __init__(self):
        self.calls = []
        self.state = "running"
        self.namespace = "default"

    def status(self, reference, *, start):
        self.calls.append((reference, start))
        return {**reference, "state": self.state, "stage": "lead"}


@pytest.fixture
def client_env(env, tmp_path, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("trader.research_runtime.now", lambda: AT)
    service.reconcile()
    transport = Transport()
    root = tmp_path / "runs"
    client = TemporalResearchClient(
        service.cfg,
        service.store,
        root=root,
        address="unused",
        transport=transport,
    )
    context = {
        "symbol": "ACME",
        "run_key": "fixture-run",
        "research_only": False,
        "portfolio": {"snapshot": service.store.get("snapshot")},
        "current_timestamp": AT.isoformat(),
    }
    return service, client, root, transport, context


def test_freeze_once_and_poll_same_reference_without_resending(client_env):
    service, client, root, transport, context = client_env
    with pytest.raises(ResearchPending):
        client.start(context)
    frozen = service.store.get(context["run_key"], "research_request")
    reference = client.reference_for(context)
    assert frozen["context"]["research_only"] is True
    assert frozen["context"]["runtime_control"]
    assert load_manifest(root, reference, service.cfg, service.store.scope)["context"] == frozen["context"]
    with pytest.raises(ResearchPending):
        client.start({**context, "current_timestamp": (AT.isoformat())})
    assert len(transport.calls) == 2
    assert transport.calls == [(reference, True), (reference, False)]
    with pytest.raises(ValueError, match="different study, slot or symbol"):
        client.reference_for({**context, "symbol": "OTHER"})


def test_complete_status_requires_receipt_settlement_and_journal_hashes(client_env):
    service, client, root, transport, context = client_env
    with pytest.raises(ResearchPending):
        client.start(context)
    reference = client.reference_for(context)
    transport.state = "complete"
    with pytest.raises(ResearchPending, match="commit_receipt_pending"):
        client.poll(reference)
    result = {
        "decision": {"symbol": "ACME", "action": "WATCH"},
        "research_completed_at": AT.isoformat(),
    }
    audit = {"symbol": "ACME", "estimated_usd": "1.25"}
    key = context["run_key"]
    service.store.put(key, result, "research_result")
    service.store.put(key, audit, "research_audit")
    service.store.put(
        f"research_stage_receipt:{key}:commit",
        {
            **reference,
            "stage": "commit",
            "receipt": {"status": "complete", "receipt_ref": f"research_stage_receipt:{key}:commit"},
        },
        "research_stage_receipt",
    )
    binding = load_manifest(root, reference, service.cfg, service.store.scope)["binding"]
    service.store.put(
        "spend",
        {
            "run_key": key,
            "experiment_id": service.store.scope,
            "config_sha256": binding["config_sha256"],
            "durable": True,
            "status": "settled",
            "unknown_total": False,
            "usd": "1.25",
        },
        "research_spend",
        scope="global",
    )
    journal = Journal(run_directory(root, key) / "operations.sqlite", binding)
    hashes = {
        "result": fingerprint(result),
        "audit": fingerprint(audit),
    }
    with journal.lease():
        journal.save_state({"desk": {"continuation": {"commit_hashes": hashes}}})
    assert client.poll(reference) == result
    changed = copy.deepcopy(result)
    changed["research_completed_at"] = "2026-10-22T15:00:00+00:00"
    service.store.put(key, changed, "research_result")
    with pytest.raises(ValueError, match="hashes differ"):
        client.poll(reference)
    service.store.put(key, result, "research_result")
    spend = service.store.get("spend", "research_spend", scope="global")
    spend["status"] = "reserved"
    service.store.put("spend", spend, "research_spend", scope="global")
    with pytest.raises(ValueError, match="not settled"):
        client.poll(reference)


def test_pending_transport_does_not_require_result(client_env):
    service, client, _, _, context = client_env
    with pytest.raises(ResearchPending) as pending:
        client.start(context)
    assert pending.value.state == "running"
    assert service.store.get(context["run_key"], "research_result") is None
    assert len(service.store.items("research_request")) == 1


def test_lost_initial_submission_requires_explicit_same_id_resolution(client_env, monkeypatch):
    service, client, root, transport, context = client_env
    monkeypatch.setattr("trader.research_client.now", lambda: AT)
    transport.state = "history_unavailable"
    with pytest.raises(ResearchPending, match="history_unavailable"):
        client.start(context)
    reference = client.reference_for(context)
    with pytest.raises(ResearchPending, match="history_unavailable"):
        client.poll(reference)
    assert [start for _, start in transport.calls] == [True, False]
    proof = {
        "verified_by": "operator",
        "reason": "Temporal reports no execution for this exact workflow ID",
        "observed_at": AT.isoformat(),
        "evidence_sha256": "a" * 64,
        "namespace": "default",
        "workflow_id": f"research:{reference['run_key']}",
        "manifest_hash": reference["manifest_hash"],
    }
    client.authorize_resubmit(reference, proof)
    with pytest.raises(ResearchPending):
        client.poll(reference)
    assert [start for _, start in transport.calls] == [True, False, False, True]
    assert service.store.items("research_workflow_resubmission")
    assert service.store.items("research_workflow_resubmission_consumed")
    with pytest.raises(ResearchPending):
        client.poll(reference)
    assert transport.calls[-1][1] is False
    # A stage reservation or journal forbids even an explicitly requested retry.
    (run_directory(root, reference["run_key"]) / "operations.sqlite").touch()
    with pytest.raises(ValueError, match="journal exists"):
        client.authorize_resubmit(reference, proof)


def test_terminal_workflow_is_retained_for_review(client_env):
    service, client, _, transport, context = client_env
    transport.state = "terminal_review_required"
    with pytest.raises(ResearchPending, match="terminal_review_required"):
        client.start(context)
    assert service.store.get(context["run_key"], "research_workflow_terminal")


def test_original_claim_can_reenter_before_frozen_request(client_env):
    service, client, _, _, _ = client_env
    state = {
        "symbol": "ACME",
        "run_key": "scheduled-early-crash",
        "study_id": "study",
        "study_stratum": "scheduled",
    }
    pending = {
        "status": "starting",
        "slot": state["run_key"],
        "study_id": "study",
        "schedule_sha256": None,
        "state": state,
    }
    claim = {"symbol": "ACME", "schedule_sha256": None}
    assert service.store.claim_paper_slot("study", state["run_key"], claim, pending=pending)
    invocation = {"status": "claimed", **claim}
    assert client.same_invocation(state, invocation)
    service.store.put(state["run_key"], {**pending, "status": "blocked"}, "paper_slot_pending")
    assert not client.same_invocation(state, invocation)
