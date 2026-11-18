import copy
import json
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
from test_research_team import Evidence as RealEvidence
from test_research_team import TeamModel

from trader.config import Settings
from trader.models import now
from trader.paper_dispatch import fingerprint
from trader.research_journal import Journal
from trader.research_stages import ResearchStages
from trader.research_team import ResearchStageYield
from trader.research_workflow import StageAmbiguous, StageBudgetError, StageConfigurationError
from trader.store import Store


@pytest.fixture(autouse=True)
def approved_funding(monkeypatch):
    monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *args, **kwargs: None)


class Evidence:
    def __init__(self, counts):
        self.counts = counts

    def get(self, symbol, fresh=False):
        self.counts["evidence"] += 1
        return {"symbol": symbol, "sources": [{"id": "source-one", "content": "Original source"}]}


class X:
    def __init__(self, cfg, store):
        pass

    def snapshot(self, symbol):
        return []


class Desk:
    def __init__(
        self,
        cfg,
        provider,
        context,
        evidence,
        budget,
        model,
        *,
        checkpoint_factory=None,
        defer_correction=False,
        counts=None,
        correction_requested=False,
    ):
        self.context, self.budget = copy.deepcopy(context), budget
        self.sources = {s["id"]: s for s in evidence["sources"]}
        self.companies = {context["symbol"]: evidence}
        self.reports, self.report_times, self.calculations = {}, {}, {}
        self.continuation, self.correction = {}, None
        self.counts, self.correction_requested = counts, correction_requested

    def checkpoint(self):
        return {
            "version": 1,
            "context": copy.deepcopy(self.context),
            "desk": copy.deepcopy(
                {
                    "sources": self.sources,
                    "companies": self.companies,
                    "reports": self.reports,
                    "report_times": self.report_times,
                    "calculations": self.calculations,
                    "continuation": self.continuation,
                    "correction": self.correction,
                }
            ),
            "budget": self.budget.checkpoint(),
        }

    def restore(self, saved):
        assert saved["context"] == self.context
        self.budget.restore(saved["budget"])
        for key, value in saved["desk"].items():
            setattr(self, key, copy.deepcopy(value))

    def specialist(self, role, task):
        self.counts[role] = self.counts.get(role, 0) + 1
        self.reports[role] = {"evidence_ids": ["source-one"]}
        self.report_times[role] = now().isoformat()
        return {"messages": []}

    def run_lead(self):
        self.counts["lead"] = self.counts.get("lead", 0) + 1
        if self.correction_requested and self.correction is None:
            self.correction = {"invalidated": ["valuation", "adversarial"]}
            for role in self.correction["invalidated"]:
                self.reports.pop(role)
            raise ResearchStageYield()
        decision = {
            "symbol": self.context["symbol"],
            "action": "WATCH",
            "quantity": "0",
            "limit_price": "0",
            "business_attraction": "Uncertain",
            "price_attraction": "Uncertain",
            "why_now": "Await evidence",
            "catalysts": ["Review"],
            "thesis_development": "Review later",
            "holding_period_days": 0,
            "next_review": (now() + timedelta(days=1)).isoformat(),
            "add_if": "Evidence improves",
            "reduce_if": "Risk grows",
            "exit_if": "Thesis fails",
            "exit_reason": "none",
            "evidence_ids": ["source-one"],
            "assumptions": [],
            "expected_outcome": "Remain on watch",
            "confidence": "0.5",
        }
        return {
            "decision": decision,
            "research": self.reports["business"],
            "valuation": self.reports["valuation"],
            "catalysts": self.reports["catalysts"],
            "critique": {**self.reports["adversarial"], "unresolved_material_gaps": False},
            "evidence": {"symbol": self.context["symbol"], "sources": list(self.sources.values())},
        }

    def arithmetic_records(self):
        return []


def setup(tmp_path, *, correction=False):
    db = tmp_path / "ledger.sqlite"
    cfg = Settings(database=str(db))
    store = Store(db)
    store.put("active", {"id": "experiment-one"}, scope="global")
    store.db.close()
    context = {"run_key": "durable-one", "symbol": "ACME", "portfolio": {}, "research_only": True}
    binding = {
        "run_key": context["run_key"],
        "experiment_id": "experiment-one",
        "config_sha256": fingerprint(cfg),
        "code_sha256": "a" * 64,
        "context_sha256": fingerprint(context),
    }
    counts = {"evidence": 0}
    engine = ResearchStages(
        cfg,
        db,
        lambda store: Evidence(counts),
        context,
        {},
        tmp_path / "private",
        binding,
        x_factory=X,
        desk_factory=lambda *args, **kwargs: Desk(
            *args, **kwargs, counts=counts, correction_requested=correction
        ),
    )
    return engine, counts, db


