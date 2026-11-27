"""Current delta imports arithmetic only; every report and decision must be fresh."""

import copy
import json
from datetime import timedelta

import pytest
from test_research_continuation import parent  # noqa: F401
from test_research_continuation_integration import composed  # noqa: F401
from test_research_field_projection import expand_fields
from test_research_synthesis_continuation import synthesis  # noqa: F401
from test_research_synthesis_revision import completed  # noqa: F401
from test_research_team import Evidence, TeamModel

from trader.models import dec, dumps, now
from trader.paper_dispatch import fingerprint
from trader.research_continuation import (
    accounted_ancestry_cost,
    import_into_desk,
    validate_bundle,
    validate_parent_state,
)
from trader.research_manifest import run_directory, runtime_fingerprint
from trader.research_refresh import export_refresh_bundle
from trader.research_stages import ResearchStages
from trader.research_team import Budget, ResearchDesk


@pytest.fixture
def refresh(completed):  # noqa: F811
    store, root, engine, old_context, prior = completed
    at = now().isoformat()
    delta = {
        "symbol": "ACME",
        "since": prior["completion"]["result"]["evidence"]["fetched_at"],
        "as_of": at,
        "sources": [
            {
                "id": "current-delta",
                "symbol": "ACME",
                "source": "Offline fixture",
                "url": "https://example.com/filing",
                "content": "Material new operating evidence; must review.",
                "retrieved_at": at,
                "published_at": at,
            }
        ],
        "coverage": {
            kind: {
                "status": "partial",
                "source_ids": ["current-delta"],
                "limitations": "Offline fixture coverage only; not verified complete",
            }
            for kind in ["filings", "news", "issuer"]
        },
        "quote": None,
        "research_reference": {
            "symbol": "ACME",
            "historical": True,
            "executable": False,
            "price": "100",
            "retrieved_at": at,
        },
    }
    bundle = export_refresh_bundle(root, prior["parent"], store, delta)
    context = {
        **copy.deepcopy(old_context),
        "run_key": "current-refresh",
        "current_timestamp": at,
        "quote": None,
        "research_reference": delta["research_reference"],
        "continuation": {
            "mode": "current_refresh",
            "parent": bundle["parent"],
            "bundle_sha256": fingerprint(bundle),
        },
    }
    context["portfolio"]["snapshot"] = copy.deepcopy(store.get("snapshot"))
    return store, root, engine.cfg, bundle, context


def validate(case):
    store, _root, cfg, b, ctx = case
    return validate_bundle(
        b, ctx, fingerprint(b), experiment_id=store.scope, cfg=cfg, current_snapshot=store.get("snapshot")
    )


def rehash(b, ctx):
    b["artifact_sha256"] = {
        k: fingerprint(v) for k, v in b.items() if k not in {"schema", "parent", "artifact_sha256"}
    }
    ctx["continuation"]["bundle_sha256"] = fingerprint(b)


def test_current_delta_import_preserves_originals_and_zero_old_reports(refresh):
    store, root, cfg, b, ctx = refresh
    validate(refresh)
    validate_parent_state(store, root, b)
    before = copy.deepcopy(b)
    desk = ResearchDesk(
        cfg, Evidence(), ctx, copy.deepcopy(b["snapshot"]["desk"]["companies"]["ACME"]), Budget(cfg), None
    )
    import_into_desk(desk, b)
    assert desk.calculations == b["snapshot"]["desk"]["calculations"]
    assert desk.reports == desk.report_times == desk.report_dependencies == {} and desk.reused == []
    assert desk.continuation["parent_lineage"]["historical_reports"] == b["snapshot"]["desk"]["reports"]
    assert (
        desk.continuation["parent_lineage"]["historical_report_times"]
        == b["snapshot"]["desk"]["report_times"]
    )
    assert desk.sources["current-delta"]["content"].startswith("Material new")
    assert desk.companies["ACME"].get("fetched_at") == b["snapshot"]["desk"]["companies"]["ACME"].get(
        "fetched_at"
    )
    assert all(desk.sources[k] == v for k, v in b["snapshot"]["desk"]["sources"].items())
    assert desk.budget.models == desk.budget.tools == desk.budget.reserved == 0 and b == before
    assert accounted_ancestry_cost(b) == dec(b["spend"]["body"]["usd"]) + accounted_ancestry_cost(
        b["ancestor_bundle"]
    )


