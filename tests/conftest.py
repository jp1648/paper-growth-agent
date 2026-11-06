import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from trader.config import Security, Settings
from trader.models import D, Decision, Quote, Snapshot
from trader.service import Service
from trader.store import Store

FIXTURES = Path(__file__).parent / "fixtures"
AT = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)


class ScriptedBroker:
    """Offline broker responses only. No production simulator is shipped."""

    def __init__(self):
        self.raw_account = json.loads((FIXTURES / "account.json").read_text())
        self.positions, self.orders, self.activities = [], {}, []
        self.submits = 0
        self.cancels = []
        self.mode = "ok"
        self.open = True
        self.end = "16:00"
        self.at = AT

    def account(self):
        return copy.deepcopy(self.raw_account)

    def open_orders(self):
        from trader.models import TERMINAL

        return [copy.deepcopy(o) for o in self.orders.values() if o["status"] not in TERMINAL]

    def snapshot(self, after, tracked_ids):
        tracked = set(tracked_ids)
        selected = {o["client_order_id"]: o for o in self.open_orders()}
        selected.update({cid: o for cid, o in self.orders.items() if cid in tracked})
        return Snapshot(
            account=self.account(),
            positions=copy.deepcopy(self.positions),
            orders=copy.deepcopy(list(selected.values())),
            activities=copy.deepcopy(self.activities),
            captured_at=self.at,
        )

    def lookup(self, cid):
        return copy.deepcopy(self.orders.get(cid))

    def submit(self, payload):
        self.submits += 1
        order = {
            **payload,
            "id": "fixture-order-" + str(self.submits),
            "status": "new",
            "filled_qty": "0",
            "filled_avg_price": None,
            "created_at": self.at.isoformat(),
            "updated_at": self.at.isoformat(),
        }
        if self.mode != "timeout_missing":
            self.orders[payload["client_order_id"]] = order
        if self.mode.startswith("timeout"):
            raise httpx.ReadTimeout("test timeout")
        return copy.deepcopy(order)

    def cancel(self, order_id):
        self.cancels.append(order_id)
        for o in self.orders.values():
            if o["id"] == order_id:
                o["status"] = "pending_cancel"
                o["updated_at"] = (self.at + timedelta(seconds=1)).isoformat()

    def clock(self):
        return {"timestamp": self.at.isoformat(), "is_open": self.open}

    def calendar(self, start, end):
        from datetime import date

        dates, day = [], date.fromisoformat(start)
        while day <= date.fromisoformat(end):
            if day.weekday() < 5:
                dates.append({"date": day.isoformat(), "open": "09:30", "close": self.end})
            day += timedelta(days=1)
        return dates

    def asset(self, symbol):
        return {
            "symbol": symbol,
            "class": "us_equity",
            "status": "active",
            "tradable": True,
            "exchange": "NASDAQ",
        }

    def fixture_fill(self, cid, qty="10", price="100", status="filled"):
        o = self.orders[cid]
        quantity, price = D(qty), D(price)
        old_qty = D(o["filled_qty"])
        o.update(
            filled_qty=str(old_qty + quantity),
            filled_avg_price=str(price),
            status=status,
            updated_at=(self.at + timedelta(seconds=2 + len(self.activities))).isoformat(),
        )
        a = {
            "id": "fixture-fill-" + str(len(self.activities)),
            "activity_type": "FILL",
            "qty": str(quantity),
            "price": str(price),
            "side": o["side"],
            "symbol": o["symbol"],
            "order_id": o["id"],
            "transaction_time": o["updated_at"],
            "type": "fill",
        }
        self.activities.append(a)
        cash = D(self.raw_account["cash"])
        existing = next((p for p in self.positions if p["symbol"] == o["symbol"]), None)
        held = D(existing["qty"]) if existing else D(0)
        updated = held + quantity if o["side"] == "buy" else held - quantity
        self.raw_account["cash"] = str(cash + (-1 if o["side"] == "buy" else 1) * quantity * price)
        self.positions = [p for p in self.positions if p["symbol"] != o["symbol"]]
        if updated:
            self.positions.append(
                {
                    "symbol": o["symbol"],
                    "qty": str(updated),
                    "avg_entry_price": "100",
                    "cost_basis": str(updated * D(100)),
                    "market_value": str(updated * price),
                    "unrealized_pl": str(updated * (price - D(100))),
                }
            )
        self.raw_account["equity"] = str(
            D(self.raw_account["cash"]) + sum((D(p["market_value"]) for p in self.positions), D(0))
        )
        return a


class FixtureData:
    def __init__(self):
        self.at = AT
        self.delayed, self.halted = False, False
        self.bid, self.ask = D("99.99"), D("100")
        self.news_items = []

    def quote(self, symbol):
        return Quote(
            symbol=symbol,
            bid=self.bid,
            ask=self.ask,
            bid_size=D("1000"),
            ask_size=D("1000"),
            timestamp=self.at,
            source="offline fixture",
            feed="iex",
            coverage="IEX exchange only",
            delayed=self.delayed,
            halt_checked_at=self.at,
            halted=self.halted,
        )

    def news(self, symbol, since):
        return self.news_items

    def session_close(self, symbol, day):
        if symbol == "SPY":
            return D("500") if day["date"] == "2026-10-21" else D("505")
        if symbol == "QQQ":
            return D("400") if day["date"] == "2026-10-21" else D("408")
        return D("100")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.research_team.now", lambda: AT)
    for module in (
        "trader.models",
        "trader.service",
        "trader.alpaca",
        "trader.research",
        "trader.reports",
        "trader.runner",
        "trader.spending",
        "trader.health",
    ):
        monkeypatch.setattr(module + ".now", lambda: AT)
    for key in ("APCA_API_BASE_URL", "ALPACA_BASE_URL", "ALPACA_TRADING_URL"):
        monkeypatch.delenv(key, raising=False)
    cfg = Settings(
        database=str(tmp_path / "ledger.sqlite"),
        control_dir=str(tmp_path / "controls"),
        universe={
            "ACME": Security(sector="Technology", cik="1"),
            "OTHER": Security(sector="Health", cik="2"),
        },
        cooldown_hours=0,
    )
    broker, data = ScriptedBroker(), FixtureData()
    store = Store(cfg.database)
    service = Service(cfg, store, broker, data)
    monkeypatch.setattr(service, "filings_since", lambda symbol, since: [])
    service.initialize()
    yield service, broker, data
    store.db.close()


@pytest.fixture
def decision():
    return Decision.model_validate_json((FIXTURES / "decision.json").read_text())
