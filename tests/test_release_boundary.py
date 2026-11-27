"""Offline proof that ordinary configuration cannot activate live execution."""

import httpx
import pytest

from trader.alpaca import AlpacaBroker
from trader.config import Settings, credentials


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize(
    "override", ["settings", "APCA_API_BASE_URL", "ALPACA_BASE_URL", "ALPACA_TRADING_URL"]
)
def test_runtime_endpoint_change_rejected_before_transport(monkeypatch, method, override):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture-paper-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture-paper-secret")
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(lambda req: seen.append(req)))
    settings = Settings()
    broker = AlpacaBroker(settings, client)
    if override == "settings":
        settings.broker_url = "https://api.alpaca.markets"
    else:
        monkeypatch.setenv(override, "https://api.alpaca.markets")
    with pytest.raises(ValueError):
        broker.request(method, "/v2/orders")
    assert seen == []


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_never_reaches_live_transport(monkeypatch, method, status):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fixture-paper-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "fixture-paper-secret")
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(status, headers={"Location": "https://api.alpaca.markets/v2/orders"})

    broker = AlpacaBroker(
        Settings(), httpx.Client(transport=httpx.MockTransport(response), follow_redirects=True)
    )
    with pytest.raises(httpx.HTTPStatusError):
        broker.request(method, "/v2/orders")
    assert len(seen) == 1
    assert seen[0].url.host == "paper-api.alpaca.markets"
    assert seen[0].method == method


def test_live_or_generic_credentials_cannot_replace_paper_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_SECRET_KEY", raising=False)
    for variable in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        monkeypatch.setenv(variable, "fixture-generic-credential")
    with pytest.raises(ValueError, match="dedicated paper account"):
        credentials()