def call(engine, stage):
    return engine({"run_key": "durable-one", "manifest_hash": engine.manifest_hash, "stage": stage})


def test_full_stages_reserve_once_commit_research_only_and_return_saved_receipts(tmp_path):
    engine, counts, db = setup(tmp_path)
    stages = ["acquisition", "business", "catalysts", "valuation", "adversarial", "lead", "commit"]
    receipts = [call(engine, stage) for stage in stages]
    assert [r["status"] for r in receipts] == ["next"] * 6 + ["complete"]
    assert counts == {
        "evidence": 1,
        "business": 1,
        "catalysts": 1,
        "valuation": 1,
        "adversarial": 1,
        "lead": 1,
    }
    assert call(engine, "business") == receipts[1]
    assert call(engine, "commit") == receipts[-1]
    assert counts["business"] == 1
    store = Store(db)
    assert store.get("durable-one", "research_result")["decision"]["action"] == "WATCH"
    assert store.get("durable-one", "research_audit")["tool_calls"] == 4
    assert store.get("ACME", "thesis") is None
    assert store.get("durable-one", "graph_decision") is None
    spending = [x for x in store.items("research_spend") if x["run_key"] == "durable-one"]
    assert not spending  # Spend rows are global, not experiment documents.
    global_spend = store.db.execute(
        "SELECT body FROM documents WHERE scope='global' AND kind='research_spend'"
    ).fetchall()
    assert len(global_spend) == 1
    store.db.close()


def test_receipt_gap_restarts_without_second_delegation_or_specialist(tmp_path, monkeypatch):
    engine, counts, db = setup(tmp_path)
    call(engine, "acquisition")
    original = engine._save_receipt
    failed = {"once": True}

    def lose_receipt(store, stage, receipt):
        if stage == "business" and failed["once"]:
            failed["once"] = False
            raise RuntimeError("crash before receipt")
        return original(store, stage, receipt)

    monkeypatch.setattr(engine, "_save_receipt", lose_receipt)
    with pytest.raises(RuntimeError, match="crash before receipt"):
        call(engine, "business")
    assert call(engine, "business")["next_stage"] == "catalysts"
    assert counts["business"] == 1
    store = Store(db)
    assert (
        store.db.execute(
            "SELECT count(*) FROM documents WHERE scope='global' AND kind='research_spend'"
        ).fetchone()[0]
        == 1
    )
    store.db.close()


def test_correction_stages_rebuild_only_invalidated_roles(tmp_path):
    engine, counts, _ = setup(tmp_path, correction=True)
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial"):
        call(engine, stage)
    assert call(engine, "lead")["next_stage"] == "correction:valuation"
    assert call(engine, "correction:valuation")["next_stage"] == "correction:adversarial"
    assert call(engine, "correction:adversarial")["next_stage"] == "correction:lead"
    assert call(engine, "correction:lead")["next_stage"] == "commit"
    assert call(engine, "commit")["status"] == "complete"
    assert counts["valuation"] == counts["adversarial"] == counts["lead"] == 2
    assert counts["business"] == counts["catalysts"] == 1


def test_settled_commit_recovers_identical_receipt_after_response_loss(tmp_path, monkeypatch):
    engine, counts, db = setup(tmp_path)
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial", "lead"):
        call(engine, stage)
    original = engine._save_receipt
    lost = {"once": True}

    def lose_response(store, stage, receipt):
        if stage == "commit" and lost["once"]:
            lost["once"] = False
            raise RuntimeError("response lost after settlement")
        return original(store, stage, receipt)

    monkeypatch.setattr(engine, "_save_receipt", lose_response)
    with pytest.raises(RuntimeError, match="response lost after settlement"):
        call(engine, "commit")
    assert call(engine, "commit")["status"] == "complete"
    assert counts["lead"] == 1
    store = Store(db)
    assert store.get("durable-one", "research_result")["decision"]["action"] == "WATCH"
    assert (
        store.db.execute(
            "SELECT count(*) FROM documents WHERE scope='global' AND kind='research_spend'"
        ).fetchone()[0]
        == 1
    )
    store.db.close()


def test_settled_commit_rejects_only_completion_timestamp_tamper(tmp_path, monkeypatch):
    engine, _, db = setup(tmp_path)
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial", "lead"):
        call(engine, stage)
    original = engine._save_receipt

    def lose_commit_receipt(store, stage, receipt):
        if stage == "commit":
            raise RuntimeError("receipt lost")
        return original(store, stage, receipt)

    monkeypatch.setattr(engine, "_save_receipt", lose_commit_receipt)
    with pytest.raises(RuntimeError, match="receipt lost"):
        call(engine, "commit")
    store = Store(db)
    result = store.get("durable-one", "research_result")
    result["research_completed_at"] = "2026-10-22T15:00:00+00:00"
    store.put("durable-one", result, "research_result")
    store.db.close()
    with pytest.raises(StageConfigurationError, match="Settled commit"):
        call(engine, "commit")


