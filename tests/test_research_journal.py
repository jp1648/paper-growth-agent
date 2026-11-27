import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

from trader.research_journal import (
    AmbiguousOperation,
    BindingMismatch,
    Journal,
    JournalBusy,
    JournalError,
    RequestHashMismatch,
    ResolutionRejected,
)


def test_inspection_identifies_ambiguity_without_exposing_or_changing_private_state(tmp_path):
    import json

    with Journal(tmp_path / "research.sqlite", {"run": "one"}).lease() as journal:
        state = {"secret_prompt": "private evidence text"}
        journal.begin("model:1", "request-hash", state)
        before = journal.load_state()
        result = journal.inspect_operations()
        assert result["complete_operations"] == 0
        pending = result["pending_operations"]
        assert len(pending) == 1 and pending[0]["key"] == "model:1"
        assert pending[0]["request_hash"] == "request-hash"
        assert len(pending[0]["saved_pre_dispatch_state_sha256"]) == 64
        assert "private evidence" not in json.dumps(result)
        assert journal.inspect_operations() == result and journal.load_state() == before
        with pytest.raises(AmbiguousOperation):
            journal.begin("model:1", "request-hash", state)


def test_dispatch_identity_is_durable_and_does_not_authorize_retry(tmp_path):
    path = tmp_path / "research.sqlite"
    with Journal(path, {"run": "one"}).lease() as journal:
        journal.begin("model:1", "original-hash", {"budget": {"calls": 0}})
        request = {"model": "fixture", "projected_request": {"prompt": "private"}}
        identifier = journal.record_dispatch("model:1", "original-hash", request)
        assert journal.record_dispatch("model:1", "original-hash", request) == identifier
        with pytest.raises(RequestHashMismatch):
            journal.record_dispatch("model:1", "original-hash", {"model": "changed"})
    with Journal(path, {"run": "one"}).lease() as journal:
        dispatch = journal.inspect_operations()["pending_operations"][0]["dispatches"][0]
        assert dispatch["client_request_id"] == identifier and dispatch["attempt"] == 0
        assert len(dispatch["projected_request_sha256"]) == 64
        with pytest.raises(AmbiguousOperation):
            journal.begin("model:1", "original-hash", {"budget": {"calls": 0}})
        journal.resolve_non_dispatch(
            "model:1",
            "original-hash",
            {"operator": "fixture", "reference": identifier, "basis": "Fixture transport never sent"},
            verifier=lambda *args: True,
        )
        journal.begin("model:1", "original-hash", {"budget": {"calls": 0}})
        replacement = journal.record_dispatch("model:1", "original-hash", request)
        assert replacement != identifier
        dispatches = journal.inspect_operations()["pending_operations"][0]["dispatches"]
        assert [entry["client_request_id"] for entry in dispatches] == [identifier, replacement]
        assert [entry["attempt"] for entry in dispatches] == [0, 1]


@pytest.mark.parametrize("approve_first", [False, True])
def test_saved_provider_response_cannot_be_overridden_by_non_dispatch_proof(tmp_path, approve_first):
    with Journal(tmp_path / "research.sqlite", {"run": "one"}).lease() as journal:
        state = {"budget": {"calls": 0}}
        journal.begin("model:1", "hash", state)
        identifier = journal.record_dispatch("model:1", "hash", {"model": "fixture"})
        proof = {"operator": "fixture", "reference": identifier, "basis": "fixture proof"}
        if approve_first:
            journal.resolve_non_dispatch("model:1", "hash", proof, verifier=lambda *args: True)
        journal.exchange("model:1", "hash", identifier, {"body": b"original"}, lambda: {"body": b"response"})
        with pytest.raises(ResolutionRejected, match="disproves"):
            journal.resolve_non_dispatch(
                "model:1",
                "hash",
                proof,
                verifier=lambda *args: pytest.fail("must reject before verification"),
            )
        with pytest.raises(AmbiguousOperation, match="replayed"):
            journal.begin("model:1", "hash", state)
        assert journal.replayable_exchange("model:1", "hash", state)
        assert (
            journal._connection()
            .execute("SELECT count(*) FROM non_dispatch_resolutions WHERE used_at IS NOT NULL")
            .fetchone()[0]
            == 0
        )


