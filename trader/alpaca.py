from __future__ import annotations

from datetime import UTC
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

from .config import Settings, credentials
from .models import PAPER_URL, Quote, Snapshot, dec, dt, now
from .observability import traced


class AlpacaBroker:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        key, secret = credentials()
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    @traced("alpaca.request")
    def request(self, method, path, **kwargs):
        # Check each call as well as startup; never accept redirects or arbitrary URLs.
        Settings.model_validate(self.settings.model_dump())
        if not path.startswith("/v2/"):
            raise ValueError("Invalid trading path")
        response = self.client.request(
            method, PAPER_URL + path, headers=self.headers, follow_redirects=False, **kwargs
        )
        response.raise_for_status()
        return response.json(parse_float=dec) if response.content else None

    def account(self):
        return self.request("GET", "/v2/account")

    def open_orders(self):
        orders = self.request("GET", "/v2/orders", params={"status": "open", "limit": 500})
        if len(orders) == 500:
            raise RuntimeError("Open order list may be truncated")
        return orders

    def lookup(self, client_id):
        try:
            return self.request("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_id})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def submit(self, payload):
        if payload.get("type") != "limit" or payload.get("extended_hours") is not False:
            raise ValueError("Only regular-hours limit orders allowed")
        return self.request("POST", "/v2/orders", json=payload)

    def cancel(self, order_id):
        self.request("DELETE", f"/v2/orders/{order_id}")

    def clock(self):
        return self.request("GET", "/v2/clock")

    def calendar(self, start, end):
        return self.request("GET", "/v2/calendar", params={"start": start, "end": end})

    def asset(self, symbol):
        return self.request("GET", f"/v2/assets/{symbol}")

    def snapshot(self, after, tracked_ids):
        # Read all activities since baseline, with overlap; activity IDs make replay idempotent.
        activities, token = [], None
        for _ in range(1000):
            params = {"after": after, "direction": "asc", "page_size": 100}
            if token:
                params["page_token"] = token
            page = self.request("GET", "/v2/account/activities", params=params)
            activities.extend(page)
            if len(page) < 100:
                break
            if token == page[-1]["id"]:
                raise RuntimeError("Activity pagination stalled")
            token = page[-1]["id"]
        else:
            raise RuntimeError("Activity pagination bound exceeded")
        orders = self.open_orders()
        by_client = {o["client_order_id"]: o for o in orders}
        for cid in tracked_ids:
            order = self.lookup(cid)
            if order:
                by_client[cid] = order
        positions = self.request("GET", "/v2/positions")
        return Snapshot(
            account=self.account(),
            positions=positions,
            orders=list(by_client.values()),
            activities=activities,
            captured_at=now(),
        )


class AlpacaData:
    BASE = "https://data.alpaca.markets"
    HALTS = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"

    def __init__(self, settings, client=None):
        self.settings = settings
        key, secret = credentials()
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self._halts = None

    @traced("alpaca.get")
    def get(self, path, params):
        r = self.client.get(self.BASE + path, params=params, headers=self.headers)
        r.raise_for_status()
        return r.json(parse_float=dec)

    @traced("alpaca.halt_status")
    def halt_status(self, symbol):
        current = now()
        if self._halts and (current - self._halts[0]).total_seconds() < 30:
            checked, published, halted = self._halts
        else:
            r = self.client.get(self.HALTS)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            channel = root.find("channel")
            if channel is None or channel.findtext("title") != "NASDAQTrader.com":
                raise ValueError("Unrecognized halt feed")
            published = parsedate_to_datetime(channel.findtext("pubDate"))
            ns = {"n": "http://www.nasdaqtrader.com/"}
            items = channel.findall("item")
            if int(channel.findtext("n:numItems", namespaces=ns)) != len(items):
                raise ValueError("Incomplete halt feed")
            halted = set()
            for item in items:
                ticker = item.findtext("n:IssueSymbol", namespaces=ns)
                date = item.findtext("n:ResumptionDate", namespaces=ns)
                time = item.findtext("n:ResumptionTradeTime", namespaces=ns)
                if not date or not time:
                    halted.add(ticker)
                else:
                    from datetime import datetime

                    resume = (
                        datetime.strptime(f"{date} {time}", "%m/%d/%Y %H:%M:%S.%f")
                        if "." in time
                        else datetime.strptime(f"{date} {time}", "%m/%d/%Y %H:%M:%S")
                    )
                    if resume.replace(tzinfo=ZoneInfo("America/New_York")) > current:
                        halted.add(ticker)
            checked = current
            self._halts = checked, published, halted
        age = (current - published).total_seconds()
        if age < -5 or age > self.settings.halt_max_age_seconds:
            return None, published
        return symbol in halted, published

    def quote(self, symbol):
        raw = self.get(f"/v2/stocks/{symbol}/quotes/latest", {"feed": self.settings.feed})["quote"]
        halted, checked = self.halt_status(symbol)
        feed = self.settings.feed
        return Quote(
            symbol=symbol,
            bid=raw["bp"],
            ask=raw["ap"],
            # Provider sizes are shares since 2025-11-03; older round-lot quotes are not executable here.
            bid_size=raw.get("bs") if dt(raw["t"]).date().isoformat() >= "2025-11-03" else None,
            ask_size=raw.get("as") if dt(raw["t"]).date().isoformat() >= "2025-11-03" else None,
            timestamp=dt(raw["t"]),
            source="Alpaca",
            feed=feed,
            delayed=feed == "delayed_sip",
            coverage="IEX exchange only" if feed == "iex" else "US consolidated SIP",
            halted=halted,
            halt_checked_at=checked,
        )

    def news(self, symbol, since):
        return self.get(
            "/v1beta1/news",
            {"symbols": symbol, "start": since, "limit": 50, "sort": "desc", "include_content": "true"},
        ).get("news", [])

    def bars(self, symbol, start, end):
        result, token = [], None
        for _ in range(100):
            params = {
                "start": start,
                "end": end,
                "timeframe": "1Day",
                "adjustment": "split",
                "feed": self.settings.feed,
                "limit": 10000,
            }
            if token:
                params["page_token"] = token
            page = self.get(f"/v2/stocks/{symbol}/bars", params)
            result.extend(page.get("bars") or [])
            token = page.get("next_page_token")
            if not token:
                return result
        raise RuntimeError("Bars pagination bound exceeded")

    def session_close(self, symbol, day):
        from datetime import datetime

        zone = ZoneInfo("America/New_York")
        opening = datetime.fromisoformat(day["date"] + "T" + day["open"]).replace(tzinfo=zone)
        closing = datetime.fromisoformat(day["date"] + "T" + day["close"]).replace(tzinfo=zone)
        page = self.get(
            f"/v2/stocks/{symbol}/bars",
            {
                "start": opening.astimezone(UTC).isoformat(),
                "end": closing.astimezone(UTC).isoformat(),
                "timeframe": "1Min",
                "adjustment": "split",
                "feed": self.settings.feed,
                "limit": 10000,
            },
        )
        bars = [b for b in page.get("bars") or [] if opening <= dt(b["t"]) < closing]
        if page.get("next_page_token") or not bars:
            raise ValueError(f"Incomplete regular-session close for {symbol}")
        last = max(bars, key=lambda b: dt(b["t"]))
        if (closing - dt(last["t"])).total_seconds() > 300:
            raise ValueError(f"Stale regular-session close for {symbol}")
        return dec(last["c"])


def safe_error(error):
    """Do not persist response bodies, request headers or credential-bearing exception strings."""
    from openai import APIError

    if isinstance(error, APIError):
        code = getattr(error, "code", None)
        suffix = (
            f" ({code})"
            if code
            in {
                "insufficient_quota",
                "billing_not_active",
                "credit_balance_exhausted",
                "organization_spend_limit_exceeded",
                "project_spend_limit_exceeded",
                "organization_usage_limit_exceeded",
                "slow_down",
                "rate_limit_exceeded",
                "model_not_found",
                "invalid_api_key",
                "invalid_request_error",
            }
            else ""
        )
        return f"OpenAI {type(error).__name__}: status {getattr(error, 'status_code', 'unavailable')}{suffix}"
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}: {error.request.url.host}{error.request.url.path}"
    return (
        type(error).__name__ + ": " + str(error)[:300]
        if not isinstance(error, httpx.RequestError)
        else type(error).__name__
    )
