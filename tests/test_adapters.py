from datetime import timedelta

import httpx
import pytest
from conftest import AT

from trader.alpaca import AlpacaBroker, AlpacaData
from trader.config import Settings
from trader.models import D


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture-secret")


def test_broker_hardcodes_paper_and_preserves_decimals(keys):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, text='{"cash":"100000.01","numeric":0.1234567890123456789}')

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert broker.account()["numeric"] == D("0.1234567890123456789")
    assert str(seen[0].url) == "https://paper-api.alpaca.markets/v2/account"
    assert seen[0].headers["APCA-API-KEY-ID"] == "fixture-key"


def test_no_redirect_to_live(keys):
    def handler(req):
        return httpx.Response(307, headers={"Location": "https://api.alpaca.markets/v2/account"})

    broker = AlpacaBroker(
        Settings(), httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    )
    with pytest.raises(httpx.HTTPStatusError):
        broker.account()


def test_data_credentials_never_follow_redirect_with_injected_client(keys):
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.host == "data.alpaca.markets":
            return httpx.Response(307, headers={"Location": "https://untrusted.example/quotes"})
        return httpx.Response(200, json={})

    data = AlpacaData(Settings(), httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))
    with pytest.raises(httpx.HTTPStatusError):
        data.get("/v2/stocks/ACME/quotes/latest", {"feed": "iex"})
    assert len(seen) == 1


@pytest.mark.parametrize("ticker", [None, "", " ", " ACME", "ACME "])
def test_malformed_halt_symbol_cannot_be_reported_as_clear(keys, monkeypatch, ticker):
    monkeypatch.setattr("trader.alpaca.now", lambda: AT)
    xml = f"""<rss xmlns:n="http://www.nasdaqtrader.com/"><channel><title>NASDAQTrader.com</title>
    <pubDate>{AT.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate><n:numItems>1</n:numItems>
    <item><n:IssueSymbol>{ticker}</n:IssueSymbol></item></channel></rss>"""
    if ticker is None:
        xml = xml.replace("<n:IssueSymbol>None</n:IssueSymbol>", "")
    recovered_xml = (
        xml.split("<item>")[0] + "<item><n:IssueSymbol>ACME</n:IssueSymbol></item></channel></rss>"
    )
    responses = [xml, recovered_xml]
    data = AlpacaData(
        Settings(),
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=responses.pop(0)))),
    )
    with pytest.raises(ValueError, match="halt symbol"):
        data.halt_status("ACME")
    assert data._halts is None
    assert data.halt_status("ACME")[0] is True


@pytest.mark.parametrize(
    "page", [{}, {"error": "unavailable"}, None, "", [None], [{}], [{"id": ""}], [{"id": 1}]]
)
def test_invalid_activity_page_never_becomes_empty_history(keys, page):
    def handler(req):
        if req.url.path.endswith("activities"):
            return httpx.Response(200, text="null") if page is None else httpx.Response(200, json=page)
        pytest.fail("Invalid history must stop snapshot collection")

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="activity page"):
        broker.snapshot(AT.isoformat(), [])


def test_activity_pagination_cycle_stops_at_first_repeated_cursor(keys):
    calls = []

    def handler(req):
        calls.append(req)
        assert len(calls) <= 3, "Cycle must not consume the 1000-page allowance"
        tail = "a" if len(calls) % 2 else "b"
        return httpx.Response(200, json=[{"id": f"{tail}-{i}"} for i in range(100)])

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="pagination"):
        broker.snapshot(AT.isoformat(), [])
    assert len(calls) == 3


def test_activity_history_bound_rejects_instead_of_returning_partial_snapshot(keys):
    calls = 0

    def handler(req):
        nonlocal calls
        assert req.url.path.endswith("activities")
        calls += 1
        return httpx.Response(200, json=[{"id": f"{calls}-{i}"} for i in range(100)])

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="bound exceeded"):
        broker.snapshot(AT.isoformat(), [])
    assert calls == 1000


def test_valid_empty_activity_history_is_supported(keys):
    def handler(req):
        return httpx.Response(200, json={"id": "fixture"} if req.url.path.endswith("account") else [])

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert broker.snapshot(AT.isoformat(), []).activities == []


@pytest.mark.parametrize(
    "orders",
    [
        {},
        None,
        [None],
        [{}],
        [{"id": "a", "client_order_id": ""}],
        [{"id": "a", "client_order_id": "same"}, {"id": "b", "client_order_id": "same"}],
        [{"id": "same", "client_order_id": "a"}, {"id": "same", "client_order_id": "b"}],
    ],
)
def test_open_order_identity_is_not_silently_collapsed(keys, orders):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: (
                httpx.Response(200, text="null") if orders is None else httpx.Response(200, json=orders)
            )
        )
    )
    with pytest.raises(ValueError, match="order"):
        AlpacaBroker(Settings(), client).open_orders()


