from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .models import Decision, dec, dt, now
from .observability import traced


class GraphState(TypedDict, total=False):
    symbol: str
    run_key: str
    fresh: bool
    trigger: str
    research_only: bool
    finalize_from: str
    portfolio: dict
    evidence: dict
    quote: dict
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


def build_graph(service, team):
    store = service.store

    def portfolio(s):
        if service.kill_path.exists():
            raise RuntimeError("Kill switch is active; research paused")
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
        quote = service.data.quote(s["symbol"]).model_dump(mode="json")
        context = {**s, "quote": quote, "current_timestamp": now().isoformat()}
        result = (
            team.run(context, finalize_from=s["finalize_from"])
            if s.get("finalize_from")
            else team.run(context)
        )
        result["quote"] = quote
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
                    "reference_price": result["quote"]["ask"],
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
        if s.get("research_only"):
            return {"risk": []}
        d = Decision.model_validate(s["decision"])
        prior = store.get(service.client_order_id(s["run_key"]), "intent")
        # Recovery needs no new quote; execution resolves the original immutable intent.
        reasons = service.evaluate(d)[0] if not prior and d.action not in {"HOLD", "WATCH"} else []
        store.log("risk_preview", {"run_key": s["run_key"], "reasons": reasons})
        return {"risk": reasons}

    def execution(s):
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
