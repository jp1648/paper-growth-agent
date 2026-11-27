"""Ordinary paid evaluations must reserve against verified credit atomically."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from conftest import AT

from trader.evaluation import fingerprint, run_suite
from trader.models import dec
from trader.spending import DailyBudgetExceeded, DailyReservation
from trader.store import Store

SUITE = "evaluations/baseline-v1.json"


def funded(service):
    service.store.put(
        "confirmed",
        {
            "basis": "provider_confirmed_available_credit",
            "evidence_sha256": "fixture-only",
            "verified_by": "fixture-reviewer",
            "observed_at": AT.isoformat(),
            "expires_at": (AT + timedelta(days=1)).isoformat(),
            "spending_sha256": fingerprint([]),
            "available_usd": "15",
            "buffer_usd": "10",
        },
        "research_funding",
        scope="global",
    )


def test_paid_evaluation_denied_before_model_initialization(env, tmp_path, monkeypatch):
    service, _, _ = env
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-not-a-key")

    def forbidden(*args):
        pytest.fail("Unfunded evaluation initialized provider models")

    monkeypatch.setattr("trader.research_team.DeepResearchTeam.models", forbidden)
    with pytest.raises(DailyBudgetExceeded, match="fresh provider credit"):
        run_suite(service.cfg, SUITE, tmp_path / "denied", "unfunded-eval", "1")
    assert not service.store.db.execute("SELECT 1 FROM documents WHERE kind='research_spend'").fetchone()
    assert service.store.get("unfunded-eval", "evaluation_manifest", scope="global")["status"] == "claimed"


def test_paid_evaluation_checks_effective_cap_and_keeps_funding_provenance(env, tmp_path, monkeypatch):
    service, _, _ = env
    funded(service)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-not-a-key")
    monkeypatch.setattr("trader.spending.now", lambda: AT)
    from trader.paper_dispatch import funding_gate

    checks = []

    def checked(store, cfg, current, **kwargs):
        assert store.db.in_transaction
        checks.append(cfg.research_max_cost_usd)
        return funding_gate(store, cfg, current, **kwargs)

    def stop(*args):
        raise KeyboardInterrupt("offline stop before provider initialization")

    monkeypatch.setattr("trader.paper_dispatch.funding_gate", checked)
    monkeypatch.setattr("trader.research_team.DeepResearchTeam.models", stop)
    with pytest.raises(KeyboardInterrupt):
        run_suite(service.cfg, SUITE, tmp_path / "funded", "funded-eval", "1")
    assert checks == [dec("1")]
    row = service.store.db.execute("SELECT body FROM documents WHERE kind='research_spend'").fetchone()
    record = json.loads(row[0])
    assert record["funding_evidence_sha256"] == "fixture-only"
    assert record["usd"] == "1" and record["unknown_total"] is True


def test_required_funding_serializes_competing_reservations(env, monkeypatch):
    service, _, _ = env
    funded(service)
    service.cfg.research_max_cost_usd = dec(5)
    monkeypatch.setattr("trader.spending.now", lambda: AT)
    barrier = Barrier(2)

    def reserve(index):
        store = Store(service.cfg.database)
        try:
            barrier.wait(timeout=5)
            try:
                DailyReservation(store, service.cfg, f"funded-{index}", require_funding=True)
                return True
            except DailyBudgetExceeded:
                return False
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [1, 2])) == [False, True]
    assert (
        service.store.db.execute("SELECT count(*) FROM documents WHERE kind='research_spend'").fetchone()[0]
        == 1
    )


def test_explicit_testing_evaluation_keeps_existing_override(env, tmp_path, monkeypatch):
    service, _, _ = env
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture-not-a-key")
    service.cfg.research_daily_cost_usd = dec(0)

    def stop(*args):
        raise KeyboardInterrupt("offline stop before provider initialization")

    monkeypatch.setattr("trader.research_team.DeepResearchTeam.models", stop)
    with pytest.raises(KeyboardInterrupt):
        run_suite(service.cfg, SUITE, tmp_path / "testing", "explicit-testing-eval", "1", testing=True)
    row = service.store.db.execute("SELECT body FROM documents WHERE kind='research_spend'").fetchone()
    assert json.loads(row[0])["testing_budget_override"] is True
