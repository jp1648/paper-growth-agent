"""Release-blocking regressions; all broker and model traffic uses local fixtures."""

from datetime import timedelta

import pytest
from conftest import AT
from test_graph_reporting import FixtureTeam

from trader.models import dec
from trader.research import build_graph


@pytest.mark.parametrize("age", [48, -1, 12])
def test_direct_graph_rejects_expired_or_future_research(env, decision, age):
    service, broker, _ = env
    graph = build_graph(service, FixtureTeam(decision))
    graph.invoke({"symbol": "ACME", "run_key": "cached", "research_only": True})
    cached = service.store.get("cached", "research_result")
    cached["research_completed_at"] = (AT - timedelta(hours=age)).isoformat()
    service.store.put("cached", cached, "research_result")
    result = graph.invoke({"symbol": "ACME", "run_key": "cached"})
    assert result["execution"]["status"] == "rejected"
    assert "Research is stale or future-dated" in result["execution"]["reasons"]
    assert broker.submits == 0


def test_direct_service_rechecks_news_for_cached_research(env, decision):
    service, broker, data = env
    graph = build_graph(service, FixtureTeam(decision))
    graph.invoke({"symbol": "ACME", "run_key": "cached", "research_only": True})
    data.news_items = [{"created_at": (AT + timedelta(seconds=1)).isoformat()}]
    result = service.execute(decision, "cached")
    assert result["status"] == "rejected"
    assert "New news requires research review" in result["reasons"]
    assert broker.submits == 0


@pytest.mark.parametrize("reason", ["risk", "thesis_invalid", "catalyst_failed"])
def test_protective_exit_survives_cooldown_and_turnover(env, decision, reason):
    service, broker, _ = env
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    service.reconcile()
    service.cfg.cooldown_hours = 24
    service.cfg.turnover_30d = dec("0.001")
    proposal = decision.model_copy(update={"action": "SELL", "exit_reason": reason})
    assert service.execute(proposal, "exit")["status"] == "submitted"


def test_protective_exit_keeps_halt_and_ownership_gates(env, decision):
    service, broker, data = env
    data.halted = True
    result = service.execute(decision.model_copy(update={"action": "SELL", "exit_reason": "risk"}), "exit")
    assert result["status"] == "rejected"
    assert "Sell exceeds uncommitted shares" in result["reasons"]
    assert any("halted" in reason for reason in result["reasons"])
    assert broker.submits == 0


def test_discretionary_exit_keeps_churn_limits(env, decision):
    service, broker, _ = env
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    service.reconcile()
    service.cfg.cooldown_hours = 24
    result = service.execute(
        decision.model_copy(update={"action": "SELL", "exit_reason": "valuation"}), "exit"
    )
    assert result["status"] == "rejected"
    assert "Symbol cooldown is active" in result["reasons"]


def test_broker_fee_revision_preserves_original_and_survives_retry(env):
    service, broker, _ = env
    original = {"id": "fee-1", "activity_type": "FEE", "net_amount": "-1", "date": "2026-10-21"}
    broker.activities = [original.copy()]
    broker.raw_account.update(cash="99999", equity="99999")
    service.reconcile()
    corrected = {**original, "net_amount": "-2"}
    broker.activities = [corrected]
    broker.raw_account.update(cash="99998", equity="99998")
    service.kill_path.write_text("operator review")
    revision = {"before": original, "after": corrected, "reason": "Verified broker fee correction"}
    service.acknowledge_revision(revision)
    service.acknowledge_revision(revision)
    assert service.store.get("fee-1", "activity") == original
    assert service.store.activities() == [corrected]
    assert service.store.get("audit")["fees"] == "2"
    assert len(service.store.items("broker_revision")) == 1
    assert service.kill_path.exists()
    assert broker.submits == 0


