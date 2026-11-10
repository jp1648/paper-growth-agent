from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from .models import Decision, dec, dt, dumps, now
from .observability import traced


class GraphState(TypedDict, total=False):
    symbol: str
    run_key: str
    fresh: bool
    trigger: str
    research_only: bool
    finalize_from: str
    study_id: str
    study_stratum: str
    portfolio: dict
    evidence: dict
    quote: dict | None
    research_reference: dict | None
    research: dict
    valuation: dict
    catalysts: dict
    critique: dict
    team_reports: dict
    team_report_times: dict
    calculations: dict
    reused_reports: list
    decision: dict
    risk: list[str]
    execution: dict
    report: dict


def research_input(service, symbol, *, deferred):
    if deferred:
        clock = service.broker.clock()
        if clock["is_open"] is False:
            stamp, observed = dt(clock["timestamp"]), now()
            if abs((observed - stamp).total_seconds()) > 30:
                raise ValueError("Broker clock stale; historical research reference unavailable")
            zone = ZoneInfo("America/New_York")
            day = stamp.astimezone(zone).date()
            start = day - timedelta(days=14)
            sessions = service.broker.calendar(start.isoformat(), day.isoformat())
            if not isinstance(sessions, list) or len(sessions) > 15:
                raise ValueError("Invalid historical research calendar")
            dates, completed = [], []
            for session in sessions:
                session_day = date.fromisoformat(session["date"])
                opening, closing = time.fromisoformat(session["open"]), time.fromisoformat(session["close"])
                if (
                    session_day.isoformat() != session["date"]
                    or not start <= session_day <= day
                    or session_day.weekday() >= 5
                    or opening.tzinfo is not None
                    or closing.tzinfo is not None
                    or not time(9, 30) == opening < closing <= time(16)
                ):
                    raise ValueError("Invalid regular-session research calendar boundaries")
                dates.append(session_day)
                closing_at = datetime.combine(session_day, closing, zone)
                if closing_at <= min(stamp, observed):
                    completed.append((closing_at, session))
            if dates != sorted(set(dates)):
                raise ValueError("Research calendar requires unique increasing session dates")
            if not completed:
                raise ValueError("No completed regular session available for research reference")
            closing_at, session = completed[-1]
            price = dec(service.data.session_close(symbol, session))
            if price <= 0:
                raise ValueError("Nonpositive historical research reference")
            return {
                "quote": None,
                "research_reference": {
                    "symbol": symbol,
                    "price": str(price),
                    "basis": "last_validated_regular_session_minute_bar_close",
                    "method": "Last available split-adjusted 1Min bar close within 300 seconds before the scheduled session close; not an official consolidated closing-auction price",
                    "session_bounds_source": "https://paper-api.alpaca.markets/v2/calendar",
                    "session": session,
                    "session_closed_at": closing_at.isoformat(),
                    "retrieved_at": now().isoformat(),
                    "feed": service.cfg.feed,
                    "source": f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                    "timeframe": "1Min",
                    "adjustment": "split",
                    "coverage": "IEX exchange only" if service.cfg.feed == "iex" else "US consolidated SIP",
                    "historical": True,
                    "executable": False,
                },
            }
    return {"quote": service.data.quote(symbol).model_dump(mode="json"), "research_reference": None}


