"""Historical research references never substitute for executable quotes."""

from datetime import timedelta

import httpx
import pytest

from trader.alpaca import AlpacaData
from trader.models import dec
from trader.research import build_graph, research_input


def test_held_graph_offhours_uses_distinct_reference_and_keeps_prior_thesis(env, decision, monkeypatch):
    from test_research_team import team

    service, broker, data = env
    service.execute(decision.model_copy(update={"quantity": dec(1)}), "held", {})
    broker.fixture_fill(service.client_order_id("held"), qty="1")
    service.reconcile()
    prior = {"run_key": "old-thesis", "reviewed_at": (broker.at - timedelta(days=2)).isoformat()}
    service.store.put("ACME", prior, "thesis")
    broker.open = False
    data.ask = dec(0)
    monkeypatch.setattr(
        data, "quote", lambda _: pytest.fail("Closed deferred research must not need a quote")
    )
    worker, model = team(env, decision)
    original = worker.run

    def inspect(context):
        assert context["portfolio"]["prior_thesis"] == prior
        assert context["portfolio"]["snapshot"]["positions"][0]["qty"] == "1"
        assert context["quote"] is None
        assert "ask" not in context["research_reference"]
        return original(context)

    monkeypatch.setattr(worker, "run", inspect)
    result = build_graph(service, worker).invoke(
        {"symbol": "ACME", "run_key": "held-offhours", "research_only": True, "fresh": True}
    )
    reference = result["research_reference"]
    assert result["execution"]["status"] == "deferred" and broker.submits == 1
    assert result["quote"] is None and reference["price"] == "100"
    assert reference["historical"] and reference["executable"] is False
    assert reference["session"]["date"] == "2026-10-20"
    assert reference["session_closed_at"].startswith("2026-10-20T16:00")
    assert reference["retrieved_at"] == broker.at.isoformat()
    assert reference["feed"] == "iex" and reference["source"].endswith("/ACME/bars")
    thesis = service.store.get("ACME", "thesis")
    assert thesis["reference_price"] == "100" and thesis["reference_basis"] == reference
    assert service.store.get("held-offhours", "research_audit")["research_reference"] == reference
    assert model.calls


@pytest.mark.parametrize("open,deferred", [(True, True), (False, False), (True, False)])
def test_no_historical_fallback_for_open_market_or_execution(env, open, deferred, monkeypatch):
    service, broker, data = env
    broker.open = open
    data.ask = dec(0)
    monkeypatch.setattr(data, "session_close", lambda *args: pytest.fail("No historical fallback allowed"))
    with pytest.raises(ValueError):
        research_input(service, "ACME", deferred=deferred)
    assert broker.submits == 0


@pytest.mark.parametrize("failure", ["unknown_session", "stale_clock"])
def test_reference_requires_fresh_broker_clock_and_completed_session(env, monkeypatch, failure):
    service, broker, data = env
    broker.open = False
    monkeypatch.setattr(
        data, "session_close", lambda *args: pytest.fail("Invalid calendar/clock must stop first")
    )
    if failure == "unknown_session":
        monkeypatch.setattr(broker, "calendar", lambda *args: [])
    else:
        broker.at -= timedelta(seconds=31)
    with pytest.raises(ValueError):
        research_input(service, "ACME", deferred=True)


@pytest.mark.parametrize("failure", ["empty", "stale", "paginated", "nonpositive"])
def test_reference_reuses_strict_regular_bar_validation(env, monkeypatch, failure):
    service, broker, _ = env
    broker.open = False
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture-secret")
    bar = {"t": "2026-10-20T19:59:00Z", "c": 100}
    if failure == "stale":
        bar["t"] = "2026-10-20T19:50:00Z"
    if failure == "nonpositive":
        bar["c"] = 0
    payload = {"bars": [] if failure == "empty" else [bar]}
    if failure == "paginated":
        payload["next_page_token"] = "remaining"
    service.data = AlpacaData(
        service.cfg,
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))),
    )
    with pytest.raises(ValueError):
        research_input(service, "ACME", deferred=True)
    assert broker.submits == 0