@pytest.mark.parametrize("fault", ["activity", "position"])
def test_revision_conflicting_snapshot_cannot_persist_approval(env, fault):
    service, broker, _ = env
    original = {"id": "fee-1", "activity_type": "FEE", "net_amount": "-1", "date": "2026-10-21"}
    broker.activities = [original.copy()]
    broker.raw_account.update(cash="99999", equity="99999")
    service.reconcile()
    corrected = {**original, "net_amount": "-2"}
    broker.activities = [corrected.copy(), {**corrected, "net_amount": "-3"}]
    if fault == "position":
        broker.activities = [corrected.copy()]
        broker.positions = [{"symbol": "ACME", "qty": "1"}] * 2
    broker.raw_account.update(cash="99998", equity="99998")
    service.kill_path.write_text("operator review")
    revision = {"before": original, "after": corrected, "reason": "Verified fee correction"}
    with pytest.raises(RuntimeError, match="Conflicting broker activities|Duplicate broker position"):
        service.acknowledge_revision(revision)
    assert not service.store.items("broker_revision")
    assert not service.store.events("broker_revision_approved")
    assert service.store.activities() == [original]
    assert service.kill_path.exists()
    assert broker.submits == 0

    broker.activities = [corrected.copy(), corrected.copy()]
    broker.positions = []
    service.acknowledge_revision(revision)
    service.acknowledge_revision(revision)
    assert service.store.activities() == [corrected]
    assert len(service.store.items("broker_revision")) == 1
    assert service.store.get("fee-1", "activity") == original


