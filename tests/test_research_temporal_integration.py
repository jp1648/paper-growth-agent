import asyncio
from datetime import timedelta
from threading import Lock

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from test_research_stages import X
from test_research_team import Evidence, TeamModel

from trader.config import Settings
from trader.models import now
from trader.paper_dispatch import fingerprint
from trader.research_stages import ResearchStages
from trader.research_workflow import (
    ResearchActivities,
    ResearchWorkflow,
    StageAmbiguous,
    start_research_workflow,
)
from trader.store import Store


async def wait_for(handle, stage, state):
    for _ in range(100):
        status = await handle.query(ResearchWorkflow.status)
        if status["stage"] == stage and status["state"] == state:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"Workflow did not reach {stage}/{state}")


def test_temporal_worker_restart_replays_saved_stage_receipt(tmp_path, monkeypatch):
    async def exercise():
        monkeypatch.setattr("trader.research_stages.funding_gate", lambda *args, **kwargs: None)
        monkeypatch.setattr("trader.paper_dispatch.funding_gate", lambda *args, **kwargs: None)
        db = tmp_path / "ledger.sqlite"
        cfg = Settings(database=str(db), input_cost_per_million="1", output_cost_per_million="1")
        store = Store(db)
        store.put("active", {"id": "experiment-one"}, scope="global")
        store.db.close()
        context = {"run_key": "temporal-real", "symbol": "ACME", "portfolio": {}, "research_only": True}
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
        counts, lock = {"provider": 0}, Lock()

        class CountingEvidence(Evidence):
            def get(self, symbol, fresh=False):
                with lock:
                    counts["provider"] += 1
                return super().get(symbol, fresh=fresh)

        engine = ResearchStages(
            cfg,
            db,
            lambda store: CountingEvidence(),
            context,
            model,
            tmp_path / "private",
            binding,
            x_factory=X,
        )
        lost = {"once": True}

        def handler(request):
            receipt = engine(request)
            if request["stage"] == "business" and lost["once"]:
                lost["once"] = False
                raise StageAmbiguous("fixture response lost after receipt commit")
            return receipt

        async with await WorkflowEnvironment.start_local() as env:
            activities = ResearchActivities(handler)
            async with Worker(
                env.client,
                task_queue="research-stage-restart",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                handle = await start_research_workflow(
                    env.client, context["run_key"], engine.manifest_hash, "research-stage-restart"
                )
                await wait_for(handle, "business", "blocked")
                calls_before = len(model.calls)
                business_calls_before = model.calls.count("Research")
                assert counts["provider"] == 1
            async with Worker(
                env.client,
                task_queue="research-stage-restart",
                workflows=[ResearchWorkflow],
                activities=[activities.research_stage],
            ):
                await handle.signal(ResearchWorkflow.resume)
                completed = await asyncio.wait_for(handle.result(), timeout=20)
                assert completed["state"] == "complete"
        saved = Store(db)
        audit = saved.get(context["run_key"], "research_audit")
        assert audit["model_calls"] == len(model.calls)
        assert len(model.calls) > calls_before
        assert model.calls.count("Research") == business_calls_before
        assert counts["provider"] == 1
        assert saved.get(context["run_key"], "research_result")["decision"]["action"] == "WATCH"
        assert saved.get("ACME", "thesis") is None
        assert saved.get(context["run_key"], "graph_decision") is None
        assert saved.get(context["run_key"], "order_intent") is None
        assert (
            saved.db.execute(
                "SELECT count(*) FROM documents WHERE scope='global' AND kind='research_spend'"
            ).fetchone()[0]
            == 1
        )
        saved.db.close()

    asyncio.run(exercise())
