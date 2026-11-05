"""Explicit opt-in: this test can buy ONE share with simulated dollars and leave it held."""

import os
import time
from datetime import timedelta
from decimal import ROUND_DOWN
from pathlib import Path

import httpx
import pytest

from trader.accounting import LedgerError
from trader.alpaca import AlpacaBroker, AlpacaData
from trader.config import Settings
from trader.models import TERMINAL, D, Decision, dumps, now
from trader.service import Service
from trader.store import Store

pytestmark = [
    pytest.mark.paper,
    pytest.mark.skipif(
        os.getenv("PAPER_SMOKE") != "1", reason="Set PAPER_SMOKE=1 explicitly to submit one paper buy"
    ),
]


def test_broker_confirms_simulated_fill():
    symbol = os.environ["PAPER_SMOKE_SYMBOL"]
    cfg = Settings.load(os.getenv("PAPER_CONFIG", "config.toml"))
    store = Store(cfg.database)
    service = Service(cfg, store, AlpacaBroker(cfg), AlpacaData(cfg))
    service.require_experiment()
    assert symbol in cfg.universe, "Smoke symbol must be operator-verified in the universe"
    assert service.broker.clock()["is_open"], "Run during a regular market session"
    quote = service.data.quote(symbol)
    limit = (quote.ask * (1 + cfg.price_tolerance)).quantize(D(".01"), rounding=ROUND_DOWN)
    raw = __import__("json").loads((Path(__file__).parent / "fixtures" / "decision.json").read_text())
    raw.update(
        symbol=symbol,
        action="BUY",
        quantity="1",
        limit_price=str(limit),
        business_attraction="Operator-selected paper connectivity test, not an investment thesis",
        price_attraction="Bounded limit price relative to observed ask",
        why_now="Explicit opt-in operational test during a regular market session",
        expected_outcome="Verify broker-confirmed simulated execution; no performance inference",
        evidence_ids=["operator-opt-in-paper-smoke"],
        next_review=(now() + timedelta(days=1)).isoformat(),
    )
    intent_key = "explicit-paper-smoke:" + symbol
    import hashlib

    cid = "pg-" + hashlib.sha256(f"{store.scope}:{intent_key}".encode()).hexdigest()[:40]
    saved = store.get(cid, "intent")
    proposal = Decision.model_validate(saved["decision"] if saved else raw)
    result = service.execute(proposal, intent_key, {"test": "explicit operator opt-in"})
    assert result["status"] in {"submitted", "complete"}, dumps(result)
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                service.reconcile()
            except (LedgerError, httpx.HTTPError):
                time.sleep(5)
                continue
            order = store.get(cid, "order")
            fills = [
                a
                for a in store.items("activity")
                if a.get("order_id") == order["id"] and a["activity_type"] == "FILL"
            ]
            if D(order["filled_qty"]) > 0 and fills:
                print(
                    dumps(
                        {
                            "broker_confirmed_paper_fill": True,
                            "order_id": order["id"],
                            "fills": fills,
                            "note": "This is simulated; the position remains held. No automatic liquidation.",
                        }
                    )
                )
                return
            if order["status"] in TERMINAL:
                break
            time.sleep(5)
        pytest.fail("Broker did not confirm a simulated fill within 180 seconds; no fill is claimed")
    finally:
        order = service.broker.lookup(cid)
        if order and order["status"] not in TERMINAL:
            service.request_cancel(order)
            for _ in range(12):
                time.sleep(5)
                try:
                    service.reconcile()
                except (LedgerError, httpx.HTTPError) as error:
                    print("Reconciliation still pending:", type(error).__name__)
                    continue
                if store.get(cid, "order")["status"] in TERMINAL:
                    break
            print("Cancellation requested; inspect broker/ledger for late fills or outstanding orders.")
        store.db.close()
