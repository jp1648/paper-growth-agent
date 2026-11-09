import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import AT

from trader.models import dec, dt
from trader.paper_dispatch import dispatch, fingerprint, source_fingerprint
from trader.paper_schedule import build_file
from trader.runner import Runner


@pytest.fixture
def runtime(env, tmp_path, monkeypatch):
    service, broker, _ = env
    service.store.experiment["ends_at"] = (AT + timedelta(days=120)).isoformat()
    security = service.cfg.universe["ACME"]
    service.cfg.universe = {s: security for s in ("AAPL", "MSFT", "AMZN")}
    service.cfg.research_only_universe = {s: security for s in ("PG", "CAT")}
    service.cfg.research_max_cost_usd = dec(5)
    service.cfg.research_daily_cost_usd = dec(6)
    monkeypatch.setattr("trader.paper_schedule.now", lambda: AT - timedelta(days=1))
    monkeypatch.setattr("trader.paper_dispatch.now", lambda: broker.at)
    source, target = tmp_path / "schedule-input.json", tmp_path / "schedule.json"
    source.write_text(
        json.dumps(
            {
                "study_id": "fixture",
                "start_session": AT.date().isoformat(),
                "calendar": broker.calendar(
                    AT.date().isoformat(), (AT + timedelta(days=110)).date().isoformat()
                ),
            }
        )
    )
    schedule = build_file(source, target)
    calls = []

    def invoke(payload):
        assert service.store.items("paper_slot")
        calls.append(payload)
        return {"decision": {"action": "WATCH"}, "execution": {"status": "watch"}}

    runner = Runner(service, SimpleNamespace(invoke=invoke))
    study = {
        "status": "operator_approved",
        "experiment_id": service.store.scope,
        "config_sha256": fingerprint(service.cfg.model_dump(mode="json")),
        "source_sha256": source_fingerprint(),
        "approved_by": "fixture-only",
        "approved_at": (AT - timedelta(days=1)).isoformat(),
        "acceptance_evidence_sha256": "fixture-not-real-approval",
        "schedule": schedule,
        "schedule_sha256": fingerprint(schedule),
    }
    funding = {
        "basis": "provider_confirmed_available_credit",
        "evidence_sha256": "fixture",
        "verified_by": "fixture",
        "observed_at": AT.isoformat(),
        "expires_at": (AT + timedelta(days=100)).isoformat(),
        "spending_sha256": fingerprint([]),
        "available_usd": "100",
        "buffer_usd": "10",
    }
    service.store.put("confirmed", funding, "research_funding", scope="global")
    return runner, study, broker, calls, funding


def run(runtime):
    runner, study, broker, _, _ = runtime
    day = broker.calendar(broker.at.date().isoformat(), broker.at.date().isoformat())[0]
    return dispatch(runner, study, broker.clock(), day)


def test_scheduled_run_consumed_once_and_journaled(runtime):
    runner, _, _, calls, _ = runtime
    assert "mechanically completed" in run(runtime)["status"]
    assert run(runtime)["status"] == "scheduled slot already consumed"
    assert len(calls) == 1 and calls[0]["run_key"] == "fixture:scheduled:01"
    assert calls[0]["research_only"] is False
    assert (
        len(runner.store.events("paper_slot_claimed"))
        == len(runner.store.events("paper_slot_completed"))
        == 1
    )


def test_failure_after_claim_never_retries(runtime):
    runner, _, _, calls, _ = runtime

    def fail(payload):
        calls.append(payload)
        raise TimeoutError("fixture lost model response")

    runner.graph.invoke = fail
    with pytest.raises(TimeoutError):
        run(runtime)
    assert run(runtime)["status"] == "scheduled slot already consumed"
    assert len(calls) == 1 and len(runner.store.events("paper_slot_failed")) == 1