@pytest.mark.parametrize(
    "table,kind", [("documents", "broker_revision"), ("journal", "broker_revision_approved")]
)
def test_revision_storage_failure_rolls_back_approval_and_receipt(env, table, kind):
    import sqlite3

    service, broker, _ = env
    original = {"id": "fee-1", "activity_type": "FEE", "net_amount": "-1", "date": "2026-10-21"}
    broker.activities = [original.copy()]
    broker.raw_account.update(cash="99999", equity="99999")
    service.reconcile()
    corrected = {**original, "net_amount": "-2"}
    broker.activities = [corrected.copy()]
    broker.raw_account.update(cash="99998", equity="99998")
    service.kill_path.write_text("operator review")
    revision = {"before": original, "after": corrected, "reason": "Verified fee correction"}
    service.store.db.execute(
        f"CREATE TEMP TRIGGER fail_revision BEFORE INSERT ON {table} "
        f"WHEN NEW.kind='{kind}' BEGIN SELECT RAISE(ABORT, 'revision write fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="revision write fault"):
        service.acknowledge_revision(revision)
    assert not service.store.items("broker_revision")
    assert not service.store.events("broker_revision_approved")
    assert service.store.activities() == [original]
    service.store.db.execute("DROP TRIGGER fail_revision")
    service.acknowledge_revision(revision)
    service.acknowledge_revision(revision)
    assert service.store.activities() == [corrected]
    assert len(service.store.items("broker_revision")) == 1
    assert len(service.store.events("broker_revision_approved")) == 1
    assert service.store.get("audit")["fees"] == "2"
    assert service.store.get("fee-1", "activity") == original
    assert service.kill_path.exists()
    assert broker.submits == 0


@pytest.mark.parametrize("failure", ["unpaused", "wrong_account", "fabricated", "wrong_before"])
def test_revision_rejects_unverified_evidence(env, failure):
    service, broker, _ = env
    original = {"id": "fee-1", "activity_type": "FEE", "net_amount": "-1", "date": "2026-10-21"}
    broker.activities = [original]
    broker.raw_account.update(cash="99999", equity="99999")
    service.reconcile()
    corrected = {**original, "net_amount": "-2"}
    broker.activities = [corrected]
    service.kill_path.write_text("review")
    revision = {"before": original, "after": corrected, "reason": "Correction"}
    if failure == "unpaused":
        service.kill_path.unlink()
    elif failure == "wrong_account":
        broker.raw_account["id"] = "other-account"
    elif failure == "fabricated":
        revision["after"] = {**corrected, "net_amount": "-3"}
    else:
        revision["before"] = {**original, "net_amount": "-5"}
    with pytest.raises((ValueError, RuntimeError)):
        service.acknowledge_revision(revision)
    assert not service.store.items("broker_revision")
    assert service.store.get("fee-1", "activity") == original


def test_fill_quantity_correction_replays_cash_shares_basis_and_order(env, decision):
    service, broker, _ = env
    result = service.execute(decision, "entry")
    cid = result["client_order_id"]
    original = broker.fixture_fill(cid)
    service.reconcile()
    order_before = service.store.get(cid, "order")
    corrected = {**original, "qty": "9"}
    broker.activities = [corrected]
    broker.orders[cid]["filled_qty"] = "9"
    broker.raw_account["cash"] = "99100"
    broker.positions[0].update(qty="9", cost_basis="900", market_value="900")
    service.kill_path.write_text("correction review")
    revision = {
        "before": original,
        "after": corrected,
        "reason": "Broker reduced execution",
        "order_before": order_before,
        "order_after": broker.orders[cid].copy(),
    }
    service.acknowledge_revision(revision)
    service.reconcile()
    audit = service.store.get("audit")
    assert audit["cash"] == "99100"
    assert audit["shares"]["ACME"] == "9"
    assert audit["basis"]["ACME"] == "900"
    assert service.store.get(cid, "order")["filled_qty"] == "9"
    assert service.store.get(original["id"], "activity")["qty"] == "10"
    assert broker.submits == 1


@pytest.mark.parametrize("metadata", ["overdue", "missing", "future", "malformed"])
def test_budget_exhaustion_still_flags_held_positions_and_blocks_entries(env, decision, metadata):
    from trader.health import check_health
    from trader.reports import portfolio_report
    from trader.runner import Runner

    service, broker, _ = env
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    service.cfg.research_daily_cost_usd = dec(0)
    if metadata != "missing":
        thesis = {
            "reviewed_at": (AT - timedelta(days=1)).isoformat(),
            "decision": {"next_review": (AT - timedelta(seconds=1)).isoformat()},
        }
        if metadata == "future":
            thesis["reviewed_at"] = (AT + timedelta(hours=1)).isoformat()
        elif metadata == "malformed":
            thesis["decision"]["next_review"] = "invalid"
        service.store.put("ACME", thesis, "thesis")
    runner = Runner(service, None)
    runner.maintain()
    runner.maintain()
    issues = service.store.get("position_review")["issues"]
    assert len(issues) == 1
    assert len(service.store.events("position_review_changed")) == 1
    assert issues[0] in portfolio_report(service)["risk_reviews"]
    with pytest.raises(ValueError, match="review"):
        check_health(service.cfg)
    add = decision.model_copy(update={"action": "ADD"})
    assert service.execute(add, "add")["status"] == "rejected"
    exit_decision = decision.model_copy(update={"action": "SELL", "exit_reason": "risk"})
    assert service.execute(exit_decision, "protective-exit")["status"] == "submitted"
    assert broker.submits == 2  # Initial entry and explicit protective exit; no automatic sell.
    service.store.put(
        "ACME", {"reviewed_at": AT.isoformat(), "decision": decision.model_dump(mode="json")}, "thesis"
    )
    runner.maintain()
    assert service.store.get("position_review")["issues"] == []
    assert len(service.store.events("position_review_changed")) == 2
    check_health(service.cfg)


def test_future_evidence_cache_is_not_reused(env, monkeypatch):
    from trader.evidence import EvidenceProvider

    service, _, data = env
    monkeypatch.setattr("trader.evidence.now", lambda: AT)
    service.store.put(
        "ACME", {"fetched_at": (AT + timedelta(hours=1)).isoformat(), "sources": []}, "evidence_cache"
    )
    provider = EvidenceProvider(service.cfg, service.store, data)
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        return {"fetched_at": AT.isoformat(), "sources": [{"id": "fresh"}]}

    monkeypatch.setattr(provider, "fetch", fetch)
    assert provider.get("ACME")["sources"][0]["id"] == "fresh"
    assert calls == ["ACME"]


@pytest.mark.parametrize("case", ["spread", "missing_size", "zero_size", "thin_ask", "notional"])
def test_execution_quality_blocks_unsafe_entries(env, decision, monkeypatch, case):
    service, broker, data = env
    quote = data.quote("ACME")
    if case == "spread":
        quote = quote.model_copy(update={"bid": dec("90")})
    elif case == "missing_size":
        quote = quote.model_copy(update={"ask_size": None})
    elif case == "zero_size":
        quote = quote.model_copy(update={"ask_size": dec(0)})
    elif case == "thin_ask":
        quote = quote.model_copy(update={"ask_size": dec(99)})
    else:
        service.cfg.max_order_equity = dec("0.005")
    monkeypatch.setattr(data, "quote", lambda _: quote)
    assert service.execute(decision, "quality")["status"] == "rejected"
    assert broker.submits == 0


def test_sell_uses_bid_depth_and_preserves_position_on_rejection(env, decision, monkeypatch):
    service, broker, data = env
    entry = service.execute(decision, "entry")
    broker.fixture_fill(entry["client_order_id"])
    service.reconcile()
    quote = data.quote("ACME").model_copy(update={"bid_size": dec(1), "ask_size": dec(10000)})
    monkeypatch.setattr(data, "quote", lambda _: quote)
    result = service.execute(decision.model_copy(update={"action": "SELL", "exit_reason": "risk"}), "exit")
    assert "Order exceeds displayed quote participation limit" in result["reasons"]
    assert broker.submits == 1
    assert service.store.get("audit")["shares"]["ACME"] == "10"


def test_revision_chain_can_return_to_original_then_change_again(env):
    service, broker, _ = env
    original = {"id": "fee-cycle", "activity_type": "FEE", "net_amount": "-1", "date": "2026-10-21"}
    broker.activities = [original.copy()]
    broker.raw_account.update(cash="99999", equity="99999")
    service.reconcile()
    service.kill_path.write_text("review")
    before = original
    approvals = []
    for index, amount in enumerate(("-2", "-1", "-3")):
        after = {**original, "net_amount": amount}
        broker.activities = [after]
        broker.raw_account.update(cash=str(100000 + int(amount)), equity=str(100000 + int(amount)))
        approval = {"before": before, "after": after, "reason": f"Broker revision {index}"}
        service.acknowledge_revision(approval)
        approvals.append(approval)
        before = after
    for approval in approvals:
        service.acknowledge_revision(approval)
    assert service.store.activities() == [before]
    assert service.store.get("audit")["cash"] == "99997"
    assert service.store.get(original["id"], "activity") == original
    assert len(service.store.items("broker_revision")) == 3
    assert broker.submits == 0


@pytest.mark.parametrize("order_status", ["filled", "new", "partially_filled"])
def test_zero_quantity_bust_remains_blocked_without_verified_contract(env, decision, order_status):
    service, broker, _ = env
    result = service.execute(decision, "bust-entry")
    cid = result["client_order_id"]
    original = broker.fixture_fill(cid)
    service.reconcile()
    order_before = service.store.get(cid, "order")
    busted = {**original, "qty": "0"}
    broker.activities = [busted]
    broker.orders[cid].update(filled_qty="0", status=order_status)
    broker.raw_account.update(cash="100000", equity="100000")
    broker.positions = []
    from trader.service import BrokerHistoryError

    assert not service.kill_path.exists()
    with pytest.raises(BrokerHistoryError):
        service.reconcile()
    assert service.kill_path.exists()
    assert service.store.get(cid, "order") == order_before
    assert service.store.get(cid, "intent")["status"] == "complete"
    with pytest.raises(BrokerHistoryError):
        service.resume()
    assert service.kill_path.exists()
    with pytest.raises(RuntimeError, match="Invalid fill activity"):
        service.acknowledge_revision(
            {
                "before": original,
                "after": busted,
                "order_before": order_before,
                "order_after": broker.orders[cid].copy(),
                "reason": "Unverified zero-quantity bust shape",
            }
        )
    assert service.kill_path.exists()
    assert service.store.get(original["id"], "activity") == original
    assert service.store.get("audit")["cash"] == "99000"
    assert not service.store.items("broker_revision")
    assert broker.submits == 1


@pytest.mark.parametrize("change", ["filing", "unavailable", "bad_time", "unchanged"])
def test_execution_rechecks_filings_and_retains_check(env, decision, monkeypatch, change):
    service, broker, _ = env
    graph = build_graph(service, FixtureTeam(decision))
    graph.invoke({"symbol": "ACME", "run_key": "filings", "research_only": True})
    research = service.store.get("filings", "research_result")
    research["evidence"]["fetched_at"] = (AT - timedelta(minutes=5)).isoformat()
    if change == "bad_time":
        research["evidence"]["fetched_at"] = (AT + timedelta(seconds=1)).isoformat()
    service.store.put("filings", research, "research_result")
    checks = []

    def check(symbol, since):
        checks.append((symbol, since))
        if change == "unavailable":
            raise TimeoutError("SEC timeout")
        return [{"accessionNumber": "new-amendment"}] if change == "filing" else []

    monkeypatch.setattr(service, "filings_since", check)
    result = service.execute(decision, "filings")
    assert result["status"] == ("submitted" if change == "unchanged" else "rejected")
    assert broker.submits == (1 if change == "unchanged" else 0)
    assert result["filing_check"]
    if change != "bad_time":
        assert checks == [("ACME", AT - timedelta(minutes=5))]
    if change == "unchanged":
        service.execute(decision, "filings")
        assert len(checks) == 1  # Existing intents reconcile without new freshness gates.


@pytest.mark.parametrize("revised", [False, True])
def test_news_changed_after_evidence_collection_blocks_cached_execution(env, decision, revised):
    service, broker, data = env
    build_graph(service, FixtureTeam(decision)).invoke(
        {"symbol": "ACME", "run_key": "cached-news", "research_only": True}
    )
    cached = service.store.get("cached-news", "research_result")
    cached["evidence"]["fetched_at"] = (AT - timedelta(minutes=5)).isoformat()
    service.store.put("cached-news", cached, "research_result")
    changed = (AT - timedelta(minutes=1)).isoformat()
    data.news_items = [
        {
            "created_at": (AT - timedelta(days=1)).isoformat() if revised else changed,
            "updated_at": changed,
        }
    ]
    result = service.execute(decision, "cached-news")
    assert result["status"] == "rejected"
    assert "New news requires research review" in result["reasons"]
    assert broker.submits == 0


def test_later_news_page_failure_blocks_execution(env, decision, monkeypatch):
    import httpx

    from trader.alpaca import AlpacaData

    service, broker, data = env
    build_graph(service, FixtureTeam(decision)).invoke(
        {"symbol": "ACME", "run_key": "news-outage", "research_only": True}
    )
    pages = []

    def handler(req):
        pages.append(req)
        if len(pages) == 1:
            return httpx.Response(200, json={"news": [], "next_page_token": "next"})
        return httpx.Response(503)

    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture-secret")
    adapter = AlpacaData(service.cfg, httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(data, "news", adapter.news)
    with pytest.raises(httpx.HTTPStatusError):
        service.execute(decision, "news-outage")
    assert len(pages) == 2
    assert broker.submits == 0
    assert service.store.get(service.client_order_id("news-outage"), "intent") is None


@pytest.mark.parametrize("fault", ["missing", "gap", "blank", "uncited"])
def test_cached_buy_requires_normalization_review(env, decision, fault):
    service, broker, _ = env
    build_graph(service, FixtureTeam(decision)).invoke(
        {"symbol": "ACME", "run_key": "normalization", "research_only": True}
    )
    saved = service.store.get("normalization", "research_result")
    review = saved["valuation"]["normalization_review"]
    if fault == "missing":
        saved["valuation"].pop("normalization_review")
    elif fault == "gap":
        review["unresolved_material_gaps"] = True
    elif fault == "blank":
        review["debt_and_cash"] = " "
    else:
        review["evidence_ids"] = ["unknown"]
    service.store.put("normalization", saved, "research_result")
    result = service.execute(decision, "normalization")
    assert result["status"] == "rejected"
    assert "Valuation normalization review is missing or unresolved" in result["reasons"]
    assert broker.submits == 0


@pytest.mark.parametrize("fault", ["missing", "gap", "unknown", "string_false", "uncited", "empty"])
@pytest.mark.parametrize("entry", ["service", "graph"])
def test_cached_buy_rechecks_adversarial_review(env, decision, fault, entry):
    service, broker, _ = env
    graph = build_graph(service, FixtureTeam(decision))
    graph.invoke({"symbol": "ACME", "run_key": "critic-cache", "research_only": True})
    saved = service.store.get("critic-cache", "research_result")
    if fault == "missing":
        saved.pop("critique")
    elif fault == "gap":
        saved["critique"]["unresolved_material_gaps"] = True
    elif fault == "unknown":
        saved["critique"]["unresolved_material_gaps"] = None
    elif fault == "string_false":
        saved["critique"]["unresolved_material_gaps"] = "false"
    elif fault == "uncited":
        saved["critique"]["evidence_ids"] = ["absent-source"]
    else:
        saved["critique"]["evidence_ids"] = []
    service.store.put("critic-cache", saved, "research_result")
    if entry == "service":
        result = service.execute(decision, "critic-cache")
    else:
        result = graph.invoke({"symbol": "ACME", "run_key": "critic-cache"})["execution"]
    assert result["status"] == "rejected"
    assert "Adversarial review is missing, unresolved or lacks retained evidence" in result["reasons"]
    assert broker.submits == 0
