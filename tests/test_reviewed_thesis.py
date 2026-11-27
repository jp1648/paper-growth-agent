import copy
import importlib.util
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import AT

from trader.paper_dispatch import fingerprint
from trader.research import commit_research
from trader.research_client import TemporalResearchClient

spec = importlib.util.spec_from_file_location(
    "reviewed_acceptance", Path(__file__).parents[1] / "deploy/acceptance.py"
)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


@pytest.fixture
def accepted(env, decision, monkeypatch):
    service, _, data = env
    store = service.store
    service.kill_path.write_text("preserve this pause")
    service.cfg = service.cfg.model_copy(update={"temporal_address": "unused"})
    monkeypatch.setattr(acceptance, "now", lambda: AT)
    refs = {"evidence_ids": ["fixture-sec-1"]}
    reports = {
        "business": {
            **refs,
            "business_quality": "supported",
            "growth_evidence": "supported",
            "financial_health": "supported",
            "contrary_evidence": [],
            "missing_information": [],
        },
        "catalysts": {
            **refs,
            "developments": [],
            "catalyst_timeline": [],
            "why_now": "review",
            "thesis_development": "stable",
            "monitoring_triggers": [],
            "requires_deeper_review": False,
            "missing_information": [],
        },
        "valuation": {
            **refs,
            "calculation_id": "calc",
            "normalization_review": {
                **refs,
                "cash_flow_basis": "fixture",
                "debt_and_cash": "fixture",
                "dilution_and_sbc": "fixture",
                "nonrecurring_items": "fixture",
                "unresolved_material_gaps": False,
            },
            "bear_value": "70",
            "base_value": "100",
            "bull_value": "120",
            "method": "fixture",
            "assumptions": [],
            "sensitivity": "fixture",
            "margin_of_safety": "fixture",
        },
        "adversarial": {
            **refs,
            "objections": [],
            "thesis_breakers": [],
            "unresolved_material_gaps": False,
            "conclusion": "complete fixture",
        },
    }
    result = {
        "decision": decision.model_copy(update={"action": "WATCH", "quantity": acceptance.D(0)}).model_dump(
            mode="json"
        ),
        "team_reports": reports,
        "research": reports["business"],
        "valuation": reports["valuation"],
        "catalysts": reports["catalysts"],
        "critique": reports["adversarial"],
        "research_completed_at": AT.isoformat(),
        "calculations": {"calc": {"inputs": "synthetic fixture"}},
        "quote": data.quote("ACME").model_dump(mode="json"),
        "evidence": {"sources": [{"id": "fixture-sec-1", "content": "fixture"}]},
    }
    prior = {"run_key": "old", "reviewed_at": (AT - timedelta(days=1)).isoformat()}
    context = {
        "run_key": "new",
        "symbol": "ACME",
        "research_only": True,
        "portfolio": {"prior_thesis": prior},
    }
    reference = {"run_key": "new", "manifest_hash": "a" * 64}
    store.put("ACME", prior, "thesis")
    store.put(
        "new",
        {"status": "complete", "mode": "research", "durable": True, "symbol": "ACME", "context": context},
        "acceptance",
    )
    store.put("new", result, "acceptance_research")
    store.put("new", {"context": {**context, "runtime_control": {}}}, "research_request")
    store.put("new", reference, "research_workflow")
    store.put(
        "research_stage_receipt:new:commit", {"receipt": {"status": "complete"}}, "research_stage_receipt"
    )
    review = {
        "experiment_id": store.scope,
        "run_key": "new",
        "symbol": "ACME",
        "result_sha256": fingerprint(result),
        "reference_sha256": fingerprint(reference),
        "prior_thesis_sha256": fingerprint(prior),
        "verdict": "accepted_complete_research",
        "research_complete": True,
        "independent": True,
        "reviewed_by": "independent-fixture-reviewer",
        "rationale": "Fixture only",
        "reviewed_at": AT.isoformat(),
    }

    def poll(self, ref):
        assert ref == reference
        return result

    monkeypatch.setattr(TemporalResearchClient, "poll", poll)
    monkeypatch.setattr(service, "execute", lambda *a: pytest.fail("order execution"))
    monkeypatch.setattr(service, "reconcile", lambda *a: pytest.fail("broker mutation"))
    return service, context, result, review


def test_commit_reviewed_thesis_atomic_idempotent_and_paused(accepted):
    service, _, result, review = accepted
    pause = service.kill_path.stat().st_mtime_ns
    assert acceptance.commit_reviewed_research(service, "ACME", "new", review)["status"] == "committed"
    thesis = service.store.get("ACME", "thesis")
    assert thesis["run_key"] == "new"
    assert thesis["reviewed_at"] == result["research_completed_at"]
    assert thesis["independent_review"] == review and thesis["research_only"] is True
    assert (
        acceptance.commit_reviewed_research(service, "ACME", "new", review)["status"] == "already_committed"
    )
    assert len(service.store.events("research_decision")) == 1
    assert not service.store.items("intent") and not service.store.items("order")
    assert service.kill_path.stat().st_mtime_ns == pause


@pytest.mark.parametrize(
    "field,value",
    [
        ("verdict", "changes_required"),
        ("research_complete", False),
        ("independent", False),
        ("reviewed_by", ""),
        ("result_sha256", "b" * 64),
        ("experiment_id", "wrong"),
        ("symbol", "OTHER"),
        ("reference_sha256", "b" * 64),
        ("reviewed_at", (AT - timedelta(days=1)).isoformat()),
    ],
)
def test_invalid_review_cannot_commit(accepted, field, value):
    service, _, _, review = accepted
    with pytest.raises(ValueError):
        acceptance.commit_reviewed_research(service, "ACME", "new", {**review, field: value})
    assert service.store.get("new", "research_committed") is None
    assert service.store.get("ACME", "thesis")["run_key"] == "old"


