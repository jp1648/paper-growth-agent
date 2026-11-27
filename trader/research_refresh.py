"""Current evidence delta plus historical arithmetic; never reuse old report freshness."""

import copy
import json
from datetime import timedelta
from urllib.parse import urlparse

from .models import TERMINAL, dec, dt, dumps, now
from .paper_dispatch import fingerprint
from .research_manifest import run_directory

SCHEMA = "research-current-refresh-v1"


def export_refresh_bundle(root, reference, store, delta):
    from .research_continuation import (
        _export_parent_bundle,
        _read,
        _require,
        _verify_ancestor_claim,
        validate_parent_state,
    )

    core = _export_parent_bundle(root, reference, store, allow_continuation=True, allow_completed=True)
    descriptor = core["manifest"]["context"].get("continuation", {})
    _require(
        descriptor.get("mode") == "reviewed_synthesis", "Refresh requires completed synthesis provenance"
    )
    ancestor = json.loads(_read(run_directory(root, reference["run_key"]) / "continuation.json"))
    validate_parent_state(store, root, ancestor)
    _require(
        descriptor["bundle_sha256"] == fingerprint(ancestor) and descriptor["parent"] == ancestor["parent"],
        "Refresh ancestor differs",
    )
    authorization, claim = _verify_ancestor_claim(store, reference, core, ancestor)
    core.update(
        schema=SCHEMA,
        ancestor_bundle=ancestor,
        ancestor_authorization=authorization,
        ancestor_claim=claim,
        evidence_delta=copy.deepcopy(delta),
    )
    core["artifact_sha256"] = {
        k: fingerprint(v) for k, v in core.items() if k not in {"schema", "parent", "artifact_sha256"}
    }
    return core


