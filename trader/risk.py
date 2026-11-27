from datetime import date, datetime, timedelta
from decimal import InvalidOperation
from zoneinfo import ZoneInfo

from .accounting import recent_turnover, remaining, reserved
from .models import ZERO, dec, dt


def daily_loss(account, activities, calendar, at, fraction):
    """Use broker prior-close equity, never the first balance observed after downtime."""
    local = at.astimezone(ZoneInfo("America/New_York"))
    result = {"date": local.date().isoformat(), "valid": False, "breached": False}
    try:
        sessions = {}
        for session in calendar:
            day = session["date"]
            if date.fromisoformat(day).isoformat() != day or day in sessions:
                raise ValueError("Ambiguous broker calendar")
            sessions[day] = session
        prior = max(day for day in sessions if day < result["date"])
        session = sessions[prior]
        if account.get("balance_asof") != prior or session["close"] != "16:00":
            raise ValueError("Prior-close date or regular-close baseline is unverified")
        baseline = dec(account["last_equity"])
        if baseline <= 0 or dec(account["intraday_adjustments"]) != 0:
            raise ValueError("Prior-close equity or intraday adjustments are unsupported")
        cutoff = datetime.fromisoformat(prior + "T16:00:00").replace(tzinfo=local.tzinfo)
        flows = ZERO
        for activity in activities:
            if activity["activity_type"] not in {"CSD", "CSW"}:
                continue
            if activity.get("transaction_time"):
                stamp = dt(activity["transaction_time"])
                if stamp > at:
                    raise ValueError("Future capital movement")
                if stamp == cutoff:
                    raise ValueError("Capital movement coincides with baseline cutoff")
                after = stamp > cutoff
            else:
                day = date.fromisoformat(activity["date"])
                if day > local.date() or day == cutoff.date():
                    raise ValueError("Capital movement cannot be placed relative to prior close")
                after = day > cutoff.date()
            if after:
                flows += dec(activity["net_amount"])
        pnl = dec(account["equity"]) - baseline - flows
        threshold = baseline * fraction
        result.update(
            valid=True,
            baseline_equity=baseline,
            baseline_date=prior,
            capital_flows=flows,
            pnl=pnl,
            threshold_usd=threshold,
            breached=pnl <= -threshold,
        )
    except (KeyError, ValueError, TypeError, InvalidOperation, StopIteration) as error:
        result["error"] = f"Daily loss baseline unavailable: {type(error).__name__}"
    return result


