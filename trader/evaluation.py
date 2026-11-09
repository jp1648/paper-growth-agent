"""Research evaluation only: no broker, service or execution graph; model access requires explicit opt-in."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .financials import annual_cash_flow, normalization_supported, normalized_cash_flow
from .models import Decision
from .research_team import Catalysts, Critique, Research, Valuation

SCHEMAS = {"business": Research, "catalysts": Catalysts, "valuation": Valuation, "adversarial": Critique}
SCORING_CONTRACT = "financial-provenance-v8"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Evaluation timestamps must include a timezone")
    return result


def load_suite(path):
    suite = json.loads(Path(path).read_text())
    if suite.get("version") != 1 or not suite.get("cases"):
        raise ValueError("Unsupported or empty evaluation suite")
    ids = set()
    for case in suite["cases"]:
        if case["id"] in ids or case["split"] not in {"development", "holdout"}:
            raise ValueError("Duplicate case ID or invalid split")
        ids.add(case["id"])
        cutoff = timestamp(case["cutoff"])
        if set(case["inputs"]) - {"symbol", "sources", "documents", "reference_price", "reference_kind"}:
            raise ValueError("Unknown frozen input field; rubric and cutoff must stay outside inputs")
        price = Decimal(case["inputs"]["reference_price"])
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid historical reference price")
        sources = case["inputs"]["sources"]
        source_ids = set()
        for source in sources:
            if source["id"] in source_ids:
                raise ValueError("Duplicate source ID")
            source_ids.add(source["id"])
            if timestamp(source["available_at"]) > cutoff:
                raise ValueError("Future source in frozen case")
        for document in case["inputs"].get("documents", []):
            if timestamp(document["available_at"]) > cutoff:
                raise ValueError("Future document in frozen case")
        if not set(case["expected"]["required_evidence_ids"]).issubset(source_ids):
            raise ValueError("Case rubric cites missing evidence")
        if case["expected"]["outcome"] not in {"complete", "abstain", "reject"}:
            raise ValueError("Invalid expected outcome")
    return suite


def model_inputs(case):
    """Only these values may be supplied to a candidate; no rubric or split."""
    return {**case["inputs"], "cutoff": case["cutoff"]}


def scenario_values(inputs):
    """Independent scoring oracle; deliberately not the production tool implementation."""
    cash = Decimal(inputs["cash_flow_per_share"])
    rate = Decimal(inputs["discount_rate"])
    years = inputs["years"]
    if (
        not cash.is_finite()
        or not rate.is_finite()
        or not 0 < cash <= 100000
        or not Decimal(".01") <= rate <= 1
        or type(years) is not int
        or not 1 <= years <= 10
        or len(inputs["growth_rates"]) != 3
        or len(inputs["terminal_multiples"]) != 3
    ):
        raise ValueError("Invalid calculation inputs")
    results = []
    for growth, multiple in zip(inputs["growth_rates"], inputs["terminal_multiples"], strict=True):
        growth, multiple = Decimal(growth), Decimal(multiple)
        if (
            not growth.is_finite()
            or not multiple.is_finite()
            or not Decimal("-.9") <= growth <= 1
            or not 0 <= multiple <= 100
        ):
            raise ValueError("Invalid scenario inputs")
        factor = (1 + growth) / (1 + rate)
        annuity = years if factor == 1 else factor * (1 - factor**years) / (1 - factor)
        value = cash * (annuity + multiple * factor**years)
        results.append(str(value.quantize(Decimal("0.01"))))
    if list(map(Decimal, results)) != sorted(map(Decimal, results)):
        raise ValueError("Scenario values must be ordered bear/base/bull")
    return results


def score(case, candidate, review=None):
    """Score a recorded candidate. Existence of a citation is not semantic support."""
    failures = []
    reports = candidate.get("reports", {})
    decision = candidate.get("decision")
    # Derived IDs require a frozen passage mapping, not just a candidate assertion.
    if candidate.get("derived_evidence_ids"):
        failures.append("unverified_derived_evidence")
    originals = {s["id"]: s for s in case["inputs"]["sources"]}
    verified_sources = dict(originals)
    sources = set(originals)
    for source in candidate.get("sources", []):
        if source.get("id") in originals:
            original = originals[source["id"]]
            if source != original:
                failures.append("mutated_frozen_source")
            continue
        matched = False
        for document in case["inputs"].get("documents", []):
            offset = source.get("offset")
            if source.get("url") != document["metadata"].get("url") or type(offset) is not int or offset < 0:
                continue
            passage = document["text"][offset : offset + 12000]
            identifier = (
                "passage-" + hashlib.sha256((source["url"] + str(offset) + passage).encode()).hexdigest()[:20]
            )
            if passage and source.get("content") == passage and source.get("id") == identifier:
                sources.add(identifier)
                verified_sources[identifier] = source
                matched = True
                break
        if not matched:
            failures.append("unverified_derived_evidence")
    cited = set()
    provenance = None
    complete = isinstance(decision, dict) and isinstance(reports, dict) and set(reports) == set(SCHEMAS)
    if complete:
        try:
            decision = Decision.model_validate(decision).model_dump(mode="json")
            reports = {
                role: schema.model_validate(reports[role]).model_dump(mode="json")
                for role, schema in SCHEMAS.items()
            }
        except (ValueError, TypeError):
            failures.append("invalid_schema")
            complete = False
    if complete:
        for report in [decision, *reports.values()]:
            refs = set(report["evidence_ids"])
            cited.update(refs)
            if not refs.issubset(sources):
                failures.append("unknown_citation")
        if decision["symbol"] != case["inputs"]["symbol"]:
            failures.append("wrong_symbol")
        if timestamp(decision["next_review"]) <= timestamp(case["cutoff"]):
            failures.append("review_not_future")
        valuation = reports["valuation"]
        if valuation["calculation_id"] is None:
            if any(valuation[k] is not None for k in ("bear_value", "base_value", "bull_value")):
                failures.append("unsupported_valuation_numbers")
        else:
            calc = candidate.get("calculations", {}).get(valuation["calculation_id"])
            try:
                values = scenario_values(calc["inputs"])
                if values != [valuation[k] for k in ("bear_value", "base_value", "bull_value")]:
                    failures.append("incorrect_calculation")
            except (KeyError, TypeError, ValueError, ArithmeticError):
                failures.append("missing_or_invalid_calculation")
            try:
                inputs = calc["inputs"]
                source_id = inputs["financial_source_id"]
                provenance = annual_cash_flow(
                    originals[source_id], inputs["financial_period_end"], timestamp(case["cutoff"])
                )
                bridge = None
                if inputs.get("normalization_bridge") is not None:
                    bridge = normalized_cash_flow(
                        originals[source_id],
                        inputs["financial_period_end"],
                        timestamp(case["cutoff"]),
                        inputs["normalization_bridge"],
                        verified_sources,
                    )
                    if not set(bridge["evidence_ids"]).issubset(valuation["evidence_ids"]):
                        failures.append("unverified_normalization_evidence")
                if (
                    calc.get("financial_provenance") != provenance
                    or calc.get("normalization_bridge") != bridge
                    or Decimal(inputs["cash_flow_per_share"])
                    != Decimal((bridge or provenance)["cash_flow_per_share"])
                    or source_id not in valuation["evidence_ids"]
                ):
                    failures.append("unverified_financial_provenance")
            except (KeyError, TypeError, ValueError, ArithmeticError):
                failures.append("unverified_financial_provenance")
        normalization = valuation.get("normalization_review")
        if normalization and not set(normalization["evidence_ids"]).issubset(valuation["evidence_ids"]):
            failures.append("unverified_normalization_evidence")
        if decision["action"] in {"BUY", "ADD"} and (
            reports["adversarial"]["unresolved_material_gaps"]
            or valuation["calculation_id"] is None
            or not normalization_supported(valuation)
        ):
            failures.append("unsupported_buy")
    expected = case["expected"]
    if complete and expected.get("valuation_required"):
        calc = candidate.get("calculations", {}).get(reports["valuation"]["calculation_id"])
        if not calc:
            failures.append("required_valuation_missing")
        else:
            try:
                # The frozen expectation is historical; normalized input is checked above.
                if provenance is not None and Decimal(provenance["cash_flow_per_share"]) != Decimal(
                    expected["cash_flow_per_share"]
                ):
                    failures.append("unsupported_cash_flow_input")
            except (KeyError, TypeError, ValueError, ArithmeticError):
                failures.append("missing_or_invalid_calculation")
    if expected["outcome"] == "reject":
        if complete:
            failures.append("expected_rejection")
        if candidate.get("failure_category") != expected["failure_category"]:
            failures.append("wrong_failure_category")
    else:
        if not complete:
            failures.append("incomplete_research")
        elif decision["action"] not in expected["allowed_actions"]:
            failures.append("unexpected_action")
        if not set(expected["required_evidence_ids"]).issubset(cited):
            failures.append("missing_required_citation")
        if (
            complete
            and expected["outcome"] == "abstain"
            and not reports["adversarial"]["unresolved_material_gaps"]
        ):
            failures.append("missing_material_gap")
    mechanically_valid = complete and not failures
    mechanical_pass = not failures
    # Semantic review is explicitly separate from mechanical correctness.
    if candidate.get("human_review") is not None:
        failures.append("embedded_review_not_accepted")
    reviewed = review is not None
    if reviewed:
        if review.get("case_hash") != fingerprint(case) or review.get("candidate_hash") != fingerprint(
            candidate
        ):
            raise ValueError("Semantic review does not match exact case and candidate")
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
            raise ValueError("Semantic review requires a named independent reviewer")
        timestamp(review["reviewed_at"])
        if (
            review.get("claims_supported") is not True
            or review.get("abstention_reason_correct") is not True
            or review.get("independent") is not True
            or not isinstance(review.get("notes"), str)
            or not review["notes"].strip()
        ):
            failures.append("human_review_failed")
    return {
        "scoring_contract": SCORING_CONTRACT,
        "case_id": case["id"],
        "split": case["split"],
        "sufficient_evidence": expected["outcome"] == "complete",
        "structurally_complete": complete,
        "complete": mechanically_valid,
        "action": decision.get("action") if isinstance(decision, dict) else None,
        "execution_eligible": False,
        "mechanical_pass": mechanical_pass,
        "failures": sorted(set(failures)),
        "semantic_review": "reviewed" if reviewed else "pending",
        "acceptance": "failed" if failures else "passed" if reviewed else "pending_human_review",
    }


def summarize(rows):
    sufficient = [r for r in rows if r["sufficient_evidence"]]
    costs = [Decimal(r["metrics"]["known_cost_usd"]) for r in rows]
    completed = sum(r["complete"] for r in rows)
    accepted_completed = sum(r["complete"] and r["acceptance"] == "passed" for r in rows)
    latencies = sorted(r["metrics"]["latency_seconds"] for r in rows)
    stage_names = sorted({stage for row in rows for stage in row["stages"]})
    stages = {}
    for name in stage_names:
        measurements = [row["stages"][name] for row in rows if name in row["stages"]]
        stages[name] = {
            "outcomes": {
                status: sum(m["status"] == status for m in measurements)
                for status in ("complete", "failed", "not_started")
            },
            "known_cost_usd": str(
                sum((Decimal(m.get("known_cost_usd", "0")) for m in measurements), Decimal(0))
            ),
            "model_seconds": sum(m.get("model_seconds", 0) for m in measurements),
            "repeated_tool_calls": sum(m.get("repeated_tool_calls", 0) for m in measurements),
            "peak_context_bytes": max((m.get("peak_context_bytes", 0) for m in measurements), default=0),
        }
    return {
        "cases": len(rows),
        "completed": completed,
        "accepted_completed_decisions": accepted_completed,
        "reviewed_failed_cases": sum(
            r["semantic_review"] == "reviewed" and r["acceptance"] == "failed" for r in rows
        ),
        "mechanical_passes": sum(r["mechanical_pass"] for r in rows),
        "pending_semantic_reviews": sum(r["semantic_review"] == "pending" for r in rows),
        "sufficient_case_completion_rate": sum(r["complete"] for r in sufficient) / len(sufficient)
        if sufficient
        else None,
        "sufficient_case_acceptance_rate": sum(r["acceptance"] == "passed" for r in sufficient)
        / len(sufficient)
        if sufficient
        else None,
        "known_cost_usd": str(sum(costs, Decimal(0))),
        "unknown_cost_calls": sum(r["metrics"]["unknown_cost_calls"] for r in rows),
        "known_cost_per_completed_decision": str(sum(costs, Decimal(0)) / completed) if completed else None,
        "known_cost_per_accepted_completed_decision": str(sum(costs, Decimal(0)) / accepted_completed)
        if accepted_completed
        else None,
        "median_known_case_cost_usd": str(statistics.median(costs)) if costs else None,
        "p95_known_case_cost_usd": str(sorted(costs)[math.ceil(len(costs) * 0.95) - 1]) if costs else None,
        "median_latency_seconds": statistics.median(latencies) if latencies else None,
        "repeated_tool_calls": sum(r["metrics"]["repeated_tool_calls"] for r in rows),
        "stages": stages,
        "p95_latency_seconds": latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None,
        "investment_performance_measured": False,
    }


def replay(suite_path, responses_path, output_path, run_id, *, reviews_path=None):
    """Evaluate immutable recorded outputs. Never invokes a model or trading path."""
    suite = load_suite(suite_path)
    recording = json.loads(Path(responses_path).read_text())
    if recording["suite_hash"] != fingerprint(suite):
        raise ValueError("Recording does not match frozen suite")
    expected_ids = {c["id"] for c in suite["cases"]}
    if set(recording["cases"]) != expected_ids:
        raise ValueError("Recording must include every case, including failures")
    for field in ("candidate_id", "code_revision", "prompt_hash", "model_versions", "configuration", "mode"):
        if not recording.get(field):
            raise ValueError(f"Missing candidate provenance: {field}")
    if recording["mode"] not in {"offline_script", "recorded_model"}:
        raise ValueError("Invalid recording mode")
    reviews = None
    if reviews_path is not None:
        reviews = json.loads(Path(reviews_path).read_text())
        if (
            reviews.get("version") != 1
            or reviews.get("suite_hash") != fingerprint(suite)
            or reviews.get("recording_hash") != fingerprint(recording)
            or not isinstance(reviews.get("cases"), dict)
            or not set(reviews["cases"]).issubset(expected_ids)
        ):
            raise ValueError("Reviews must bind the exact suite and recording with known case IDs")
    rows = []
    for case in suite["cases"]:
        candidate = recording["cases"][case["id"]]
        metrics = candidate["metrics"]
        for key in (
            "known_cost_usd",
            "unknown_cost_calls",
            "latency_seconds",
            "model_calls",
            "tool_calls",
            "repeated_tool_calls",
            "peak_context_bytes",
        ):
            if key not in {"known_cost_usd", "latency_seconds"} and type(metrics[key]) is not int:
                raise ValueError("Measurement counts must be integers")
            if key == "latency_seconds" and type(metrics[key]) not in {int, float}:
                raise ValueError("Latency must be numeric")
            number = Decimal(str(metrics[key]))
            if not number.is_finite() or number < 0:
                raise ValueError("Invalid measurement")
        if not isinstance(candidate.get("stages"), dict):
            raise TypeError("Missing stage outcomes")
        if any(
            stage.get("status") not in {"complete", "failed", "not_started"}
            for stage in candidate["stages"].values()
        ):
            raise ValueError("Invalid stage outcome")
        rows.append(
            {
                **score(case, candidate, reviews["cases"].get(case["id"]) if reviews else None),
                "metrics": metrics,
                "stages": candidate["stages"],
                "inputs_hash": fingerprint(model_inputs(case)),
                "cutoff": case["cutoff"],
                "evidence_hashes": {
                    source["id"]: fingerprint(source) for source in case["inputs"]["sources"]
                },
                "candidate_hash": fingerprint(candidate),
            }
        )
    result = {
        "version": 1,
        "scoring_contract": SCORING_CONTRACT,
        "run_id": run_id,
        "suite_hash": fingerprint(suite),
        "recording_hash": fingerprint(recording),
        "reviews_hash": fingerprint(reviews) if reviews is not None else None,
        "provenance": {k: v for k, v in recording.items() if k != "cases"},
        "cases": rows,
        "summary": summarize(rows),
        "limitations": [
            "Scripted outputs validate mechanics only; recorded model outputs still require semantic review.",
            "Citation existence does not prove semantic support; human reviews remain explicit.",
            "No order execution or investment-performance evaluation is performed.",
        ],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return result


class FrozenEvidence:
    """No network fallback; only immutable evidence admitted before the cutoff."""

    def __init__(self, case):
        self.case = case

    def get(self, symbol, fresh=False):
        if symbol != self.case["inputs"]["symbol"]:
            raise ValueError("Symbol outside frozen evaluation evidence")
        return {"symbol": symbol, "sources": self.case["inputs"]["sources"]}

    def fetch(self, symbol):
        return self.get(symbol)

    def documents(self, symbol, filing):
        self.get(symbol)
        return [
            d["metadata"]
            for d in self.case["inputs"].get("documents", [])
            if d["accession"] == filing["accessionNumber"]
        ]

    def document(self, metadata):
        for document in self.case["inputs"].get("documents", []):
            if document["metadata"] == metadata:
                return document["text"]
        raise ValueError("Document outside frozen evaluation evidence")


def run_case(cfg, store, case, model, run_key):
    """Exercise the real research desk with an injected model, never an execution graph.

    Caller owns model authorization and durable budget reservation. This lower-level
    function deliberately does not choose credentials or construct a model provider.
    """
    import time
    from collections import Counter

    from .models import dumps
    from .observability import span
    from .research_team import Budget, ResearchDesk

    provider = FrozenEvidence(case)
    context = {
        "symbol": case["inputs"]["symbol"],
        "run_key": run_key,
        "current_timestamp": case["cutoff"],
        "quote": {
            "ask": case["inputs"]["reference_price"],
            "bid": case["inputs"]["reference_price"],
            "reference_kind": "frozen_non_executable",
        },
        "portfolio": {"prior_thesis": None, "positions": [], "available_capital": "100000"},
        "evaluation": "Frozen research-only case. No executable quote or broker access.",
    }
    budget = Budget(cfg)
    desk = ResearchDesk(
        cfg, provider, context, provider.get(context["symbol"]), budget, model, evaluation_at=case["cutoff"]
    )
    start = time.monotonic()
    output, error = None, None
    try:
        with span("evaluation.case", {"run_key": run_key, "case_id": case["id"]}, store.scope) as traced:
            output = desk.run()
            traced["output"] = output
    except Exception as exc:  # noqa: BLE001 - record failed cases and continue the evaluation cohort
        if "cited unknown evidence" in str(exc):
            error = "unknown_citation"
        elif "Timeout" in type(exc).__name__:
            error = "provider_timeout"
        elif isinstance(exc, (ValueError, TypeError)):
            error = "invalid_schema"
        else:
            error = type(exc).__name__
    events = [{"kind": kind, "body": json.loads(dumps(body))} for kind, body in budget.events]
    costs = [e["body"] for e in events if e["kind"] == "model_cost"]
    tools = [e["body"] for e in events if e["kind"] == "research_tool_result"]
    contexts = [e["body"] for e in events if e["kind"] == "research_context"]
    repeated = Counter((t["name"], t["arguments_hash"]) for t in tools)
    metrics = {
        "known_cost_usd": str(
            sum(
                (Decimal(str(c["estimated_usd"])) for c in costs if c["estimated_usd"] is not None),
                Decimal(0),
            )
        ),
        "unknown_cost_calls": max(0, budget.models - len(costs))
        + sum(c["estimated_usd"] is None for c in costs),
        "latency_seconds": time.monotonic() - start,
        "model_calls": budget.models,
        "tool_calls": budget.tools,
        "repeated_tool_calls": sum(max(0, n - 1) for n in repeated.values()),
        "peak_context_bytes": max((c["total_bytes"] for c in contexts), default=0),
    }
    stages = {}
    for role in [*SCHEMAS, "research_lead"]:
        stage_costs = [c for c in costs if c["stage"] == role]
        stages[role] = {
            "status": "complete"
            if role in desk.reports or role == "research_lead" and output
            else "failed"
            if stage_costs
            else "not_started",
            "model_calls": len(stage_costs),
            "known_cost_usd": str(
                sum(
                    (Decimal(str(c["estimated_usd"])) for c in stage_costs if c["estimated_usd"] is not None),
                    Decimal(0),
                )
            ),
            "unknown_cost_calls": sum(c["estimated_usd"] is None for c in stage_costs),
            "peak_context_bytes": max((c["total_bytes"] for c in contexts if c["role"] == role), default=0),
            "tool_calls": sum(t["role"] == role for t in tools),
            "repeated_tool_calls": sum(
                max(0, n - 1)
                for n in Counter(
                    (t["name"], t["arguments_hash"]) for t in tools if t["role"] == role
                ).values()
            ),
            "model_seconds": sum(
                e["body"]["duration_seconds"]
                for e in events
                if e["kind"] == "research_model_timing" and e["body"]["role"] == role
            ),
        }
    record = {
        "decision": output["decision"] if output else None,
        "reports": desk.reports,
        "calculations": desk.calculations,
        "metrics": metrics,
        "stages": stages,
        "failure_category": error,
        "events": events,
        "sources": list(desk.sources.values()),
        "inputs_hash": fingerprint(model_inputs(case)),
    }
    store.put(run_key, record, "evaluation_case")
    return record


def run_suite(cfg, suite_path, output_dir, run_id, max_cost, split="development", *, models=None):
    """Explicitly budgeted research-only run; shared daily reservation survives interruption."""
    import os
    import re
    from zoneinfo import ZoneInfo

    from .config import Security, Settings
    from .models import dec, dumps, now
    from .research_team import MANDATE, ROLES, DeepResearchTeam
    from .spending import DailyReservation
    from .store import Store

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", run_id):
        raise ValueError("Invalid evaluation run ID")
    cap = dec(max_cost)
    if not cap.is_finite() or not 0 < cap <= min(cfg.research_max_cost_usd, cfg.research_daily_cost_usd):
        raise ValueError("Evaluation budget must fit both configured per-run and daily limits")
    if split not in {"development", "holdout"}:
        raise ValueError("Select development or holdout explicitly")
    original = load_suite(suite_path)
    suite = {
        **original,
        "cases": [c for c in original["cases"] if c["split"] == split and c.get("kind", "model") == "model"],
    }
    if not suite["cases"]:
        raise ValueError("No model cases in selected split")
    if models is None and not os.getenv("OPENAI_API_KEY"):
        raise ValueError("Set OPENAI_API_KEY for an explicitly authorized model evaluation")
    root = Store(cfg.database)
    reservation = None
    ledger = None
    charged = dec(0)
    uncertain = True
    try:
        if not root.experiment and models is None:
            raise ValueError("Paid evaluation requires the existing initialized budget ledger")
        with root.db:
            root.db.execute("BEGIN IMMEDIATE")
            if root.get(run_id, "evaluation_manifest", scope="global"):
                raise ValueError("Evaluation run ID already exists; do not duplicate paid runs")
            root.put(run_id, {"status": "claimed", "at": now()}, "evaluation_manifest", scope="global")
        destination = Path(output_dir)
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        local_cfg = Settings.model_validate(
            {
                **cfg.model_dump(),
                "database": str(destination / "ledger.sqlite"),
                "x_enabled": False,
                "flovia_x_enabled": False,
                "research_max_cost_usd": cap,
                "universe": {
                    c["inputs"]["symbol"]: Security(sector="Evaluation", cik="1") for c in suite["cases"]
                },
            }
        )
        budget_cfg = cfg.model_copy(update={"research_max_cost_usd": cap})
        reservation = DailyReservation(root, budget_cfg, "evaluation:" + run_id)
        allowance = reservation.allowance
        ledger = Store(local_cfg.database)
        ledger.experiment = {"id": "evaluation:" + run_id}
        model_set = models if models is not None else DeepResearchTeam(local_cfg, ledger, None).models()
        config = local_cfg.model_dump(mode="json")
        code = {
            p.name: p.read_text()
            for p in sorted(Path(__file__).parent.glob("*.py"))
            if not p.name.startswith(".")
        }
        prompt = {"mandate": MANDATE, "roles": {r: instructions for r, (_, instructions) in ROLES.items()}}
        recording = {
            "suite_hash": fingerprint(suite),
            "parent_suite_hash": fingerprint(original),
            "candidate_id": run_id,
            "code_revision": fingerprint(code),
            "prompt_hash": fingerprint(prompt),
            "model_versions": {r: cfg.research_model(r).model for r in [*SCHEMAS, "research_lead"]},
            "configuration": config,
            "mode": "offline_script" if models is not None else "recorded_model",
            "started_at": now().isoformat(),
            "cases": {},
        }

        def save(name, value):
            path = destination / name
            import tempfile

            with tempfile.NamedTemporaryFile(mode="w", dir=destination, delete=False) as handle:
                temporary = Path(handle.name)
                try:
                    handle.write(dumps(value))
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)

        save("suite.json", suite)
        save("manifest.json", {k: v for k, v in recording.items() if k != "cases"})
        for case in suite["cases"]:
            remaining = allowance - charged
            if (
                remaining <= 0
                or now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
                != reservation.record["day"]
            ):
                record = {
                    "decision": None,
                    "reports": {},
                    "calculations": {},
                    "stages": {},
                    "failure_category": "budget_or_date_limit",
                    "metrics": {
                        "known_cost_usd": "0",
                        "unknown_cost_calls": 0,
                        "latency_seconds": 0,
                        "model_calls": 0,
                        "tool_calls": 0,
                        "repeated_tool_calls": 0,
                        "peak_context_bytes": 0,
                    },
                }
            else:
                case_cfg = local_cfg.model_copy(update={"research_max_cost_usd": remaining})
                record = run_case(case_cfg, ledger, case, model_set, run_id + ":" + case["id"])
                charged += dec(record["metrics"]["known_cost_usd"])
                if record["metrics"]["unknown_cost_calls"]:
                    # Unknown charges consume the remaining reservation; no blind next-case retry.
                    charged = allowance
            recording["cases"][case["id"]] = record
            save("recording.json", recording)
        uncertain = any(c["metrics"]["unknown_cost_calls"] for c in recording["cases"].values())
        result = replay(
            destination / "suite.json", destination / "recording.json", destination / "results.json", run_id
        )
        root.put(
            run_id,
            {"status": "complete", "at": now(), "output": str(destination), "summary": result["summary"]},
            "evaluation_manifest",
            scope="global",
        )
        return result
    finally:
        if reservation:
            reservation.finish(charged, unknown=uncertain)
        if ledger:
            ledger.db.close()
        root.db.close()


def compare(baseline_path, candidate_path, output_path):
    """Paired comparison only; never silently compare different cases or evidence."""
    baseline, candidate = [json.loads(Path(p).read_text()) for p in (baseline_path, candidate_path)]
    if any(r.get("scoring_contract") != SCORING_CONTRACT for r in (baseline, candidate)):
        raise ValueError("Comparison requires both recordings rescored under the current scoring contract")
    if baseline["suite_hash"] != candidate["suite_hash"]:
        raise ValueError("Comparison requires identical frozen suites and splits")
    left = {r["case_id"]: r for r in baseline["cases"]}
    right = {r["case_id"]: r for r in candidate["cases"]}
    if (
        not left
        or len(left) != len(baseline["cases"])
        or len(right) != len(candidate["cases"])
        or set(left) != set(right)
        or any(
            left[k]["inputs_hash"] != right[k]["inputs_hash"] or left[k]["split"] != right[k]["split"]
            for k in left
        )
    ):
        raise ValueError("Comparison requires identical cases and input hashes")
    if baseline["provenance"]["mode"] != candidate["provenance"]["mode"]:
        raise ValueError("Cannot compare scripted fixtures against model research")
    pairs = []
    for key, a in left.items():
        b = right[key]
        pairs.append(
            {
                "case_id": key,
                "split": a["split"],
                "baseline_pass": a["mechanical_pass"],
                "candidate_pass": b["mechanical_pass"],
                "baseline_acceptance": a["acceptance"],
                "candidate_acceptance": b["acceptance"],
                "known_cost_delta_usd": str(
                    Decimal(b["metrics"]["known_cost_usd"]) - Decimal(a["metrics"]["known_cost_usd"])
                ),
                "latency_delta_seconds": b["metrics"]["latency_seconds"] - a["metrics"]["latency_seconds"],
                "context_bytes_delta": b["metrics"]["peak_context_bytes"]
                - a["metrics"]["peak_context_bytes"],
            }
        )
    result = {
        "baseline_run_id": baseline["run_id"],
        "candidate_run_id": candidate["run_id"],
        "baseline_result_hash": fingerprint(baseline),
        "candidate_result_hash": fingerprint(candidate),
        "pairs": pairs,
        "baseline_summary": summarize(baseline["cases"]),
        "candidate_summary": summarize(candidate["cases"]),
        "regressions": [p["case_id"] for p in pairs if p["baseline_pass"] and not p["candidate_pass"]],
        "promotion_recommended": False,
        "note": "Paired engineering results require semantic review; they do not establish an investment edge or authorize promotion.",
    }
    with Path(output_path).open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    return result