def test_complete_response_and_state_replay_after_reopen(tmp_path):
    path = tmp_path / "research.sqlite"
    binding = {"code": "abc", "config": {"limit": 6}, "experiment": "run-1"}
    pre = {"budget": {"calls": 0}}
    post = {"budget": {"calls": 1}, "reports": ["business"]}
    response = ModelResponse(result=[AIMessage(content="original response")])
    journal = Journal(path, binding)
    with journal.lease():
        assert journal.load_state() is None
        assert journal.begin("model:1", "hash-1", pre) is None
        assert journal.load_state() == pre
        journal.complete("model:1", "hash-1", response, post)
        assert journal.load_state() == post
    with Journal(path, {"experiment": "run-1", "config": {"limit": 6}, "code": "abc"}).lease() as reopened:
        cached = reopened.begin("model:1", "hash-1", {"ignored": True})
        assert cached["result"] == response
        assert cached["state"] == post
        assert reopened.load_state() == post
        reopened.save_state({"stage": "next"})
        assert reopened.load_state() == {"stage": "next"}


def test_pending_is_ambiguous_after_crash_and_never_retried(tmp_path):
    path = tmp_path / "research.sqlite"
    with Journal(path, {"run": "one"}).lease() as journal:
        journal.begin("tool:1", "hash", {"before": True})
    with Journal(path, {"run": "one"}).lease() as reopened:
        assert reopened.load_state() == {"before": True}
        with pytest.raises(AmbiguousOperation):
            reopened.begin("tool:1", "hash", {})
        with pytest.raises(AmbiguousOperation):
            reopened.begin("tool:2", "different", {})
        with pytest.raises(AmbiguousOperation):
            reopened.save_state({"unsafe": True})


def test_binding_and_request_hash_are_exact(tmp_path):
    path = tmp_path / "research.sqlite"
    with Journal(path, {"code": "one"}).lease() as journal:
        journal.begin("model:1", "request-one", {})
        journal.complete("model:1", "request-one", "ok", {})
        with pytest.raises(RequestHashMismatch):
            journal.begin("model:1", "request-two", {})
    with pytest.raises(BindingMismatch), Journal(path, {"code": "two"}).lease():
        pass