@pytest.mark.parametrize("change", ["id", "client", "status"])
def test_tracked_lookup_cannot_replace_a_different_open_order(keys, change):
    def handler(req):
        if req.url.path.endswith("activities"):
            return httpx.Response(200, json=[])
        if req.url.path.endswith("by_client_order_id"):
            return httpx.Response(
                200,
                json={
                    "id": "other" if change == "id" else "original",
                    "client_order_id": "other" if change == "client" else "tracked",
                    "status": "filled",
                },
            )
        if req.url.path.endswith("orders"):
            return httpx.Response(200, json=[{"id": "original", "client_order_id": "tracked"}])
        if change != "status":
            pytest.fail("Conflicting identity must stop snapshot collection")
        return httpx.Response(200, json={"id": "fixture"} if req.url.path.endswith("account") else [])

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    if change == "status":
        assert broker.snapshot(AT.isoformat(), ["tracked"]).orders[0]["status"] == "filled"
    else:
        with pytest.raises(ValueError, match="order identity"):
            broker.snapshot(AT.isoformat(), ["tracked"])


def test_activity_pagination_and_lookup404(keys):
    requests = []

    def handler(req):
        requests.append(req)
        path = req.url.path
        if path.endswith("activities"):
            if "page_token" not in req.url.params:
                return httpx.Response(200, json=[{"id": str(i)} for i in range(100)])
            assert req.url.params["page_token"] == "99"
            return httpx.Response(200, json=[{"id": "100"}])
        if path.endswith("by_client_order_id"):
            return httpx.Response(404)
        if path.endswith("account"):
            return httpx.Response(200, json={"id": "fixture"})
        return httpx.Response(200, json=[])

    broker = AlpacaBroker(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    snap = broker.snapshot(AT.isoformat(), ["unknown"])
    assert len(snap.activities) == 101
    assert broker.lookup("unknown") is None


@pytest.mark.parametrize("feed,coverage", [("iex", "IEX exchange only"), ("sip", "US consolidated SIP")])
def test_feed_metadata_and_halt_feed(keys, monkeypatch, feed, coverage):
    monkeypatch.setattr("trader.alpaca.now", lambda: AT)
    halt_xml = f"""<rss xmlns:n="http://www.nasdaqtrader.com/"><channel><title>NASDAQTrader.com</title>
    <pubDate>{AT.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate><n:numItems>1</n:numItems>
    <item><n:IssueSymbol>HALT</n:IssueSymbol><n:ResumptionDate/><n:ResumptionTradeTime/></item></channel></rss>"""

    def handler(req):
        if req.url.host == "www.nasdaqtrader.com":
            return httpx.Response(200, text=halt_xml)
        assert req.url.params["feed"] == feed
        return httpx.Response(200, json={"quote": {"bp": 99.99, "ap": 100, "t": AT.isoformat()}})

    data = AlpacaData(Settings(feed=feed), httpx.Client(transport=httpx.MockTransport(handler)))
    quote = data.quote("ACME")
    assert quote.coverage == coverage
    assert quote.feed == feed
    assert quote.delayed is False
    assert quote.halted is False
    assert data.quote("HALT").halted is True
    monkeypatch.setattr("trader.alpaca.now", lambda: AT + timedelta(minutes=5))
    assert data.quote("ACME").halted is None


@pytest.mark.parametrize("duplicate", [False, True])
def test_regular_session_close_excludes_extended_hours(keys, duplicate):
    def handler(req):
        assert req.url.params["timeframe"] == "1Min"
        assert req.url.params["adjustment"] == "split"
        assert req.url.params["feed"] == "iex"
        return httpx.Response(
            200,
            json={
                "bars": [{"t": "2026-10-21T19:59:00Z", "c": 100}] * (2 if duplicate else 1)
                + [{"t": "2026-10-21T20:00:00Z", "c": 200}],
                "next_page_token": None,
            },
        )

    data = AlpacaData(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert data.session_close("ACME", {"date": "2026-10-21", "open": "09:30", "close": "16:00"}) == D(100)


def test_sip_entitlement_failure_does_not_fall_back(keys):
    requests = []

    def handler(req):
        requests.append(req)
        assert req.url.params["feed"] == "sip"
        return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})

    data = AlpacaData(Settings(feed="sip"), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError):
        data.quote("ACME")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://data.sec.gov/api",
        "https://data.sec.gov.attacker.test/api",
        "https://user:secret@www.sec.gov/Archives/a",
        "https://127.0.0.1/",
        "https://www.sec.gov:8443/Archives/a",
        "file:///etc/passwd",
    ],
)
def test_sec_rejects_untrusted_origin_before_network(url):
    from trader.evidence import EvidenceProvider

    client = httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail("Unexpected request")))
    provider = EvidenceProvider(Settings(sec_user_agent="Test contact@example.org"), None, None, client)
    with pytest.raises(ValueError, match="SEC evidence URLs"):
        provider.request(url)


