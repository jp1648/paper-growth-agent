"""Explicit supervised acceptance. Never starts or resumes autonomous trading."""

import argparse
import fcntl
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import ROUND_DOWN
from zoneinfo import ZoneInfo

import httpx

from trader.accounting import LedgerError
from trader.alpaca import AlpacaBroker, AlpacaData, safe_error
from trader.config import Settings
from trader.evidence import EvidenceProvider
from trader.models import TERMINAL, D, Decision, dt, dumps, now
from trader.research_team import DeepResearchTeam
from trader.service import Service
from trader.spending import spending_status
from trader.store import Store


@contextmanager
def exclusive(service):
    # Hold the listener lock so a restarted listener cannot enter the unpaused window.
    path = service.store.control_path(service.cfg.control_dir, service.account_id, ".listener.lock")
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def research_price(service, symbol):
    clock = service.broker.clock()
    if clock["is_open"]:
        return service.data.quote(symbol).model_dump(mode="json")
    zone = ZoneInfo("America/New_York")
    stamp = dt(clock["timestamp"])
    day = stamp.astimezone(zone).date()
    sessions = service.broker.calendar((day - timedelta(days=14)).isoformat(), day.isoformat())
    completed = [
        session
        for session in sessions
        if datetime.fromisoformat(session["date"] + "T" + session["close"]).replace(tzinfo=zone) <= stamp
    ]
    if not completed:
        raise RuntimeError("No completed regular session available for research reference")
    session = max(completed, key=lambda item: item["date"])
    close = service.data.session_close(symbol, session)
    return {
        "symbol": symbol,
        "reference_price": str(close),
        "ask": str(close),
        "timestamp": datetime.fromisoformat(session["date"] + "T" + session["close"])
        .replace(tzinfo=zone)
        .isoformat(),
        "source": "historical regular-session close; ask field is a reference only",
        "feed": service.cfg.feed,
        "historical": True,
        "executable": False,
        "coverage": "IEX exchange only" if service.cfg.feed == "iex" else "consolidated SIP",
    }


def confirmed_fill(store, cid):
    order = store.get(cid, "order")
    if not order:
        return None
    fills = [
        a
        for a in store.items("activity")
        if a.get("order_id") == order["id"] and a["activity_type"] == "FILL"
    ]
    if D(order["filled_qty"]) == 1 and sum((D(fill["qty"]) for fill in fills), D(0)) == 1:
        return {"status": "complete", "order_id": order["id"], "fills": fills}
    return None