def validate_refresh_bundle(
    bundle, context, expected, *, experiment_id, cfg, current=None, current_snapshot=None
):
    from .research_continuation import _require

    current = current or now()
    _require(
        bundle.get("schema") == SCHEMA and fingerprint(bundle) == expected, "Refresh bundle hash differs"
    )
    _require(
        bundle["artifact_sha256"]
        == {k: fingerprint(v) for k, v in bundle.items() if k not in {"schema", "parent", "artifact_sha256"}},
        "Refresh artifact hash differs",
    )
    binding, parent = bundle["manifest"]["binding"], bundle["manifest"]["context"]
    symbol = parent["symbol"]
    _require(
        binding["experiment_id"] == experiment_id
        and fingerprint(binding) == bundle["parent"]["manifest_hash"]
        and binding["context_sha256"] == fingerprint(parent),
        "Refresh parent binding differs",
    )
    _require(
        context.get("continuation")
        == {"mode": "current_refresh", "parent": bundle["parent"], "bundle_sha256": expected},
        "Refresh descriptor differs",
    )
    _require(
        context.get("research_only") is True
        and parent.get("research_only") is True
        and context.get("symbol") == symbol
        and symbol in cfg.research_universe
        and context.get("run_key") != parent["run_key"],
        "Refresh identity/scope differs",
    )
    _require(
        context.get("runtime_control") == parent.get("runtime_control"), "Refresh runtime controls differ"
    )
    delta = bundle["evidence_delta"]
    _require(
        set(delta) == {"symbol", "since", "as_of", "sources", "coverage", "quote", "research_reference"}
        and delta["symbol"] == symbol,
        "Exact evidence delta required",
    )
    start, end = dt(delta["since"]), dt(delta["as_of"])
    cutoff = dt(bundle["completion"]["result"]["evidence"]["fetched_at"])
    _require(
        start <= cutoff <= end <= dt(context["current_timestamp"]) <= current
        and timedelta(0) <= current - end < timedelta(hours=cfg.research_cache_hours),
        "Refresh delta stale, future or incomplete interval",
    )
    sources = delta["sources"]
    _require(
        isinstance(sources, list) and sources and all(isinstance(s, dict) for s in sources),
        "Delta sources missing",
    )
    ids = [s.get("id") for s in sources]
    _require(
        all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids),
        "Duplicate/invalid delta source",
    )
    old_sources = bundle["snapshot"]["desk"]["sources"]
    for source in sources:
        _require(
            source.get("symbol") == symbol
            and isinstance(source.get("content"), str)
            and bool(source["content"].strip())
            and urlparse(source.get("url", "")).scheme == "https"
            and bool(urlparse(source.get("url", "")).hostname),
            "Delta source provenance missing",
        )
        _require(
            max(start, end - timedelta(hours=cfg.research_cache_hours)) <= dt(source["retrieved_at"]) <= end
            and (not source.get("published_at") or dt(source["published_at"]) <= end),
            "Delta source timestamp differs",
        )
        _require(
            source["id"] not in old_sources or old_sources[source["id"]] == source,
            "Delta overwrites historical source",
        )
    coverage = delta["coverage"]
    _require(
        isinstance(coverage, dict) and set(coverage) == {"filings", "news", "issuer"},
        "All evidence coverage checks required",
    )
    for item in coverage.values():
        _require(
            set(item) == {"status", "source_ids", "limitations"}
            and item["status"] in {"complete", "partial", "unavailable"}
            and isinstance(item["source_ids"], list)
            and item["source_ids"]
            and set(item["source_ids"]) <= set(ids)
            and isinstance(item["limitations"], str)
            and item["limitations"].strip(),
            "Coverage evidence/limitations missing",
        )
    _require(
        context.get("quote") == delta["quote"]
        and context.get("research_reference") == delta["research_reference"],
        "Refresh reference differs",
    )
    quote, reference = delta["quote"], delta["research_reference"]
    _require((quote is None) != (reference is None), "One current reference required")
    if reference is not None:
        _require(
            reference.get("symbol") == symbol
            and reference.get("historical") is True
            and reference.get("executable") is False
            and dec(reference["price"]).is_finite()
            and dec(reference["price"]) > 0
            and end - timedelta(hours=cfg.research_cache_hours) <= dt(reference["retrieved_at"]) <= end,
            "Historical reference must be current-retrieved and nonexecutable",
        )
    else:
        _require(
            quote.get("symbol") == symbol
            and dec(quote["ask"]).is_finite()
            and dec(quote["ask"]) > 0
            and end - timedelta(hours=cfg.research_cache_hours) <= dt(quote["timestamp"]) <= end,
            "Quote stale or invalid",
        )
    frozen = context.get("portfolio", {}).get("snapshot")
    _require(
        isinstance(current_snapshot, dict) and isinstance(frozen, dict), "Refresh requires current portfolio"
    )
    _require(
        current_snapshot["account"]["id"] == frozen["account"]["id"]
        and dec(current_snapshot["account"]["cash"]) == dec(frozen["account"]["cash"])
        and sorted((p["symbol"], dec(p["qty"])) for p in current_snapshot["positions"])
        == sorted((p["symbol"], dec(p["qty"])) for p in frozen["positions"]),
        "Refresh portfolio changed",
    )
    account = parent["runtime_control"]["account_id"]
    _require(
        current_snapshot["account"]["id"] == account
        and dec(current_snapshot["account"]["cash"]).is_finite()
        and dec(current_snapshot["account"]["cash"]) >= 0,
        "Refresh account differs",
    )
    positions = current_snapshot["positions"]
    _require(
        len({p["symbol"] for p in positions}) == len(positions)
        and all(dec(p["qty"]).is_finite() and dec(p["qty"]) >= 0 for p in positions),
        "Invalid refresh holdings",
    )
    _require(
        all(
            order.get("status") in TERMINAL
            for snapshot in (current_snapshot, frozen)
            for order in snapshot.get("orders", [])
        ),
        "Open orders block refresh",
    )
    return bundle


