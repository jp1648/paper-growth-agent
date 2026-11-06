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