@pytest.mark.parametrize(
    "bad",
    [
        "hash",
        "delta_old",
        "missing_coverage",
        "future_source",
        "issuer",
        "collision",
        "cash",
        "open_order",
        "reference",
        "cutoff",
        "source_content",
    ],
)
def test_refresh_fails_closed(refresh, bad):
    store, _root, _cfg, b, ctx = refresh
    if bad == "hash":
        b["evidence_delta"]["sources"][0]["content"] = "tampered"
    elif bad == "delta_old":
        b["evidence_delta"]["as_of"] = (now() - timedelta(hours=13)).isoformat()
    elif bad == "missing_coverage":
        del b["evidence_delta"]["coverage"]["news"]
    elif bad == "future_source":
        b["evidence_delta"]["sources"][0]["published_at"] = (now() + timedelta(days=1)).isoformat()
    elif bad == "issuer":
        b["evidence_delta"]["sources"][0]["symbol"] = "OTHER"
    elif bad == "collision":
        b["evidence_delta"]["sources"][0]["id"] = "fixture-sec"
    elif bad == "cash":
        ctx["portfolio"]["snapshot"]["account"]["cash"] = "1"
    elif bad == "open_order":
        snapshot = store.get("snapshot")
        snapshot["orders"] = [{"status": "new"}]
        store.put("snapshot", snapshot)
    elif bad == "reference":
        ctx["research_reference"] = {**ctx["research_reference"], "price": "999"}
    elif bad == "cutoff":
        b["evidence_delta"]["since"] = b["evidence_delta"]["as_of"]
    else:
        del b["evidence_delta"]["sources"][0]["content"]
    if bad != "hash":
        rehash(b, ctx)
    with pytest.raises((ValueError, KeyError)):
        validate(refresh)