@pytest.mark.parametrize("boundary,expected", [("before", 0), ("open", 1), ("cutoff", 0)])
def test_claim_window_boundaries(runtime, boundary, expected):
    runner, study, broker, calls, funding = runtime
    slot = study["schedule"]["opportunities"][0]
    broker.at = dt(slot["claim_before"] if boundary == "cutoff" else slot["claim_from"])
    if boundary == "before":
        broker.at -= timedelta(microseconds=1)
    funding["observed_at"] = broker.at.isoformat()
    runner.store.put("confirmed", funding, "research_funding", scope="global")
    run(runtime)
    assert len(calls) == expected
    if boundary == "cutoff":
        assert runner.store.items("paper_slot")[0]["outcome"] == "missed"
        run(runtime)
        assert len(runner.store.items("paper_slot")) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "stale",
        "future",
        "expired",
        "small_credit",
        "small_buffer",
        "changed_spend",
        "unknown",
        "daily",
        "missing",
    ],
)
def test_funding_failures_block_before_claim(runtime, fault):
    runner, _, _, calls, funding = runtime
    if fault == "stale":
        funding["observed_at"] = (AT - timedelta(minutes=31)).isoformat()
    if fault == "future":
        funding["observed_at"] = (AT + timedelta(seconds=1)).isoformat()
    if fault == "expired":
        funding["expires_at"] = AT.isoformat()
    if fault == "small_credit":
        funding["available_usd"] = "14.99"
    if fault == "small_buffer":
        funding["buffer_usd"] = "0"
    if fault in {"changed_spend", "unknown", "daily"}:
        runner.store.put(
            "other",
            {
                "day": AT.date().isoformat(),
                "usd": "2",
                "status": "unknown" if fault == "unknown" else "settled",
            },
            "research_spend",
            scope="global",
        )
        if fault != "changed_spend":
            funding["spending_sha256"] = fingerprint(
                list(
                    runner.store.db.execute(
                        "SELECT key,body FROM documents WHERE scope='global' AND kind='research_spend' ORDER BY key"
                    )
                )
            )
    if fault == "missing":
        funding["basis"] = "unverified"
    runner.store.put("confirmed", funding, "research_funding", scope="global")
    run(runtime)
    assert calls == [] and runner.store.items("paper_slot") == []


@pytest.mark.parametrize(
    "field", ["status", "experiment_id", "source_sha256", "config_sha256", "schedule_sha256", "approved_by"]
)
def test_approval_binding_failures_cannot_run(runtime, field):
    runner, study, _, calls, _ = runtime
    study[field] = "" if field == "approved_by" else "wrong"
    with pytest.raises(ValueError):
        run(runtime)
    assert calls == [] and runner.store.items("paper_slot") == []


def test_pause_preserved_and_missed_slots_retained(runtime):
    runner, study, broker, calls, _ = runtime
    runner.service.kill_path.write_text("fixture pause")
    assert "paused" in run(runtime)["status"]
    broker.at = dt(study["schedule"]["opportunities"][0]["claim_before"])
    run(runtime)
    assert runner.store.items("paper_slot")[0]["outcome"] == "missed"
    assert runner.service.kill_path.exists() and not calls


def test_safety_work_precedes_slot_and_can_displace_it(runtime, monkeypatch):
    runner, _, broker, calls, _ = runtime
    snap = runner.store.get("snapshot")
    snap["positions"] = [{"symbol": "AAPL"}]
    runner.store.put("snapshot", snap)

    def safety(slot, phase, symbols=None, study_id=None):
        assert symbols == {"AAPL"} and not calls
        broker.at += timedelta(hours=6)
        return {"fixture": "safety used remaining window"}

    monkeypatch.setattr(runner, "research_batch", safety)
    assert "closed" in run(runtime)["status"]
    assert not calls
    run(runtime)
    assert runner.store.items("paper_slot")[0]["outcome"] == "missed"


def test_tick_uses_study_dispatch_without_legacy_batch(runtime, monkeypatch):
    runner, study, broker, calls, _ = runtime
    runner.store.put("active", study, "paper_study")
    monkeypatch.setattr("trader.runner.now", lambda: broker.at)
    monkeypatch.setattr(runner, "maintain", lambda: None)
    monkeypatch.setattr(runner, "research_batch", lambda *a, **k: pytest.fail("legacy batch invoked"))
    runner.tick()
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["delay", "unknown_spend"])
def test_final_clock_cannot_invalidate_funding_and_still_start(runtime, monkeypatch, fault):
    runner, study, broker, calls, _ = runtime
    initial = broker.clock()

    def changed():
        if fault == "delay":
            broker.at += timedelta(minutes=31)
        else:
            runner.store.put(
                "other",
                {"status": "unknown", "usd": "5", "day": AT.date().isoformat()},
                "research_spend",
                scope="global",
            )
        return {"timestamp": broker.at.isoformat(), "is_open": True}

    monkeypatch.setattr(broker, "clock", changed)
    result = dispatch(
        runner, study, initial, broker.calendar(AT.date().isoformat(), AT.date().isoformat())[0]
    )
    assert "funding" in result["status"] or "spending changed" in result["status"]
    assert calls == [] and runner.store.items("paper_slot") == []