@pytest.mark.parametrize(
    "stamp,expected", [("2026-10-21T15:00:00Z", D("123")), ("2025-11-02T15:00:00Z", None)]
)
def test_quote_sizes_use_shares_without_round_lot_multiplication(keys, monkeypatch, stamp, expected):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"quote": {"bp": 99, "ap": 100, "bs": 123, "as": 456, "t": stamp}}
            )
        )
    )
    data = AlpacaData(Settings(), client)
    monkeypatch.setattr(data, "halt_status", lambda symbol: (False, AT))
    quote = data.quote("ACME")
    assert quote.bid_size == expected
    assert quote.ask_size == (D(456) if expected is not None else None)


def test_news_paginates_with_fixed_window(keys, monkeypatch):
    monkeypatch.setattr("trader.alpaca.now", lambda: AT)
    requests = []

    def handler(req):
        requests.append(req)
        if len(requests) == 1:
            return httpx.Response(200, json={"news": [{"id": 1}], "next_page_token": "second"})
        assert req.url.params["page_token"] == "second"
        return httpx.Response(200, json={"news": [{"id": 2}], "next_page_token": None})

    data = AlpacaData(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert data.news("ACME", (AT - timedelta(hours=1)).isoformat()) == [{"id": 1}, {"id": 2}]
    assert len(requests) == 2
    assert requests[0].url.params["end"] == requests[1].url.params["end"] == AT.isoformat()


@pytest.mark.parametrize(
    "page", [{}, {"news": None}, {"news": {}}, {"news": [None]}, {"news": [], "next_page_token": 1}]
)
def test_news_malformed_page_is_not_empty_coverage(keys, page):
    data = AlpacaData(
        Settings(), httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=page)))
    )
    with pytest.raises((ValueError, TypeError), match="news"):
        data.news("ACME", AT.isoformat())


@pytest.mark.parametrize("cycle", [True, False])
def test_news_incomplete_pagination_never_returns_partial_results(keys, cycle):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={"news": [{"id": len(calls)}], "next_page_token": "repeat" if cycle else str(len(calls))},
        )

    data = AlpacaData(Settings(), httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="News pagination"):
        data.news("ACME", AT.isoformat())
    assert len(calls) == (2 if cycle else 100)


@pytest.mark.parametrize("close", [0, -1, "NaN", "Infinity", "-Infinity"])
def test_regular_close_rejects_invalid_valuation_marks(keys, close):
    data = AlpacaData(
        Settings(),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"bars": [{"t": "2026-10-21T19:59:00Z", "c": close}]}
                )
            )
        ),
    )
    with pytest.raises(ValueError):
        data.session_close("ACME", {"date": "2026-10-21", "open": "09:30", "close": "16:00"})


@pytest.mark.parametrize("reverse", [False, True])
def test_regular_close_rejects_conflicting_duplicate_bars(keys, reverse):
    bars = [{"t": "2026-10-21T19:59:00Z", "c": value} for value in [100, 150]]
    data = AlpacaData(
        Settings(),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"bars": bars[::-1] if reverse else bars})
            )
        ),
    )
    with pytest.raises(ValueError, match="Conflicting"):
        data.session_close("ACME", {"date": "2026-10-21", "open": "09:30", "close": "16:00"})


def test_invalid_close_does_not_publish_daily_sample_and_can_recover(env, keys, monkeypatch):
    from trader.reports import daily_report

    service, _, _ = env
    close = 0
    service.data = AlpacaData(
        service.cfg,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"bars": [{"t": "2026-10-21T19:59:00Z", "c": close}]}
                )
            )
        ),
    )
    monkeypatch.setattr("trader.reports.now", lambda: AT + timedelta(hours=6))
    with pytest.raises(ValueError, match="Nonpositive"):
        daily_report(service)
    assert not service.store.items("daily")
    assert not service.store.items("daily_report")
    assert not service.store.events("daily_report")
    close = 100
    report = daily_report(service)
    assert report["marks"] == {"SPY": D(100), "QQQ": D(100)}
    assert len(service.store.items("daily")) == 1
    assert len(service.store.events("daily_report")) == 1