def check(
    settings,
    decision,
    snapshot,
    quote,
    asset,
    clock,
    calendar,
    orders,
    activities,
    last_trade,
    reconciled,
    killed,
    ends_at,
    at,
):
    reasons = []
    if not reconciled:
        reasons.append("Account reconciliation failed")
    if killed:
        reasons.append("Kill switch is active")
    if at >= dt(ends_at):
        reasons.append("Experiment ended; review open positions without automatic liquidation")
    account = snapshot.account
    if account.get("currency") != "USD":
        reasons.append("Only USD accounts are supported")
    if account.get("status") != "ACTIVE" or any(
        account.get(k) is not False for k in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
    ):
        reasons.append("Account is not eligible to trade")
    equity, cash = dec(account["equity"]), dec(account["cash"])
    if equity <= 0 or cash < 0 or dec(account.get("short_market_value", "0")) != 0:
        reasons.append("Cash-only account invariant violated")
    if not clock.get("is_open") or abs((at - dt(clock["timestamp"])).total_seconds()) > 30:
        reasons.append("Market closed or broker clock stale")
    local = at.astimezone(ZoneInfo("America/New_York"))
    day = next((d for d in calendar if d["date"] == local.date().isoformat()), None)
    if day is None or not (day["open"] <= local.strftime("%H:%M") < day["close"]):
        reasons.append("Outside broker calendar regular session")
    if asset.get("class") != "us_equity" or asset.get("status") != "active" or not asset.get("tradable"):
        reasons.append("Security inactive or ineligible")
    if asset.get("exchange") not in {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "NYSEARCA"}:
        reasons.append("Security is not on an allowed US exchange")
    if decision.symbol not in settings.universe:
        reasons.append("Security lacks operator-verified common-stock and sector metadata")
    if quote.symbol != decision.symbol or asset.get("symbol") != decision.symbol:
        reasons.append("Quote/asset symbol mismatch")
    if quote.feed != settings.feed:
        reasons.append("Quote feed does not match configured market data")
    age = (at - quote.timestamp).total_seconds()
    if quote.delayed or age < -5 or age > settings.quote_max_age_seconds or quote.bid > quote.ask:
        reasons.append("Quote delayed, stale, future-dated, or crossed")
    if (
        quote.halted is not False
        or not 0 <= (at - quote.halt_checked_at).total_seconds() <= settings.halt_max_age_seconds
    ):
        reasons.append("Security halted or halt status unavailable/stale")
    reference = quote.ask if decision.side == "buy" else quote.bid
    spread = (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2)
    if spread > settings.max_spread_fraction:
        reasons.append("Quoted spread exceeds execution limit")
    displayed = quote.ask_size if decision.side == "buy" else quote.bid_size
    if displayed is None or displayed <= 0:
        reasons.append("Displayed executable quote size unavailable")
    elif decision.quantity > displayed * settings.max_quote_participation:
        reasons.append("Order exceeds displayed quote participation limit")
    if abs(decision.limit_price / reference - 1) > settings.price_tolerance:
        reasons.append("Limit price outside configured quote tolerance")
    if decision.next_review <= at:
        reasons.append("Decision review date is already due")
    positions = {p["symbol"]: p for p in snapshot.positions}
    held = dec(positions.get(decision.symbol, {}).get("qty", "0"))
    if decision.action == "BUY" and held > 0:
        reasons.append("Use ADD for an existing position")
    if decision.action == "ADD" and held <= 0:
        reasons.append("ADD requires a position")
    if any(o["symbol"] == decision.symbol for o in orders):
        reasons.append("Outstanding order already exists for this symbol")
    protective_exit = decision.side == "sell" and decision.exit_reason in {
        "risk",
        "thesis_invalid",
        "catalyst_failed",
    }
    if (
        not protective_exit
        and settings.cooldown_hours > 0
        and last_trade
        and at - dt(last_trade) < timedelta(hours=settings.cooldown_hours)
    ):
        reasons.append("Symbol cooldown is active")
    notional = decision.quantity * decision.limit_price
    if notional > equity * settings.max_order_equity:
        reasons.append("Order exceeds per-order equity limit")
    if (
        not protective_exit
        and equity > 0
        and recent_turnover(activities, orders, at) + notional > equity * settings.turnover_30d
    ):
        reasons.append("Rolling 30-day gross turnover ceiling exceeded")
    if decision.side == "buy":
        # Alpaca cash is the cash ledger, not margin buying power and not net of open-order holds.
        available = cash - reserved(orders, settings.fee_buffer)
        if notional + settings.fee_buffer > available:
            reasons.append("Insufficient unreserved cash")
        try:
            non_marginable = dec(account["non_marginable_buying_power"])
            if non_marginable < 0:
                raise ValueError("Negative non-marginable buying power")
            # Broker balance already reflects holds; do not subtract local reservations twice.
            if notional + settings.fee_buffer > non_marginable:
                reasons.append("Insufficient broker non-marginable buying power")
        except (KeyError, ValueError, InvalidOperation):
            reasons.append("Broker non-marginable buying power unavailable or invalid")
        exposure = {s: max(ZERO, dec(p["market_value"])) for s, p in positions.items()}
        for o in orders:
            if o["side"] == "buy":
                exposure[o["symbol"]] = exposure.get(o["symbol"], ZERO) + remaining(o) * dec(o["limit_price"])
        exposure[decision.symbol] = exposure.get(decision.symbol, ZERO) + notional
        if equity <= 0 or exposure[decision.symbol] > equity * settings.max_position:
            reasons.append("Entry concentration limit exceeded; block addition and reassess")
        if len([x for x in exposure.values() if x > 0]) > settings.max_positions:
            reasons.append("Maximum position count exceeded")
        if any(s not in settings.universe for s in exposure):
            reasons.append("Unknown sector exposure; cannot add risk")
        elif decision.symbol in settings.universe:
            sector = settings.universe[decision.symbol].sector
            sector_value = sum(
                (value for s, value in exposure.items() if settings.universe[s].sector == sector), ZERO
            )
            if sector_value > equity * settings.max_sector:
                reasons.append("Projected sector limit exceeded")
    else:
        pending_sell = sum(
            (remaining(o) for o in orders if o["symbol"] == decision.symbol and o["side"] == "sell"), ZERO
        )
        if decision.quantity > held - pending_sell:
            reasons.append("Sell exceeds uncommitted shares")
        if decision.action == "SELL" and decision.quantity != held:
            reasons.append("SELL must close the position; use REDUCE for a partial exit")
        if decision.exit_reason == "none":
            reasons.append("Exit/reduction requires an explicit reason")
    return reasons
