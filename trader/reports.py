from zoneinfo import ZoneInfo

from .accounting import recent_turnover
from .models import ZERO, dec, now
from .observability import traced
from .spending import spending_status


@traced("report.portfolio", inputs=lambda service: {})
def portfolio_report(service):
    with service.lock():
        return _portfolio_report(service)


def _portfolio_report(service):
    store = service.store
    snap, audit = store.get("snapshot"), store.get("audit")
    if not snap or not audit:
        raise RuntimeError("No reconciled snapshot to report")
    account, positions = snap["account"], snap["positions"]
    equity, cash = dec(account["equity"]), dec(account["cash"])
    unrealized = sum((dec(p["unrealized_pl"]) for p in positions), ZERO)
    pnl = equity - dec(store.experiment["initial_capital"]) - dec(audit["capital_flows"])
    reserved = service.reservation_summary()
    exposures = {p["symbol"]: dec(p["market_value"]) / equity if equity else None for p in positions}
    sector_values = {}
    for p in positions:
        security = service.cfg.universe.get(p["symbol"])
        sector = security.sector if security else "UNKNOWN"
        sector_values[sector] = sector_values.get(sector, ZERO) + dec(p["market_value"])
    costs = store.events("model_cost")
    data_costs = store.events("data_cost")
    fills = [a for a in store.activities() if a["activity_type"] == "FILL"]
    intents = {i.get("broker_order_id"): i for i in store.items("intent") if i.get("broker_order_id")}
    slippage = []
    for fill in fills:
        intent = intents.get(fill["order_id"])
        if intent and intent.get("quote"):
            reference = dec(intent["quote"]["ask" if fill["side"] == "buy" else "bid"])
            sign = 1 if fill["side"] == "buy" else -1
            slippage.append(
                {
                    "activity_id": fill["id"],
                    "usd_vs_submission_quote": (dec(fill["price"]) - reference) * dec(fill["qty"]) * sign,
                    "reference_feed": intent["quote"]["feed"],
                }
            )
    warnings = [
        f"{s} appreciated/exceeds entry cap: block additions and review"
        for s, weight in exposures.items()
        if weight is not None and weight > service.cfg.max_position
    ]
    warnings += [
        f"{s} sector exceeds cap: block additions and review"
        for s, value in sector_values.items()
        if equity and value / equity > service.cfg.max_sector
    ]
    warnings += service.position_review_issues(positions, now())
    statuses = {}
    for intent in store.items("intent"):
        statuses[intent["status"]] = statuses.get(intent["status"], 0) + 1
    missed = [
        {
            "client_order_id": o["client_order_id"],
            "status": o["status"],
            "unfilled_quantity": dec(o["qty"]) - dec(o["filled_qty"]),
        }
        for o in store.items("order")
        if o["status"] in {"canceled", "expired", "rejected"} and dec(o["filled_qty"]) < dec(o["qty"])
    ]
    return {
        "experiment": store.experiment,
        "as_of": snap["captured_at"],
        "market_data": {
            "source": "Alpaca",
            "feed": service.cfg.feed,
            "coverage": "IEX exchange only" if service.cfg.feed == "iex" else "US consolidated SIP",
        },
        "research_spending": spending_status(store, service.cfg.research_daily_cost_usd),
        "reconciliation": store.get("reconciliation"),
        "kill_switch": service.kill_path.exists(),
        "cash": cash,
        "reserved_for_buys": reserved,
        "available_capital": max(ZERO, cash - reserved),
        "equity": equity,
        "positions": positions,
        "unrealized_pnl": unrealized,
        "total_pnl": pnl,
        "realized_and_other_pnl": pnl - unrealized,
        "realized_pnl_average_cost_audit": audit["realized_average_cost"],
        "fees": audit["fees"],
        "capital_flows": audit["capital_flows"],
        "dividends_received": audit["dividends"],
        "exposure": exposures,
        "sector_values": sector_values,
        "cash_allocation": cash / equity if equity else None,
        "gross_turnover_30d": recent_turnover(store.activities(), store.active_orders(), now()) / equity
        if equity
        else None,
        "open_orders": store.active_orders(),
        "execution_slippage": slippage,
        "exit_position_age_intervals": audit["holding_periods"],
        "open_positions_since": {p["symbol"]: audit["opened_at"].get(p["symbol"]) for p in positions},
        "decisions": store.items("intent"),
        "decision_status_counts": statuses,
        "missed_or_partial_executions": missed,
        "risk_reviews": warnings,
        "model_cost_usd_estimate": sum((dec(x["body"]["estimated_usd"]) for x in costs), ZERO)
        if all(x["body"]["estimated_usd"] is not None for x in costs)
        else None,
        "model_usage": [x["body"] for x in costs],
        "data_cost_usd": sum((dec(x["body"]["usd"]) for x in data_costs), ZERO) if data_costs else None,
        "operational_events": [
            x
            for x in store.events(
                ("error", "interruption", "reconciliation_failed", "submission_uncertain", "cancel_error")
            )
        ],
        "limitations": [
            "Paper fills are simulated: no reliable queue position, impact, liquidity or live execution inference.",
            "12 weeks can test operations and behavior; cannot establish long-term alpha.",
            "Average-cost audit realized P/L can differ from broker cost-basis conventions.",
            "Model/data costs are separate from simulated portfolio returns; unknown costs are null.",
        ]
        + (
            [
                (
                    "IEX quotes and session marks cover one venue, not the consolidated market; "
                    "slippage uses IEX quotes while Alpaca paper fills use NBBO."
                )
            ]
            if service.cfg.feed == "iex"
            else []
        ),
    }