def test_request_binding_and_pending_acquisition_fail_closed(tmp_path):
    engine, counts, _ = setup(tmp_path)
    with pytest.raises(StageConfigurationError):
        engine({"run_key": "durable-one", "manifest_hash": "0" * 64, "stage": "acquisition"})
    with pytest.raises(StageConfigurationError, match="predecessor"):
        call(engine, "valuation")

    def broken_provider(store):
        class Broken:
            def get(self, symbol, fresh=False):
                counts["evidence"] += 1
                raise RuntimeError("unknown external outcome")

        return Broken()

    engine.provider_factory = broken_provider
    with pytest.raises(RuntimeError, match="unknown external outcome"):
        call(engine, "acquisition")
    with pytest.raises(StageAmbiguous):
        call(engine, "acquisition")
    assert counts["evidence"] == 1


def test_pre_dispatch_can_stop_before_acquisition_or_delegation(tmp_path):
    engine, counts, _ = setup(tmp_path)
    engine.before_dispatch = lambda: (_ for _ in ()).throw(StageBudgetError("paused"))
    with pytest.raises(StageBudgetError, match="paused"):
        call(engine, "acquisition")
    assert counts["evidence"] == 0
    engine.before_dispatch = None
    call(engine, "acquisition")
    engine.before_dispatch = lambda: (_ for _ in ()).throw(StageBudgetError("paused"))
    with pytest.raises(StageBudgetError, match="paused"):
        call(engine, "business")
    assert counts.get("business", 0) == 0


def test_real_research_desk_and_deepagents_cross_stage_checkpoints(tmp_path):
    db = tmp_path / "ledger.sqlite"
    cfg = Settings(database=str(db), input_cost_per_million="1", output_cost_per_million="1")
    store = Store(db)
    store.put("active", {"id": "experiment-one"}, scope="global")
    store.db.close()
    context = {"run_key": "real-stage", "symbol": "ACME", "portfolio": {}, "research_only": True}
    binding = {
        "run_key": context["run_key"],
        "experiment_id": "experiment-one",
        "config_sha256": fingerprint(cfg),
        "code_sha256": "a" * 64,
        "context_sha256": fingerprint(context),
    }
    decision = {
        "symbol": "ACME",
        "action": "WATCH",
        "quantity": "0",
        "limit_price": "0",
        "business_attraction": "Uncertain",
        "price_attraction": "Uncertain",
        "why_now": "Await evidence",
        "catalysts": ["Review"],
        "thesis_development": "Review later",
        "holding_period_days": 0,
        "next_review": (now() + timedelta(days=1)).isoformat(),
        "add_if": "Evidence improves",
        "reduce_if": "Risk grows",
        "exit_if": "Thesis fails",
        "exit_reason": "none",
        "evidence_ids": ["fixture-sec"],
        "assumptions": [],
        "expected_outcome": "Remain on watch",
        "confidence": "0.5",
    }
    model = TeamModel(decision=decision)
    engine = ResearchStages(
        cfg,
        db,
        lambda store: RealEvidence(),
        context,
        model,
        tmp_path / "private",
        binding,
        x_factory=X,
    )
    request = lambda stage: {"run_key": "real-stage", "manifest_hash": engine.manifest_hash, "stage": stage}
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial", "lead", "commit"):
        engine(request(stage))
    saved = Store(db)
    audit = saved.get("real-stage", "research_audit")
    assert audit["model_calls"] == len(model.calls)
    assert audit["tool_calls"] >= 4
    assert set(audit["reports"]) == {"business", "catalysts", "valuation", "adversarial"}
    assert saved.get("real-stage", "research_result")["decision"]["action"] == "WATCH"
    assert saved.get("real-stage", "research_audit")["events"]
    saved.db.close()


