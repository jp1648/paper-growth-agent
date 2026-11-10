"""Offline draft evidence assembly; never an activation or trading authorization."""

import hashlib
import json
from pathlib import Path

from .benchmark import bind_prices
from .paper_observations import bind_benchmark, build_observations, verify_shared_portfolio

METHODS = {
    "whole_5bps": (False, 5),
    "whole_0bps": (False, 0),
    "whole_10bps": (False, 10),
    "fractional_0bps": (True, 0),
}
EVIDENCE = (
    "account_terms",
    "cash_comparison",
    "cost_finality",
    "payments",
    "independent_reviews",
    "universe_decision",
    "prospective_eligibility",
)


def build_file(source, output):
    root = Path(source).resolve().parent

    def read(reference):
        name = Path(reference["path"])
        if name.is_absolute() or ".." in name.parts:
            raise ValueError("Evidence paths must remain inside the bundle directory")
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
            raise ValueError("Evidence paths must not contain symlinks")
        if not path.resolve().is_relative_to(root):
            raise ValueError("Evidence escapes bundle directory")
        with path.open("rb") as stream:
            raw = stream.read(16_000_001)
        if len(raw) > 16_000_000:
            raise ValueError("Evidence file exceeds byte bound")
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("Evidence hash mismatch")
        return path, raw

    raw = Path(source).read_bytes()
    bundle = json.loads(raw)
    if type(bundle.get("version")) is not int or bundle["version"] != 1:
        raise ValueError("Require bundle version 1")
    if not isinstance(bundle.get("study_id"), str) or not bundle["study_id"].strip():
        raise ValueError("Require a study identity")
    _, protocol_raw = read(bundle["protocol"])
    protocol = json.loads(protocol_raw)
    manifest = bundle["study_manifest"]
    if manifest.get("protocol_sha256") != bundle["protocol"]["sha256"]:
        raise ValueError("Study must bind the retained protocol")
    missing = [f"study_manifest.{key}" for key in protocol["manifest_required"] if not manifest.get(key)]
    comparisons, primary, scenarios = {}, None, {}
    cash_sources = {}
    entries = bundle["comparisons"]
    expected = {f"{symbol}:{method}" for symbol in ("SPY", "QQQ") for method in METHODS}
    if set(entries) - expected:
        raise ValueError("Unknown comparison method")
    for key, entry in entries.items():
        symbol, method = key.split(":")
        if entry["study_id"] != bundle["study_id"]:
            raise ValueError("Comparison study identity differs")
        _, nav_raw = read(entry["observations"])
        _, ledger_raw = read(entry["ledger"])
        capture_manifest, _ = read(entry["capture_manifest"])
        if capture_manifest.name != "manifest.json":
            raise ValueError("Require retained capture manifest.json")
        nav, ledger = json.loads(nav_raw), json.loads(ledger_raw)
        scenario = {k: v for k, v in ledger.items() if k not in {"fractional", "slippage_bps"}}
        # shortcut: differing quantity-dependent receipts require review until rate-based scenario binding exists.
        if symbol in scenarios and scenarios[symbol] != scenario:
            raise ValueError(
                "Sensitivity underlying inputs differ; fee/payment/interest scenario requires review"
            )
        scenarios[symbol] = scenario
        fractional, slippage = METHODS[method]
        if (
            nav["benchmark"] != symbol
            or ledger["symbol"] != symbol
            or type(ledger["fractional"]) is not bool
            or ledger["fractional"] != fractional
            or type(ledger["slippage_bps"]) is not int
            or ledger["slippage_bps"] != slippage
        ):
            raise ValueError("Comparison label differs from actual ledger method")
        result = build_observations(nav)
        for field, actual in (
            ("start_session", nav["expected_sessions"][0]),
            ("cost_allocation_method", nav["cost_allocation_method"]),
        ):
            if manifest.get(field) and manifest[field] != actual:
                raise ValueError(f"Study manifest differs from observations: {field}")
        prices = bind_prices(capture_manifest.parent, ledger)
        binding = bind_benchmark(nav, ledger)
        if method == "whole_5bps":
            cash_sources[symbol] = (nav, ledger, capture_manifest.parent)
        if primary is not None:
            verify_shared_portfolio(primary, nav)
        else:
            primary = nav
        comparisons[key] = {
            "returns": result,
            "benchmark_binding": binding,
            "prices": prices,
            "inputs": entry,
        }
    missing.extend(f"comparisons.{key}" for key in sorted(expected - set(entries)))
    evidence = bundle.get("evidence", {})
    if set(evidence) - set(EVIDENCE):
        raise ValueError("Unknown evidence category")
    for category in EVIDENCE:
        if not evidence.get(category):
            missing.append(f"evidence.{category}")
        else:
            read(evidence[category])
    cash_matrix = None
    if protocol.get("cash_model") is not None:
        if not evidence.get("cash_comparison") or set(cash_sources) != {"SPY", "QQQ"}:
            missing.append("cash_scenario_matrix")
        else:
            from .paper_cash import compare_scenarios
            from .paper_observations import reconcile_marks

            _, cash_spec_raw = read(evidence["cash_comparison"])
            cash_spec = json.loads(cash_spec_raw)
            _, cash_raw = read(cash_spec["agent_cash"])
            _, inventory_raw = read(cash_spec["inventory"])
            cash = json.loads(cash_raw)
            inventory = json.loads(inventory_raw)
            marked = reconcile_marks(cash_sources["SPY"][2], inventory)
            if protocol["cash_model"]["annual_rates"] != ["0", "0.02", "0.05"]:
                raise ValueError("Unsupported cash convention rate matrix")
            if cash["calendar_sha256"] != comparisons["SPY:whole_5bps"]["prices"]["calendar_sha256"]:
                raise ValueError("Cash calendar differs from retained capture")
            cash_matrix = compare_scenarios(
                cash_sources["SPY"][0],
                cash,
                marked,
                {symbol: value[1] for symbol, value in cash_sources.items()},
                bundle["protocol"]["sha256"],
            )
    result = {
        "version": 1,
        "status": "draft_diagnostic_only",
        "study_id": bundle["study_id"],
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "protocol": bundle["protocol"],
        "study_manifest": manifest,
        "comparisons": comparisons,
        "cash_scenario_matrix": cash_matrix,
        "evidence": evidence,
        "missing_evidence": missing,
        "activation_authorized": False,
        "limitations": [
            "Hashes bind supplied files; source authenticity and completeness are not certified.",
            "Study fields and evidence attachments require independent substantive review.",
            "No prospective eligibility, cash/account terms, cost finality or live approval is inferred.",
            "Empty missing-evidence inventory would still not authorize trial activation or trading.",
        ],
    }
    with Path(output).open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result