def test_existing_experiment_must_cover_entire_study(runtime):
    runner, _, _, calls, _ = runtime
    runner.store.experiment["ends_at"] = (AT + timedelta(days=84)).isoformat()
    with pytest.raises(ValueError, match="contain the full"):
        run(runtime)
    assert calls == [] and runner.store.items("paper_slot") == []


@pytest.mark.parametrize(
    "kind", ["research_result", "graph_decision", "research_committed", "research_audit"]
)
def test_cached_artifacts_cannot_pad_new_scheduled_research(runtime, kind):
    runner, study, _, calls, _ = runtime
    runner.store.put(study["schedule"]["opportunities"][0]["id"], {"old": True}, kind)
    with pytest.raises(ValueError, match="already has research"):
        run(runtime)
    assert calls == [] and runner.store.items("paper_slot") == []


def test_budget_reservation_rechecks_funding_for_every_study_investigation(runtime):
    from trader.spending import DailyBudgetExceeded, DailyReservation

    runner, study, _, _, _ = runtime
    runner.store.put("active", study, "paper_study")
    first = DailyReservation(runner.store, runner.cfg, "safety-1")
    assert first.allowance == dec(5)
    with pytest.raises(DailyBudgetExceeded, match="spending changed"):
        DailyReservation(runner.store, runner.cfg, "safety-2")
    first.finish(dec("0.20"))
    with pytest.raises(DailyBudgetExceeded, match="spending changed"):
        DailyReservation(runner.store, runner.cfg, "scheduled")
    assert (
        runner.store.db.execute("SELECT count(*) FROM documents WHERE kind='research_spend'").fetchone()[0]
        == 1
    )


def test_funding_deferral_is_retained_in_missed_receipt(runtime):
    runner, study, broker, calls, funding = runtime
    funding["available_usd"] = "10"
    runner.store.put("confirmed", funding, "research_funding", scope="global")
    run(runtime)
    run(runtime)
    assert len(runner.store.events("paper_slot_deferred")) == 1
    broker.at = dt(study["schedule"]["opportunities"][0]["claim_before"])
    run(runtime)
    missed = runner.store.items("paper_slot")[0]
    assert "confirmed credit" in missed["last_deferral"]["reason"]
    assert not calls


def test_active_study_blocks_unscheduled_manual_graph(env, decision):
    from test_graph_reporting import FixtureTeam

    from trader.research import build_graph

    service, broker, _ = env
    service.store.put("active", {"schedule": {"study_id": "fixture"}}, "paper_study")
    model = FixtureTeam(decision)
    with pytest.raises(RuntimeError, match="study dispatcher"):
        build_graph(service, model).invoke({"symbol": "ACME", "run_key": "manual-outside-study"})
    assert model.calls == broker.submits == 0


@pytest.mark.parametrize("fault", [None, "missing", "missed", "symbol", "hash"])
def test_graph_requires_durable_matching_claim_and_consumes_invocation_once(env, decision, fault):
    from test_graph_reporting import FixtureTeam

    from trader.research import build_graph

    service, broker, _ = env
    service.store.put(
        "active", {"schedule": {"study_id": "fixture"}, "schedule_sha256": "reviewed"}, "paper_study"
    )
    receipt = {
        "symbol": "OTHER" if fault == "symbol" else "ACME",
        "schedule_sha256": "wrong" if fault == "hash" else "reviewed",
    }
    if fault == "missed":
        receipt["outcome"] = "missed"
    if fault != "missing":
        service.store.claim_paper_slot("fixture", "scheduled", receipt)
    model = FixtureTeam(decision)
    graph = build_graph(service, model)
    payload = {
        "symbol": "ACME",
        "run_key": "scheduled",
        "research_only": True,
        "study_id": "fixture",
        "study_stratum": "scheduled",
    }
    if fault:
        with pytest.raises(RuntimeError, match="durable slot claim"):
            graph.invoke(payload)
        assert model.calls == 0
    else:
        graph.invoke(payload)
        with pytest.raises(RuntimeError, match="invocation already consumed"):
            graph.invoke(payload)
        assert model.calls == 1
        assert len(service.store.items("paper_invocation")) == 1
    assert broker.submits == 0