def import_refresh(desk, bundle):
    from .research_continuation import _require

    _require(
        not desk.reports
        and not desk.calculations
        and not desk.continuation
        and not desk.correction
        and desk.budget.models == desk.budget.tools == 0
        and desk.budget.reserved == 0,
        "Fresh desk/budget required",
    )
    saved = desk.checkpoint()
    old, delta = bundle["snapshot"]["desk"], bundle["evidence_delta"]
    try:
        for field in ("sources", "companies", "documents", "texts", "unavailable_documents"):
            setattr(desk, field, copy.deepcopy(old[field]))
        desk.verify_calculations(old["calculations"])
        desk.restore_arithmetic(
            [
                {"event": k, "result": copy.deepcopy(v)}
                for k, v in bundle["snapshot"]["budget"]["events"]
                if k in {"cash_flow_sum", "decimal_product"}
            ]
        )
        for source in delta["sources"]:
            desk.sources[source["id"]] = copy.deepcopy(source)
        company = desk.companies[desk.context["symbol"]]
        company["current_delta_as_of"] = delta["as_of"]
        company["sources"] = copy.deepcopy(list(desk.sources.values()))
        company["refresh_coverage"] = copy.deepcopy(delta["coverage"])
        desk.continuation = {
            "parent_lineage": {
                "reference": copy.deepcopy(bundle["parent"]),
                "bundle_sha256": fingerprint(bundle),
                "accounted_usd": bundle["spend"]["body"]["usd"],
                "reused_roles": [],
                "historical_calculation_ids": sorted(old["calculations"]),
                "status": "historical_inputs_only_fresh_reports_required",
                "historical_reports": copy.deepcopy(old["reports"]),
                "historical_report_times": copy.deepcopy(old["report_times"]),
                "previous_decision": copy.deepcopy(bundle["completion"]["result"]["decision"]),
                "analytical_addendum": copy.deepcopy(bundle["ancestor_bundle"].get("analytical_addendum")),
                "delta": copy.deepcopy(delta),
                "instruction": "Review every current delta and its coverage limits. Historical facts/calculations are not current reports. Reuse exact verified arithmetic only where inputs remain supported; new material evidence must change affected inputs and dependent judgments. Issue all four fresh specialist reports and fresh lead; do not copy timestamps or clear flags to pass. Explicitly resolve prior interpretation gaps with citations, preserve genuine uncertainty. No order or publication authority.",
            }
        }
        _require(
            desk.context == saved["context"] and not desk.reports and not desk.report_times,
            "Refresh promoted historical report",
        )
        return desk.checkpoint()
    except BaseException:
        desk.restore(saved)
        raise


def calculation_index(originals, projected, reports, historical_ids):
    """Index only unreferenced historical calculations; never replace saved records."""
    referenced = set()

    def visit(value):
        if isinstance(value, str) and value in originals:
            referenced.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(reports)
    result = {}
    for identifier, original in originals.items():
        if identifier in referenced or identifier not in historical_ids:
            result[identifier] = projected[identifier]
            continue
        result[identifier] = {
            **{k: copy.deepcopy(v) for k, v in original.items() if not isinstance(v, (dict, list))},
            "historical_family": {
                k: copy.deepcopy(v)
                for k, v in (original.get("variation_provenance") or {}).items()
                if k in {"label", "parent_calculation_id", "canonical_calculation_id"}
            },
            "historical_calculation_index": {
                "canonical_sha256": fingerprint(original),
                "canonical_chars": len(dumps(original)),
                "read_source_id": identifier,
                "note": "Historical derived calculation index: input assumptions, numerical input vectors, patch details and provenance are OMITTED here, not verified or summarized. It is not primary evidence. read_source with this ID and every next_offset returns the unchanged full record. Read targeted decision-material cases before relying on assumptions; exhaustive rereading of all cases is not required. Current report-referenced calculations remain full in context. No gap or acceptance flag is changed.",
            },
        }
    return result


def project_refresh_context(context):
    """Disclose omission of superseded prompts, retaining evidence and original gaps."""
    if context.get("continuation", {}).get("mode") != "current_refresh":
        return context
    if "refresh_context_projection" in context:
        return context
    projected = copy.deepcopy(context)
    omitted = []
    prior = projected.get("portfolio", {}).get("prior_thesis")
    if isinstance(prior, dict):
        projected["active_thesis_summary"] = {
            k: copy.deepcopy(prior[k])
            for k in ("run_key", "reviewed_at", "decision", "reference_price", "execution", "evidence_ids")
            if k in prior
        }
    for path in (
        ("analytical_hypothesis",),
        ("portfolio", "prior_thesis"),
        ("supplement_navigation",),
        ("review_focus",),
    ):
        parent = projected
        for key in path[:-1]:
            parent = parent.get(key, {})
        if path[-1] in parent:
            omitted.append({"path": ".".join(path), "sha256": fingerprint(parent.pop(path[-1]))})
    projected["refresh_context_projection"] = {
        "omitted_fields": omitted,
        "note": "Refresh-only model view, not lossless compression: superseded unverified hypothesis and obsolete tool-navigation/review checklist remain in immutable audit only. The operationally active prior thesis is NOT superseded: its complete decision including triggers, identity, review time and execution/reference metadata are retained in active_thesis_summary; full thesis remains authoritative for CAS/audit. Historical reports with all original gaps, prior decision, analytical addendum, current holdings/reference, fresh delta and source read paths remain. Historical calculations may be indexed with exact scalar outcomes and canonical full-record read_source handles; targeted reads are required for material assumptions, not automatic acceptance.",
    }
    return projected