def test_actual_stages_replay_without_duplicate_models_and_keep_flags(refresh, monkeypatch):
    store, root, cfg, b, ctx = refresh
    directory = run_directory(root, ctx["run_key"])
    directory.mkdir(mode=0o700)
    path = directory / "continuation.json"
    path.write_text(dumps(b))
    path.chmod(0o600)
    authority = {
        "parent": b["parent"],
        "bundle_sha256": fingerprint(b),
        "child_run_key": ctx["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(ctx),
        "code_sha256": runtime_fingerprint(),
        "max_cost_usd": str(cfg.research_max_cost_usd),
        "max_combined_cost_usd": str(accounted_ancestry_cost(b) + cfg.research_max_cost_usd),
        "approved_by": "offline-test",
        "approved_at": now().isoformat(),
        "expires_at": (now() + timedelta(hours=1)).isoformat(),
    }
    store.put(ctx["run_key"], authority, "research_continuation_authorization")
    decision = copy.deepcopy(b["completion"]["result"]["decision"])

    class PoolAwareModel(TeamModel):
        def _generate(self, messages, **kwargs):
            messages = copy.deepcopy(messages)
            context = expand_fields(json.loads(messages[1].content))
            pool = context.get("calculation_context_pool", {})

            def expand(value):
                if isinstance(value, dict):
                    if set(value) == {"calculation_context_ref"}:
                        return expand(pool[value["calculation_context_ref"]])
                    return {k: expand(v) for k, v in value.items()}
                return [expand(v) for v in value] if isinstance(value, list) else value

            expanded = expand(context)
            assert expanded["inherited_research"]["delta"]["sources"][0]["id"] == "current-delta"
            assert expanded["inherited_research"]["reused_roles"] == []
            assert "historical_inputs_only" in expanded["inherited_research"]["status"]
            messages[1].content = dumps(expanded)
            return super()._generate(messages, **kwargs)

    model = PoolAwareModel(decision=decision, gap=True)
    binding = {
        "run_key": ctx["run_key"],
        "experiment_id": store.scope,
        "config_sha256": fingerprint(cfg),
        "context_sha256": fingerprint(ctx),
        "code_sha256": runtime_fingerprint(),
    }

    class NoFetch(Evidence):
        def get(self, *args, **kwargs):
            raise AssertionError("No repeated acquisition")

    engine = ResearchStages(cfg, cfg.database, lambda _: NoFetch(), ctx, model, directory, binding)
    monkeypatch.setattr("trader.research_stages.funding_gate", lambda *a, **kw: None)
    for stage in [
        "acquisition",
        "refresh_import",
        "business",
        "catalysts",
        "valuation",
        "adversarial",
        "lead",
        "commit",
    ]:
        req = {"run_key": ctx["run_key"], "manifest_hash": engine.manifest_hash, "stage": stage}
        result = engine(req)
        calls = list(model.calls)
        assert engine(req) == result
        assert model.calls == calls
    assert list(dict.fromkeys(model.calls)) == ["Research", "Catalysts", "Valuation", "Critique", "Decision"]
    output = store.get(ctx["run_key"], "research_result")
    assert output["reused_reports"] == []
    assert all(
        output["team_report_times"][role] > b["snapshot"]["desk"]["report_times"][role]
        for role in output["team_reports"]
    )
    assert output["critique"]["unresolved_material_gaps"] is True
    assert store.get("ACME", "thesis") is None
    assert (
        len(
            store.db.execute(
                "SELECT key FROM documents WHERE kind='research_spend' AND json_extract(body,'$.run_key')=?",
                (ctx["run_key"],),
            ).fetchall()
        )
        == 1
    )
    validate_parent_state(store, root, b)


def test_import_tamper_rolls_back_everything(refresh):
    _store, _root, cfg, b, ctx = refresh
    desk = ResearchDesk(
        cfg, Evidence(), ctx, copy.deepcopy(b["snapshot"]["desk"]["companies"]["ACME"]), Budget(cfg), None
    )
    before = copy.deepcopy(desk.checkpoint())
    key = next(iter(b["snapshot"]["desk"]["calculations"]))
    b["snapshot"]["desk"]["calculations"][key]["base_value"] = "999999"
    with pytest.raises(ValueError):
        import_into_desk(desk, b)
    after = desk.checkpoint()
    before["budget"].pop("elapsed_seconds")
    after["budget"].pop("elapsed_seconds")
    assert after == before


def test_rehashed_parent_snapshot_still_requires_authoritative_original(refresh):
    store, root, _cfg, b, ctx = refresh
    b["snapshot"]["desk"]["reports"]["business"]["quality"] = "fabricated"
    rehash(b, ctx)
    with pytest.raises(ValueError, match="artifacts changed"):
        validate_parent_state(store, root, b)


@pytest.mark.parametrize(
    "bad",
    [
        "missing_account",
        "missing_positions",
        "duplicate_holdings",
        "negative_cash",
        "infinite_price",
        "frozen_open_order",
        "empty_content",
        "no_host",
        "stale_source",
    ],
)
def test_malformed_current_provenance_or_portfolio_rejected(refresh, bad):
    store, _root, _cfg, b, ctx = refresh
    snapshot = copy.deepcopy(store.get("snapshot"))
    if bad == "missing_account":
        del snapshot["account"]
    elif bad == "missing_positions":
        del snapshot["positions"]
    elif bad == "duplicate_holdings":
        snapshot["positions"] = [{"symbol": "ACME", "qty": "1"}] * 2
        ctx["portfolio"]["snapshot"]["positions"] = copy.deepcopy(snapshot["positions"])
    elif bad == "negative_cash":
        snapshot["account"]["cash"] = "-1"
        ctx["portfolio"]["snapshot"]["account"]["cash"] = "-1"
    elif bad == "infinite_price":
        b["evidence_delta"]["research_reference"]["price"] = "Infinity"
        ctx["research_reference"] = copy.deepcopy(b["evidence_delta"]["research_reference"])
    elif bad == "frozen_open_order":
        ctx["portfolio"]["snapshot"]["orders"] = [{"status": "new"}]
    elif bad == "empty_content":
        b["evidence_delta"]["sources"][0]["content"] = " "
    elif bad == "no_host":
        b["evidence_delta"]["sources"][0]["url"] = "https://"
    else:
        b["evidence_delta"]["sources"][0]["retrieved_at"] = (now() - timedelta(days=2)).isoformat()
    store.put("snapshot", snapshot)
    rehash(b, ctx)
    with pytest.raises((ValueError, KeyError)):
        validate(refresh)


def test_refresh_index_preserves_scalars_full_target_and_paged_canonical_records(refresh):
    _store, _root, cfg, bundle, context = refresh
    desk = ResearchDesk(
        cfg,
        Evidence(),
        context,
        copy.deepcopy(bundle["snapshot"]["desk"]["companies"]["ACME"]),
        Budget(cfg),
        None,
    )
    import_into_desk(desk, bundle)
    before = copy.deepcopy(desk.calculations)
    identifier = next(iter(before))
    index = desk.calculation_context()[identifier]
    assert index["historical_calculation_index"]["canonical_sha256"] == fingerprint(before[identifier])
    for key, value in before[identifier].items():
        if not isinstance(value, (dict, list)):
            assert index[key] == value
    tool = next(t for t in desk.tools() if t.name == "read_source")
    chunks, offset = [], 0
    while offset is not None:
        page = tool.invoke({"source_id": identifier, "offset": offset})
        assert page["original_source"] is False
        chunks.append(page["content"])
        offset = page["next_offset"]
    assert json.loads("".join(chunks)) == before[identifier]
    assert (
        fingerprint(json.loads("".join(chunks))) == index["historical_calculation_index"]["canonical_sha256"]
    )
    desk.reports["valuation"] = {"calculation_id": identifier}
    full = desk.calculation_context()[identifier]
    assert "historical_calculation_index" not in full
    assert full["calculation_id"] == identifier
    assert desk.calculations == before
    fresh = copy.deepcopy(before[identifier])
    fresh["calculation_id"] = "calculation-current-new"
    desk.calculations[fresh["calculation_id"]] = fresh
    assert "historical_calculation_index" not in desk.calculation_context()[fresh["calculation_id"]]
    assert desk.calculations[fresh["calculation_id"]] == fresh


def test_refresh_context_omits_only_disclosed_audit_fields(refresh):
    from trader.research_refresh import project_refresh_context

    _store, _root, _cfg, bundle, context = refresh
    context.update(
        analytical_hypothesis={"unverified": "old"}, supplement_navigation="old", review_focus="old"
    )
    context["portfolio"]["prior_thesis"] = {
        "run_key": "active",
        "reviewed_at": "2026-11-01T00:00:00Z",
        "decision": {
            "action": "HOLD",
            "exit_if": ["material impairment"],
            "next_review": "2026-11-02T00:00:00Z",
        },
    }
    context["inherited_research"] = {
        "historical_reports": bundle["snapshot"]["desk"]["reports"],
        "previous_decision": bundle["completion"]["result"]["decision"],
        "delta": bundle["evidence_delta"],
        "analytical_addendum": {"gaps": "unchanged"},
    }
    original = copy.deepcopy(context)
    projected = project_refresh_context(context)
    assert projected["inherited_research"] == original["inherited_research"]
    assert projected["portfolio"]["snapshot"] == original["portfolio"]["snapshot"]
    assert len(projected["refresh_context_projection"]["omitted_fields"]) == 4
    assert projected["active_thesis_summary"]["decision"] == original["portfolio"]["prior_thesis"]["decision"]
    assert project_refresh_context(projected) is projected
    assert context == original
    context.pop("continuation")
    assert project_refresh_context(context) is context