def build_graph(service, team):
    store = service.store

    def portfolio(s):
        if service.kill_path.exists():
            raise RuntimeError("Kill switch is active; research paused")
        study = store.get("active", "paper_study")
        if study is not None and (
            s.get("study_id") != study["schedule"]["study_id"]
            or s.get("study_stratum") not in {"scheduled", "safety"}
        ):
            raise RuntimeError("Active study research must pass through the study dispatcher")
        if study is not None:
            if s["study_stratum"] == "scheduled":
                claim = store.get(dumps([s["study_id"], s["run_key"]]), "paper_slot")
                if (
                    not claim
                    or claim.get("symbol") != s["symbol"]
                    or claim.get("outcome") == "missed"
                    or claim.get("schedule_sha256") != study["schedule_sha256"]
                ):
                    raise RuntimeError("Scheduled graph has no matching durable slot claim")
            else:
                job = store.get(s["run_key"], "job")
                if (
                    not s["run_key"].startswith(s["study_id"] + ":safety:")
                    or not job
                    or job.get("status") != "running"
                ):
                    raise RuntimeError("Safety graph has no matching running study job")
            if not store.claim_paper_slot(
                s["study_id"], s["run_key"], {"symbol": s["symbol"], "at": now()}, kind="paper_invocation"
            ):
                raise RuntimeError("Study graph invocation already consumed; no automatic retry")
        snap = service.reconcile()
        previous = store.get(s["symbol"], "thesis")
        return {
            "portfolio": {
                "snapshot": snap.model_dump(mode="json"),
                "prior_thesis": previous,
                "limits": {
                    k: getattr(service.cfg, k)
                    for k in ("max_position", "max_sector", "max_positions", "turnover_30d", "cooldown_hours")
                },
                "available_capital": str(dec(snap.account["cash"]) - service.reservation_summary()),
            }
        }

    def research(s):
        cached = store.get(s["run_key"], "research_result")
        if cached:
            if cached["decision"]["symbol"] != s["symbol"]:
                raise ValueError("Research run key belongs to another symbol")
            commit_research(s, cached)
            return cached
        pricing = research_input(service, s["symbol"], deferred=bool(s.get("research_only")))
        context = {**s, **pricing, "current_timestamp": now().isoformat()}
        result = (
            team.run(context, finalize_from=s["finalize_from"])
            if s.get("finalize_from")
            else team.run(context)
        )
        result.update(pricing)
        proposal = Decision.model_validate(result["decision"])
        if proposal.symbol != s["symbol"]:
            raise ValueError("Decision changed the requested symbol")
        ids = {x["id"] for x in result["evidence"]["sources"]}
        for output in (
            result["research"],
            result["valuation"],
            result["catalysts"],
            result["critique"],
            result["decision"],
        ):
            if not output["evidence_ids"] or not set(output["evidence_ids"]).issubset(ids):
                raise ValueError("Research/decision cited unknown evidence")
        if result["critique"]["unresolved_material_gaps"] and proposal.action in {"BUY", "ADD"}:
            result["decision"].update(action="WATCH", quantity="0")
            store.log("adversarial_veto", {"symbol": s["symbol"], "critique": result["critique"]})
        legacy = store.get(s["run_key"], "graph_decision")
        if legacy and legacy != result["decision"]:
            raise ValueError("Run key already has a different immutable decision")
        result["research_completed_at"] = now().isoformat()
        store.put(s["run_key"], result, "research_result")
        commit_research(s, result)
        return result

    def commit_research(s, result):
        if store.get(s["run_key"], "research_committed"):
            return
        store.put(s["run_key"], result["decision"], "graph_decision")
        existing = store.get(s["symbol"], "thesis")
        if not existing or dt(existing["reviewed_at"]) <= dt(result["research_completed_at"]):
            store.put(
                s["symbol"],
                {
                    "run_key": s["run_key"],
                    "decision": result["decision"],
                    "reviewed_at": result["research_completed_at"],
                    "reference_price": (
                        result["research_reference"]["price"]
                        if result.get("research_reference")
                        else result["quote"]["ask"]
                    ),
                    "reference_basis": result.get("research_reference")
                    or {
                        "basis": "broker_quote_ask",
                        "timestamp": result["quote"]["timestamp"],
                        "feed": result["quote"]["feed"],
                    },
                    "evidence_ids": sorted(x["id"] for x in result["evidence"]["sources"]),
                    "valuation": result["valuation"],
                    "team_reports": result["team_reports"],
                    "team_report_times": result.get("team_report_times", {}),
                    "research_sources": result["evidence"]["sources"],
                    "research_calculations": result.get("calculations", {}),
                },
                "thesis",
            )
        store.log("research_decision", {**s, **result})
        store.put(s["run_key"], {"at": now()}, "research_committed")

    def risk(s):
        if s.get("research_only") or s["symbol"] in service.cfg.research_only_universe:
            return {"risk": []}
        d = Decision.model_validate(s["decision"])
        prior = store.get(service.client_order_id(s["run_key"]), "intent")
        # Recovery needs no new quote; execution resolves the original immutable intent.
        reasons = service.evaluate(d)[0] if not prior and d.action not in {"HOLD", "WATCH"} else []
        store.log("risk_preview", {"run_key": s["run_key"], "reasons": reasons})
        return {"risk": reasons}

    def execution(s):
        if s["symbol"] in service.cfg.research_only_universe:
            result = {"status": "research_only", "reason": "Symbol is excluded from execution universe"}
        else:
            result = (
                {"status": "deferred", "reason": "Research outside regular trading hours"}
                if s.get("research_only")
                else service.execute(
                    s["decision"],
                    s["run_key"],
                    {
                        "sources": s["evidence"],
                        "research": s["research"],
                        "valuation": s["valuation"],
                        "critique": s.get("critique"),
                        "catalysts": s["catalysts"],
                    },
                )
            )
        thesis = store.get(s["symbol"], "thesis")
        if thesis and thesis.get("run_key") == s["run_key"]:
            thesis["execution"] = result
            store.put(s["symbol"], thesis, "thesis")
        return {"execution": result}

    def reporting(s):
        from .reports import portfolio_report

        service.reconcile()
        return {"report": portfolio_report(service)}

    graph = StateGraph(GraphState)
    nodes = {
        "portfolio_context": portfolio,
        "deep_research_team": research,
        "deterministic_risk_gate": risk,
        "paper_execution": execution,
        "reconciliation_reporting": reporting,
    }
    previous = START
    for name, fn in nodes.items():
        graph.add_node(name, traced("graph." + name)(fn))
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()