def run(
    service,
    mode,
    symbol,
    run_key,
    research_key=None,
    reviewed=False,
    team=None,
    sleep=time.sleep,
    *,
    unrestricted_research=False,
    finalize_from=None,
):
    store, cfg = service.store, service.cfg
    service.require_experiment()
    if not service.kill_path.exists():
        raise RuntimeError("Acceptance requires the persistent trading pause")
    original_pause = service.kill_path.read_bytes()
    cid = service.client_order_id("explicit-paper-smoke:" + symbol)
    attempted_order = False
    with exclusive(service), service.lock():
        try:
            if symbol not in cfg.universe:
                raise ValueError("Symbol outside configured universe")
            if any(store.get(run_key, kind) for kind in ("acceptance", "research_audit", "research_result")):
                raise RuntimeError("Acceptance run ID already attempted; inspect its saved evidence")
            if mode == "paper-smoke":
                prior = store.get(research_key, "acceptance")
                if (
                    not reviewed
                    or not prior
                    or prior.get("status") != "complete"
                    or prior.get("mode") != "research"
                    or prior.get("symbol") != symbol
                ):
                    raise RuntimeError("Paper smoke requires reviewed successful research for this symbol")
                if store.get(cid, "intent"):
                    raise RuntimeError("Paper smoke intent already exists; reconcile without resubmission")
            record = {
                "mode": mode,
                "symbol": symbol,
                "status": "started",
                "at": now(),
                "research_key": research_key,
                "unrestricted_research": unrestricted_research,
                "finalize_from": finalize_from,
            }
            store.put(run_key, record, "acceptance")
            store.log("acceptance_started", {"run_key": run_key, **record})
            snapshot = service.reconcile()
            if mode == "research":
                remaining = spending_status(store, cfg.research_daily_cost_usd)["remaining_usd"]
                cap = (
                    None
                    if unrestricted_research
                    else min(D("4.75"), cfg.research_max_cost_usd, remaining - D(".25"))
                )
                if cap is not None and cap <= 0:
                    raise RuntimeError("Insufficient daily budget including acceptance buffer")
                bounded = cfg.model_copy(
                    update={
                        "research_max_cost_usd": cfg.research_max_cost_usd if cap is None else cap,
                        "research_max_output_tokens": 5000,
                        "research_max_model_calls": 40,
                    }
                )
                worker = team or DeepResearchTeam(
                    bounded,
                    store,
                    EvidenceProvider(bounded, store, service.data),
                    unrestricted_research=unrestricted_research,
                )
                quote = research_price(service, symbol)
                context = {
                    "symbol": symbol,
                    "run_key": run_key,
                    "fresh": True,
                    "research_only": True,
                    "quote": quote,
                    "current_timestamp": now().isoformat(),
                    "portfolio": {"snapshot": snapshot.model_dump(mode="json"), "prior_thesis": None},
                }
                result = (
                    worker.run(context, finalize_from=finalize_from) if finalize_from else worker.run(context)
                )
                if set(result["team_reports"]) != {"business", "catalysts", "valuation", "adversarial"}:
                    raise ValueError("Incomplete specialist team")
                proposal = Decision.model_validate(result["decision"])
                if proposal.symbol != symbol:
                    raise ValueError("Research decision changed the requested symbol")
                result["quote"] = quote
                store.put(run_key, result, "acceptance_research")
                record.update(status="complete", estimated_cap_usd=cap)
            else:
                if not service.broker.clock()["is_open"]:
                    raise RuntimeError("Paper smoke requires a regular market session")
                if snapshot.positions or service.broker.open_orders():
                    raise RuntimeError("Paper smoke requires no positions or open orders")
                quote = service.data.quote(symbol)
                raw = dict(store.get(research_key, "acceptance_research")["decision"])
                raw.update(
                    symbol=symbol,
                    action="BUY",
                    quantity="1",
                    limit_price=str(
                        (quote.ask * (1 + cfg.price_tolerance)).quantize(D(".01"), rounding=ROUND_DOWN)
                    ),
                    next_review=(now() + timedelta(days=1)).isoformat(),
                    business_attraction="Operator-selected paper connectivity test",
                    why_now="Supervised one-share paper acceptance",
                    expected_outcome="Verify simulated fill; position remains held",
                )
                proposal = Decision.model_validate(raw)
                service.kill_path.unlink()
                attempted_order = True
                submitted = service.execute(
                    proposal,
                    "explicit-paper-smoke:" + symbol,
                    {"acceptance_run_key": run_key, "reviewed_research": research_key},
                )
                if submitted["status"] not in {"submitted", "complete"}:
                    raise RuntimeError("Paper order failed deterministic admission: " + dumps(submitted))
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    try:
                        service.reconcile()
                    except (LedgerError, httpx.HTTPError):
                        # Broker positions can arrive before their matching fill activities.
                        sleep(5)
                        continue
                    order = store.get(cid, "order")
                    fill = confirmed_fill(store, cid)
                    if fill:
                        record.update(fill)
                        break
                    if order["status"] in TERMINAL:
                        raise RuntimeError("Paper order terminated without a confirmed fill")
                    sleep(5)
                else:
                    raise RuntimeError("No broker-confirmed fill within 180 seconds")
            store.put(run_key, record, "acceptance")
            return record
        except BaseException as error:
            if "record" in locals():
                record.update(status="failed", error=safe_error(error))
                store.put(run_key, record, "acceptance")
            raise
        finally:
            service.kill_path.write_bytes(original_pause)
            if attempted_order:
                # Preserve ambiguous intents/reservations; never submit a replacement or liquidate.
                try:
                    order = service.broker.lookup(cid)
                    if order and order["status"] not in TERMINAL:
                        service.request_cancel(order)
                    service.reconcile()
                    fill = confirmed_fill(store, cid)
                    if fill:
                        record.update(fill)
                        store.put(run_key, record, "acceptance")
                except Exception as error:  # noqa: BLE001 - preserve pause even when cleanup fails
                    store.log("acceptance_cleanup_pending", {"run_key": run_key, "error": safe_error(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["research", "paper-smoke"])
    parser.add_argument("symbol")
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--listener-stopped", action="store_true", required=True)
    parser.add_argument("--research-key")
    parser.add_argument("--reviewed", action="store_true")
    parser.add_argument(
        "--finalize-from",
        help="Research only: synthesize a new final decision from a failed run's four completed reports, without repeating investigation",
    )
    parser.add_argument(
        "--unrestricted-research",
        action="store_true",
        help="Supervised test only: remove research spend/count/context/deadline quotas; retain security/data validation. Run under an external watchdog.",
    )
    args = parser.parse_args()
    if args.finalize_from and args.mode != "research":
        parser.error("--finalize-from is only available for research")
    cfg = Settings.load(args.config)
    store = Store(cfg.database)
    try:
        print(
            dumps(
                run(
                    Service(cfg, store, AlpacaBroker(cfg), AlpacaData(cfg)),
                    args.mode,
                    args.symbol,
                    args.run_key,
                    args.research_key,
                    args.reviewed,
                    unrestricted_research=args.unrestricted_research,
                    finalize_from=args.finalize_from,
                )
            )
        )
    finally:
        store.db.close()


if __name__ == "__main__":
    main()