@pytest.mark.parametrize(
    "failure",
    [
        "report",
        "citation",
        "timestamp",
        "projection",
        "gaps",
        "stale",
        "prior",
        "frozen",
        "reference",
        "normalization",
        "normalization_citation",
        "valuation",
    ],
)
def test_invalid_result_or_context_cannot_commit(accepted, monkeypatch, failure):
    service, _, result, review = accepted
    altered = copy.deepcopy(result)
    if failure == "normalization":
        altered["team_reports"]["valuation"]["normalization_review"]["unresolved_material_gaps"] = True
    elif failure == "normalization_citation":
        altered["team_reports"]["valuation"]["normalization_review"]["evidence_ids"] = ["unknown"]
    elif failure == "valuation":
        altered["team_reports"]["valuation"]["calculation_id"] = None
    elif failure == "report":
        del altered["team_reports"]["business"]
    elif failure == "citation":
        altered["team_reports"]["business"]["evidence_ids"] = ["unknown"]
    elif failure == "timestamp":
        del altered["research_completed_at"]
    elif failure == "projection":
        altered["research"] = {"different": True}
    elif failure == "gaps":
        altered["team_reports"]["adversarial"]["unresolved_material_gaps"] = True
    elif failure == "stale":
        altered["research_completed_at"] = (AT - timedelta(days=100)).isoformat()
    elif failure == "prior":
        service.store.put(
            "ACME", {"run_key": "newer", "reviewed_at": (AT + timedelta(hours=1)).isoformat()}, "thesis"
        )
    elif failure == "frozen":
        service.store.put("new", {"context": {}}, "research_request")
    elif failure == "reference":

        def invalid(*args):
            raise ValueError("Invalid durable reference")

        monkeypatch.setattr(TemporalResearchClient, "poll", invalid)
    service.store.put("new", altered, "acceptance_research")
    review["result_sha256"] = fingerprint(altered)
    monkeypatch.setattr(
        TemporalResearchClient, "poll", lambda *a: altered
    ) if failure != "reference" else None
    with pytest.raises((ValueError, KeyError)):
        acceptance.commit_reviewed_research(service, "ACME", "new", review)
    assert service.store.get("new", "research_committed") is None


def test_durable_result_must_match_reviewed_acceptance(accepted, monkeypatch):
    service, _, _, review = accepted
    monkeypatch.setattr(TemporalResearchClient, "poll", lambda *a: {"different": True})
    with pytest.raises(ValueError, match="differs from verified"):
        acceptance.commit_reviewed_research(service, "ACME", "new", review)


def test_missing_commit_receipt_never_polls_or_starts(accepted, monkeypatch):
    service, _, _, review = accepted
    with service.store.db:
        service.store.db.execute("DELETE FROM documents WHERE kind='research_stage_receipt'")
    monkeypatch.setattr(TemporalResearchClient, "poll", lambda *a: pytest.fail("poll without receipt"))
    with pytest.raises(ValueError, match="commit receipt"):
        acceptance.commit_reviewed_research(service, "ACME", "new", review)


def test_commit_rolls_back_all_documents_on_journal_failure(accepted):
    service, _, _, review = accepted
    store = service.store
    store.db.execute(
        "CREATE TEMP TRIGGER fail_commit BEFORE INSERT ON journal WHEN NEW.kind='research_decision' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        acceptance.commit_reviewed_research(service, "ACME", "new", review)
    for kind in ("graph_decision", "research_committed", "acceptance_review"):
        assert store.get("new", kind) is None
    assert store.get("ACME", "thesis")["run_key"] == "old"
    assert not store.events("research_decision")


def test_shared_commit_rejects_conflicting_replay(accepted):
    service, context, result, review = accepted
    acceptance.commit_reviewed_research(service, "ACME", "new", review)
    altered = copy.deepcopy(result)
    altered["decision"]["rationale"] = "changed"
    with pytest.raises(ValueError, match="conflicts"):
        commit_research(service.store, context, altered, review=review)


def test_legacy_hashless_marker_fails_closed_without_writes(accepted):
    service, context, result, _ = accepted
    store = service.store
    store.put("new", {"at": AT.isoformat()}, "research_committed")
    before = list(store.db.execute("SELECT * FROM documents ORDER BY scope,kind,key"))
    journal = list(store.db.execute("SELECT * FROM journal"))
    with pytest.raises(ValueError, match="conflicts"):
        commit_research(store, context, result)
    assert list(store.db.execute("SELECT * FROM documents ORDER BY scope,kind,key")) == before
    assert list(store.db.execute("SELECT * FROM journal")) == journal


def test_ordinary_shared_commit_replay_and_tamper_detection(accepted):
    service, context, result, _ = accepted
    assert commit_research(service.store, context, result) is True
    before = list(service.store.db.execute("SELECT * FROM documents ORDER BY scope,kind,key"))
    assert commit_research(service.store, context, result) is False
    altered = copy.deepcopy(result)
    altered["research_completed_at"] = (AT - timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="conflicts"):
        commit_research(service.store, context, altered)
    assert list(service.store.db.execute("SELECT * FROM documents ORDER BY scope,kind,key")) == before
    assert len(service.store.events("research_decision")) == 1