def test_real_lead_correction_yield_and_original_graph_resume(tmp_path):
    class CorrectingModel(TeamModel):
        _asked: bool = PrivateAttr(default=False)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if kwargs["response_format"]["json_schema"]["name"] == "Decision" and not self._asked:
                context = json.loads(messages[1].content)
                self._asked = True
                msg = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "request_correction",
                            "args": {
                                "role": "valuation",
                                "report_sha256": context["report_sha256"]["valuation"],
                                "reason": "Recheck the economic normalization treatment",
                            },
                            "id": "correct-valuation",
                            "type": "tool_call",
                        }
                    ],
                )
                msg.usage_metadata = {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}
                self.calls.append("Decision")
                return ChatResult(generations=[ChatGeneration(message=msg)])
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    db = tmp_path / "ledger.sqlite"
    cfg = Settings(database=str(db), input_cost_per_million="1", output_cost_per_million="1")
    store = Store(db)
    store.put("active", {"id": "experiment-one"}, scope="global")
    store.db.close()
    context = {"run_key": "real-correction", "symbol": "ACME", "portfolio": {}, "research_only": True}
    binding = {
        "run_key": context["run_key"],
        "experiment_id": "experiment-one",
        "config_sha256": fingerprint(cfg),
        "code_sha256": "a" * 64,
        "context_sha256": fingerprint(context),
    }
    decision = {
        "symbol": "ACME",
        "action": "WATCH",
        "quantity": "0",
        "limit_price": "0",
        "business_attraction": "Uncertain",
        "price_attraction": "Uncertain",
        "why_now": "Await evidence",
        "catalysts": ["Review"],
        "thesis_development": "Review later",
        "holding_period_days": 0,
        "next_review": (now() + timedelta(days=1)).isoformat(),
        "add_if": "Evidence improves",
        "reduce_if": "Risk grows",
        "exit_if": "Thesis fails",
        "exit_reason": "none",
        "evidence_ids": ["fixture-sec"],
        "assumptions": [],
        "expected_outcome": "Remain on watch",
        "confidence": "0.5",
    }
    model = CorrectingModel(decision=decision)
    engine = ResearchStages(
        cfg,
        db,
        lambda store: RealEvidence(),
        context,
        model,
        tmp_path / "private",
        binding,
        x_factory=X,
    )
    request = lambda stage: {
        "run_key": "real-correction",
        "manifest_hash": engine.manifest_hash,
        "stage": stage,
    }
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial"):
        engine(request(stage))
    assert engine(request("lead"))["next_stage"] == "correction:valuation"
    assert engine(request("correction:valuation"))["next_stage"] == "correction:adversarial"
    assert engine(request("correction:adversarial"))["next_stage"] == "correction:lead"
    assert engine(request("correction:lead"))["next_stage"] == "commit"
    assert engine(request("commit"))["status"] == "complete"
    saved = Store(db)
    audit = saved.get("real-correction", "research_audit")
    assert audit["tool_calls"] >= 8
    assert audit["reports"]["valuation"]["calculation_id"]
    assert model.calls.count("Decision") == 2
    saved.db.close()


def test_unknown_usage_blocks_complete_receipt(tmp_path):
    engine, _, db = setup(tmp_path)
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial", "lead"):
        call(engine, stage)
    path = tmp_path / "private" / "operations.sqlite"
    with Journal(path, engine.binding).lease() as journal:
        saved = journal.load_state()
        saved["budget"]["models"] = 1
        journal.save_state(saved)
    with pytest.raises(StageAmbiguous, match="Unknown model usage"):
        call(engine, "commit")
    store = Store(db)
    assert store.get(engine._receipt_ref("commit"), "research_stage_receipt") is None
    assert store.get("durable-one", "research_result") is None
    assert (
        store.db.execute(
            "SELECT json_extract(body, '$.status') FROM documents "
            "WHERE scope='global' AND kind='research_spend'"
        ).fetchone()[0]
        == "reserved"
    )
    store.db.close()
    with pytest.raises(StageAmbiguous, match="Unknown model usage"):
        call(engine, "commit")


def test_settled_amount_must_match_audit_before_receipt_recovery(tmp_path, monkeypatch):
    engine, _, db = setup(tmp_path)
    for stage in ("acquisition", "business", "catalysts", "valuation", "adversarial", "lead"):
        call(engine, stage)
    original = engine._save_receipt

    def lose_commit_receipt(store, stage, receipt):
        if stage == "commit":
            raise RuntimeError("response lost")
        return original(store, stage, receipt)

    monkeypatch.setattr(engine, "_save_receipt", lose_commit_receipt)
    with pytest.raises(RuntimeError, match="response lost"):
        call(engine, "commit")
    store = Store(db)
    with store.db:
        store.db.execute(
            "UPDATE documents SET body=json_set(body, '$.usd', '1') "
            "WHERE scope='global' AND kind='research_spend'"
        )
    store.db.close()
    with pytest.raises(StageConfigurationError, match="Settled spend differs"):
        call(engine, "commit")


def test_initial_funding_denial_creates_no_reservation(tmp_path, monkeypatch):
    engine, counts, db = setup(tmp_path)
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *args, **kwargs: "No authority")
    with pytest.raises(StageBudgetError, match="No authority"):
        call(engine, "acquisition")
    assert counts["evidence"] == 0
    store = Store(db)
    assert (
        store.db.execute(
            "SELECT count(*) FROM documents WHERE scope='global' AND kind='research_spend'"
        ).fetchone()[0]
        == 0
    )
    store.db.close()
