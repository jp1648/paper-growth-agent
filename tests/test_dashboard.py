import pytest

from trader.dashboard import publish_snapshot


@pytest.mark.parametrize("status", ["complete", "failed", "started"])
def test_snapshot_shows_only_completed_supervised_decisions(env, decision, status):
    service, broker, _ = env
    service.reconcile()
    service.store.put("supervised", {"stopped": status == "failed"}, "research_audit")
    service.store.put("supervised", {"mode": "research", "status": status}, "acceptance")
    saved = decision.model_dump(mode="json")
    service.store.put("supervised", {"decision": saved}, "acceptance_research")
    run = publish_snapshot(service)["runs"][0]
    assert run["decision"] == (saved if status == "complete" else None)
    assert (run["status"] == "accepted") == (status == "complete")
    assert broker.submits == 0


def test_snapshot_preserves_failed_research_and_omits_account_identifiers(env):
    service, broker, _ = env
    service.reconcile()
    service.store.put(
        "failed-run",
        {
            "stopped": True,
            "estimated_usd": "1.23",
            "reports": {"business": {"evidence_ids": ["sec:1"]}},
            "sources": [{"id": "sec:1", "url": "https://www.sec.gov/example", "data": "large raw source"}],
        },
        "research_audit",
    )
    snapshot = publish_snapshot(service)
    assert snapshot["runs"][0]["status"] == "failed"
    assert snapshot["runs"][0]["decision"] is None
    assert snapshot["runs"][0]["sources"] == [{"id": "sec:1", "url": "https://www.sec.gov/example"}]
    assert "account_id" not in snapshot and "experiment" not in snapshot["portfolio"]
    assert broker.submits == 0
    assert snapshot["portfolio"]["total_pnl"] == 0


def test_spend_breakdown_separates_models_unknowns_and_data_requests(env):
    from trader.dashboard import spend_breakdown
    from trader.models import dec

    service, _, _ = env
    store = service.store
    store.log("x_attempt", {"operation_id": "x1"})
    store.log("x_attempt", {"operation_id": "x2"})
    store.log("x_result", {"operation_id": "x1", "cost_usd": None})
    store.log("flovia_x_attempt", {"operation_id": "f1"})
    store.log("data_cost", {"source": "Example data", "usd": "0.03"})
    models = [
        {"model": "astra", "estimated_usd": "0.1"},
        {"model": "astra", "estimated_usd": "0.2"},
        {"model": "astra", "estimated_usd": None},
        {"model": "sol", "estimated_usd": "0"},
    ]
    rows = {r["source"]: r for r in spend_breakdown(store, models)}
    assert rows["OpenAI · astra"]["known_usd"] == dec("0.3")
    assert rows["OpenAI · astra"]["requests"] == 3
    assert rows["OpenAI · astra"]["unknown_requests"] == 1
    assert rows["OpenAI · sol"]["known_usd"] == 0
    assert rows["X API"]["known_usd"] is None
    assert rows["X API"]["requests"] == rows["X API"]["unknown_requests"] == 2
    assert rows["Flovia (legacy)"]["known_usd"] is None
    assert rows["Example data"]["known_usd"] == dec("0.03")
    assert sum(r["known_usd"] or dec(0) for r in rows.values()) == dec("0.33")
