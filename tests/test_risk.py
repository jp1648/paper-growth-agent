from datetime import timedelta

import pytest
from conftest import AT
from pydantic import ValidationError

from trader.config import Settings
from trader.models import D, Decision


@pytest.mark.parametrize(
    "url",
    [
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets.evil",
        "https://paper-api.alpaca.markets/",
    ],
)
def test_live_or_ambiguous_urls_fail(url):
    with pytest.raises(ValidationError):
        Settings(broker_url=url)


def test_live_environment_fails(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "field,value",
    [("quantity", "1.5"), ("limit_price", "NaN"), ("quantity", "Infinity"), ("limit_price", "100.001")],
)
def test_invalid_order_values(decision, field, value):
    raw = decision.model_dump()
    raw[field] = value
    with pytest.raises(ValidationError):
        Decision.model_validate(raw)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("closed", "Market closed"),
        ("early_close", "Outside broker"),
        ("stale", "Quote delayed"),
        ("delayed", "Quote delayed"),
        ("halted", "Security halted"),
        ("unknown_halt", "Security halted"),
        ("concentration", "Entry concentration"),
        ("cash", "Insufficient unreserved cash"),
        ("turnover", "turnover ceiling"),
        ("cooldown", "cooldown"),
        ("tolerance", "tolerance"),
        ("ended", "Experiment ended"),
        ("count", "position count"),
        ("sector", "sector limit"),
    ],
)
def test_risk_rejects(env, decision, case, expected):
    service, broker, data = env
    if case == "closed":
        broker.open = False
    elif case == "early_close":
        broker.end = "10:00"
    elif case == "stale":
        data.at = AT - timedelta(minutes=10)
    elif case == "delayed":
        data.delayed = True
    elif case == "halted":
        data.halted = True
    elif case == "unknown_halt":
        data.halted = None
    elif case == "concentration":
        decision = decision.model_copy(update={"quantity": D("101")})
    elif case == "cash":
        decision = decision.model_copy(update={"quantity": D("1001")})
    elif case == "turnover":
        service.cfg.turnover_30d = D("0.001")
    elif case == "cooldown":
        service.cfg.cooldown_hours = 24
        service.store.put("ACME", {"at": AT.isoformat()}, "last_trade")
    elif case == "tolerance":
        decision = decision.model_copy(update={"limit_price": D("90")})
    elif case == "ended":
        service.store.experiment["ends_at"] = (AT - timedelta(days=1)).isoformat()
    elif case == "count":
        service.cfg.max_positions = 1
        service.execute(decision.model_copy(update={"symbol": "OTHER"}), "other")
    elif case == "sector":
        service.cfg.max_sector = D("0.001")
    result = service.execute(decision, case)
    assert result["status"] == "rejected"
    assert any(expected in reason for reason in result["reasons"])


def test_pending_buy_included_in_cash_and_exposure(env, decision):
    service, _, _ = env
    service.execute(decision, "entry")
    result = service.execute(decision, "duplicate-symbol")
    assert "Outstanding order already exists for this symbol" in result["reasons"]


def test_no_short_sell(env, decision):
    service, broker, _ = env
    result = service.execute(decision.model_copy(update={"action": "SELL", "exit_reason": "risk"}), "short")
    assert "Sell exceeds uncommitted shares" in result["reasons"]
    assert broker.submits == 0


def test_hold_never_submits(env, decision):
    service, broker, _ = env
    result = service.execute(decision.model_copy(update={"action": "HOLD", "quantity": D("0")}), "hold")
    assert result["status"] == "no_order"
    assert broker.submits == 0


@pytest.mark.parametrize("feed", ["delayed_sip", "unknown"])
def test_configuration_rejects_delayed_or_unknown_feed(feed):
    with pytest.raises(ValidationError):
        Settings(feed=feed)


@pytest.mark.parametrize(
    "configured,feed", [("iex", "sip"), ("sip", "iex"), ("iex", "delayed_sip"), ("iex", "unknown")]
)
def test_risk_gate_rejects_wrong_feed_even_when_fresh(env, decision, configured, feed):
    service, broker, data = env
    service.cfg.feed = configured
    original = data.quote
    data.quote = lambda symbol: original(symbol).model_copy(update={"feed": feed})
    result = service.execute(decision, "wrong-feed")
    assert result["status"] == "rejected"
    assert "Quote feed does not match configured market data" in result["reasons"]
    assert broker.submits == 0


@pytest.mark.parametrize("feed", ["iex", "sip"])
def test_configured_realtime_feed_can_submit(env, decision, feed):
    service, broker, data = env
    assert Settings().feed == "iex"
    service.cfg.feed = feed
    original = data.quote
    data.quote = lambda symbol: original(symbol).model_copy(update={"feed": feed})
    result = service.execute(decision, "matching-feed")
    assert result["status"] == "submitted"
    assert broker.submits == 1


@pytest.mark.parametrize(
    "missing", ["currency", "trading_blocked", "account_blocked", "trade_suspended_by_user"]
)
def test_missing_account_eligibility_fails_closed(env, decision, missing):
    service, broker, _ = env
    original = broker.account
    broker.account = lambda: {k: v for k, v in original().items() if k != missing}
    reasons, _, _ = service.evaluate(decision)
    assert any("USD" in r or "eligible" in r for r in reasons)


@pytest.mark.parametrize("symbol", ["aapl", "../account", "AAPL?x=1", "AAPL/quotes"])
def test_config_rejects_unsafe_symbols(symbol):
    with pytest.raises(ValidationError):
        Settings(universe={symbol: {"sector": "Example", "cik": "1"}})