def test_cached_historical_decision_still_requires_fresh_execution_quote(env, decision, monkeypatch):
    from test_research_team import team

    service, broker, data = env
    broker.open = False
    data.ask = dec(0)
    worker, model = team(env, decision)
    graph = build_graph(service, worker)
    state = {"symbol": "ACME", "run_key": "closed-decision", "research_only": True, "fresh": True}
    result = graph.invoke(state)
    calls = list(model.calls)
    assert result["execution"]["status"] == "deferred"
    broker.open = True
    data.ask = dec(100)
    data.at -= timedelta(seconds=61)
    monkeypatch.setattr(
        data, "session_close", lambda *args: pytest.fail("Cached execution needs fresh live quote")
    )
    result = graph.invoke({**state, "research_only": False})
    assert result["execution"]["status"] == "rejected"
    assert any("stale" in reason for reason in result["execution"]["reasons"])
    assert model.calls == calls and broker.submits == 0


def test_historical_reference_keeps_raw_bar_provenance_in_durable_trace(env, monkeypatch, tmp_path):
    import json

    from trader import observability as obs

    service, broker, _ = env
    broker.open = False
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture-secret")
    sink = obs.Sink(tmp_path / "telemetry.sqlite", "unused", start=False)
    monkeypatch.setattr(obs, "_sink", sink)
    bar = {"t": "2026-10-20T19:59:00Z", "c": 100}

    def response(request):
        assert request.url.params["start"] == "2026-10-20T13:30:00+00:00"
        assert request.url.params["end"] == "2026-10-20T20:00:00+00:00"
        assert request.url.params["feed"] == "iex"
        return httpx.Response(200, json={"bars": [bar]})

    service.data = AlpacaData(service.cfg, httpx.Client(transport=httpx.MockTransport(response)))
    result = research_input(service, "ACME", deferred=True)
    reference = result["research_reference"]
    assert result["quote"] is None and reference["price"] == "100"
    assert "not an official" in reference["method"]
    assert "bar_timestamp" not in reference and "ask" not in reference and "bid" not in reference
    with sink.db() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT body FROM outbox")]
    raw = next(event for event in events if event["name"] == "alpaca.get" and event["phase"] == "end")
    assert raw["payload"]["output"]["bars"] == [bar]


@pytest.mark.parametrize(
    "failure",
    ["duplicate", "conflict", "noncanonical", "reversed", "preopen", "late_close", "timezone", "weekend"],
)
def test_ambiguous_or_malformed_calendar_stops_before_bar_retrieval(env, monkeypatch, failure):
    service, broker, data = env
    broker.open = False
    session = {"date": "2026-10-20", "open": "09:30", "close": "16:00"}
    sessions = [session]
    if failure == "duplicate":
        sessions.append(dict(session))
    elif failure == "conflict":
        sessions.append({**session, "close": "13:00"})
    elif failure == "noncanonical":
        session["date"] = "20261020"
    elif failure == "reversed":
        session["close"] = "09:00"
    elif failure == "preopen":
        session["open"] = "08:00"
    elif failure == "late_close":
        session["close"] = "17:00"
    elif failure == "timezone":
        session["open"] = "09:30-04:00"
    else:
        session["date"] = "2026-10-18"
    monkeypatch.setattr(broker, "calendar", lambda *args: sessions)
    monkeypatch.setattr(data, "session_close", lambda *args: pytest.fail("Invalid calendar must stop first"))
    with pytest.raises(ValueError):
        research_input(service, "ACME", deferred=True)


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_reference_price_fails_closed(env, monkeypatch, price):
    service, broker, data = env
    broker.open = False
    monkeypatch.setattr(data, "session_close", lambda *args: price)
    with pytest.raises(ValueError, match="Non-finite"):
        research_input(service, "ACME", deferred=True)
