"""Curated, read-only dashboard snapshots. Broker reconciliation remains authoritative."""

import json

from .models import dec, now
from .observability import emit
from .reports import portfolio_report


def publish_snapshot(service):
    report = portfolio_report(service)
    store = service.store
    runs = []
    for key, body in store.db.execute(
        "SELECT key,body FROM documents WHERE scope=? AND kind='research_audit' ORDER BY rowid DESC LIMIT 30",
        (store.scope,),
    ):
        audit = json.loads(body)
        accepted = store.get(key, "research_result")
        sources = audit.get("sources", [])
        runs.append(
            {
                "id": key,
                "status": "accepted" if accepted else "failed" if audit.get("stopped") else "incomplete",
                "decision": accepted.get("decision") if accepted else None,
                "reports": audit.get("reports", {}),
                "calculations": audit.get("calculations", {}),
                "cost": audit.get("estimated_usd"),
                "model_calls": audit.get("model_calls", 0),
                "tool_calls": audit.get("tool_calls", 0),
                "sources": [
                    {k: s[k] for k in ("id", "title", "url", "type", "as_of", "published_at") if k in s}
                    for s in sources
                ],
            }
        )
    daily = store.items("daily_report")
    snapshot = {
        "generated_at": now(),
        "experiment_id": store.scope,
        "started_at": store.experiment.get("started_at"),
        "portfolio": {
            k: report[k]
            for k in (
                "as_of",
                "kill_switch",
                "equity",
                "cash",
                "total_pnl",
                "unrealized_pnl",
                "reserved_for_buys",
                "model_cost_usd_estimate",
                "research_spending",
                "reconciliation",
                "market_data",
                "limitations",
                "risk_reviews",
                "capital_flows",
            )
        },
        "positions": [
            {k: p.get(k) for k in ("symbol", "qty", "market_value", "unrealized_pl", "avg_entry_price")}
            for p in report["positions"]
        ],
        "open_orders": [
            {k: o.get(k) for k in ("symbol", "side", "qty", "filled_qty", "status")}
            for o in report["open_orders"]
        ],
        "daily": [
            {
                k: d.get(k)
                for k in (
                    "as_of",
                    "daily_equity",
                    "benchmarks",
                    "matching_period_return",
                    "max_drawdown",
                    "missing_sessions",
                    "benchmark_method",
                )
            }
            for d in daily[-1:]
        ],
        "research_cost_known_usd": sum(
            (dec(x["estimated_usd"]) for x in report["model_usage"] if x.get("estimated_usd") is not None),
            dec(0),
        ),
        "research_cost_unknown_calls": sum(x.get("estimated_usd") is None for x in report["model_usage"]),
        "runs": runs,
        "coverage": "Research history comes from saved audits; detailed traces exist only since observability deployment.",
    }
    emit("dashboard.snapshot", snapshot, experiment_id=store.scope)
    return snapshot