def test_complete_is_atomic_on_sql_failure(tmp_path):
    path = tmp_path / "research.sqlite"
    with Journal(path, {"run": 1}).lease() as journal:
        journal.begin("tool:1", "hash", {"calls": 0})
        journal._db.execute(
            "CREATE TRIGGER fail_snapshot BEFORE UPDATE ON snapshot "
            "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            journal.complete("tool:1", "hash", "external answer", {"calls": 1})
        assert journal.load_state() == {"calls": 0}
        with pytest.raises(AmbiguousOperation):
            journal.begin("tool:1", "hash", {})


def test_process_lease_blocks_second_owner(tmp_path):
    path = tmp_path / "research.sqlite"
    with Journal(path, {"run": 1}).lease():
        with pytest.raises(JournalBusy), Journal(path, {"run": 1}).lease():
            pass
        script = (
            "import sys; from trader.research_journal import Journal,JournalBusy; "
            "j=Journal(sys.argv[1],{'run':1}); "
            "\ntry:\n with j.lease(): print('acquired')"
            "\nexcept JournalBusy: print('busy')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "busy"


def test_access_requires_lease_and_duplicate_complete_is_rejected(tmp_path):
    journal = Journal(tmp_path / "research.sqlite", {"run": 1})
    with pytest.raises(JournalError, match="lease"):
        journal.load_state()
    with journal.lease():
        journal.begin("x", "hash", {})
        journal.complete("x", "hash", {"ok": True}, {})
        with pytest.raises(JournalError, match="already complete"):
            journal.complete("x", "hash", {"ok": True}, {})


def test_cross_thread_access_is_serialized_and_files_are_private(tmp_path):
    path = tmp_path / "private" / "research.sqlite"
    journal = Journal(path, {"run": 1})
    with journal.lease():
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(journal.save_state, {"worker": i}) for i in range(2)]
            for future in futures:
                future.result()
        assert journal.load_state()["worker"] in {0, 1}
        assert path.parent.stat().st_mode & 0o077 == 0
        for file in path.parent.iterdir():
            assert file.stat().st_mode & 0o077 == 0


def test_verified_non_dispatch_authorizes_one_retry_and_retains_attempt_history(tmp_path):
    path = tmp_path / "research.sqlite"
    before = {"budget": {"elapsed_seconds": 1.0, "models": 0}, "desk": {"sources": []}}
    evidence = {
        "operator": "reviewer",
        "reference": "provider-trace:request-1",
        "basis": "No dispatch recorded",
    }
    with Journal(path, {"run": 1}).lease() as journal:
        assert journal.begin("model:1", "hash", before) is None
    with Journal(path, {"run": 1}).lease() as journal:
        with pytest.raises(ResolutionRejected, match="Independent proof"):
            journal.resolve_non_dispatch("model:1", "hash", evidence, verifier=lambda *args: False)
        proof = journal.resolve_non_dispatch(
            "model:1",
            "hash",
            evidence,
            verifier=lambda key, request_hash, supplied: (
                key == "model:1" and request_hash == "hash" and supplied == evidence
            ),
        )
        assert proof["attempt"] == 1 and len(proof["evidence_hash"]) == 64
        with pytest.raises(ResolutionRejected, match="already authorized"):
            journal.resolve_non_dispatch("model:1", "hash", evidence, verifier=lambda *args: True)
        changed = {"budget": {"elapsed_seconds": 2.0, "models": 1}, "desk": {"sources": []}}
        with pytest.raises(ResolutionRejected, match="Retry state differs"):
            journal.begin("model:1", "hash", changed)
        retry = {"budget": {"elapsed_seconds": 2.0, "models": 0}, "desk": {"sources": []}}
        assert journal.begin("model:1", "hash", retry) is None
        with pytest.raises(AmbiguousOperation):
            journal.begin("model:1", "hash", retry)
        row = journal._db.execute(
            "SELECT attempt,evidence,evidence_hash,prior_state_type,prior_state,used_at "
            "FROM non_dispatch_resolutions WHERE key='model:1'"
        ).fetchone()
        assert row[0] == 1 and row[2] == proof["evidence_hash"] and row[5]
        assert journal._decode(row[3], row[4]) == before
        journal.complete("model:1", "hash", {"answer": "verified retry"}, retry)
    with Journal(path, {"run": 1}).lease() as reopened:
        assert reopened.begin("model:1", "hash", retry)["result"] == {"answer": "verified retry"}


def test_non_dispatch_resolution_does_not_permit_time_reset_or_other_work(tmp_path):
    path = tmp_path / "research.sqlite"
    before = {"acquisition_budget": {"elapsed_seconds": 4.0, "tools": 0}}
    proof = {"operator": "reviewer", "reference": "log:absence", "basis": "Definitive no-send"}
    with Journal(path, {"run": 1}).lease() as journal:
        journal.begin("acquisition", "hash", before)
        journal.resolve_non_dispatch("acquisition", "hash", proof, verifier=lambda *args: True)
        with pytest.raises(ResolutionRejected):
            journal.begin(
                "acquisition",
                "hash",
                {"acquisition_budget": {"elapsed_seconds": 3.0, "tools": 0}},
            )
        with pytest.raises(AmbiguousOperation):
            journal.begin("other", "hash", {})
        with pytest.raises(AmbiguousOperation):
            journal.save_state({"unsafe": True})