@traced("report.daily", inputs=lambda service, date=None: {"date": date})
def daily_report(service, date=None):
    """Mark every equity at the same completed regular session, excluding dividends for comparison."""
    store, data = service.store, service.data
    local = now().astimezone(ZoneInfo("America/New_York"))
    date = date or local.date().isoformat()
    saved = store.get(date, "daily_report")
    if saved:
        return saved
    if date != local.date().isoformat():
        raise ValueError("Cannot reconstruct a missed historical portfolio snapshot")
    if any(s["feed"] != service.cfg.feed for s in store.items("daily")):
        raise ValueError("Benchmark feed changed within the experiment; explicit rebaselining required")
    calendar = service.broker.calendar(date, date)
    if not calendar or local.strftime("%H:%M") < calendar[0]["close"]:
        raise ValueError("Daily report requires a completed regular session")
    service.reconcile()
    report = portfolio_report(service)
    marks = {}
    for symbol in {"SPY", "QQQ"} | {p["symbol"] for p in report["positions"]}:
        marks[symbol] = data.session_close(symbol, calendar[0])
    close_equity = report["cash"] + sum(
        (dec(p["qty"]) * marks[p["symbol"]] for p in report["positions"]), ZERO
    )
    comparison_equity = close_equity - dec(report["dividends_received"])
    sample = {
        "date": date,
        "close_equity": close_equity,
        "comparison_equity": comparison_equity,
        "capital_flows": report["capital_flows"],
        "marks": marks,
        "feed": service.cfg.feed,
        "dividend_treatment": "Price returns, split-adjusted; dividends excluded from both sides",
    }
    store.put(date, sample, "daily")
    samples = store.items("daily")
    indexed, peak, max_drawdown = dec(1), dec(1), ZERO
    total_index, total_peak, total_drawdown = dec(1), dec(1), ZERO
    baseline_index = None
    returns = []
    baseline = samples[0]
    previous = None
    for current in samples:
        prior_equity = (
            dec(previous["comparison_equity"]) if previous else dec(store.experiment["initial_capital"])
        )
        prior_flows = dec(previous["capital_flows"]) if previous else ZERO
        delta_flow = dec(current["capital_flows"]) - prior_flows
        daily = (
            (dec(current["comparison_equity"]) - delta_flow) / prior_equity - 1 if prior_equity > 0 else None
        )
        prior_total = dec(previous["close_equity"]) if previous else dec(store.experiment["initial_capital"])
        daily_total = (
            (dec(current["close_equity"]) - delta_flow) / prior_total - 1 if prior_total > 0 else None
        )
        total_index = (
            total_index * (1 + daily_total) if total_index is not None and daily_total is not None else None
        )
        if total_index is not None:
            total_peak = max(total_peak, total_index)
            total_drawdown = max(total_drawdown, 1 - total_index / total_peak)
        indexed = indexed * (1 + daily) if indexed is not None and daily is not None else None
        if baseline_index is None:
            baseline_index = indexed
        if indexed is not None:
            peak = max(peak, indexed)
            max_drawdown = max(max_drawdown, 1 - indexed / peak)
        returns.append(
            {
                "date": current["date"],
                "equity": current["close_equity"],
                "return": daily,
                "total_return": daily_total,
            }
        )
        previous = current
    baseline_calendar = service.broker.calendar(baseline["date"], baseline["date"])[0]
    # Re-fetch historical baseline so subsequent splits cannot corrupt cached benchmark returns.
    baseline_marks = {symbol: data.session_close(symbol, baseline_calendar) for symbol in ("SPY", "QQQ")}
    benchmarks = {
        symbol: dec(sample["marks"][symbol]) / baseline_marks[symbol] - 1 for symbol in ("SPY", "QQQ")
    }
    calendar_dates = {d["date"] for d in service.broker.calendar(baseline["date"], date)}
    missing = sorted(calendar_dates - {s["date"] for s in samples})
    report.update(
        daily_equity=returns,
        matching_dates={"start": baseline["date"], "end": date},
        close_equity=close_equity,
        matching_period_return=indexed / baseline_index - 1
        if indexed is not None and baseline_index
        else None,
        experiment_price_return=indexed - 1 if indexed is not None else None,
        experiment_total_return=total_index - 1 if total_index is not None else None,
        total_return_max_drawdown=total_drawdown,
        total_return_method="Flow-adjusted close equity including net distributions and broker fees; model/data costs excluded. Benchmarks below are price-only, not comparable total returns.",
        max_drawdown=max_drawdown,
        benchmarks=benchmarks,
        marks=marks,
        missing_sessions=missing,
        benchmark_method=sample["dividend_treatment"],
        baseline_note="Comparison starts at first recorded close; no invented pre-experiment or missed monitoring.",
        flow_timing_note="Daily flow-adjusted returns treat external flows as end-of-day; intraday flow timing is approximate.",
        undefined_return_note="Returns are null when prior comparison equity is nonpositive; cannot chain a percentage return through zero.",
    )
    store.put(date, report, "daily_report")
    store.log("daily_report", {"date": date, "equity": close_equity, "benchmarks": benchmarks})
    return report
