"""Runtime-only coverage boundary; frozen economic recordings are not regenerated."""

import copy

import pytest

from trader.claims import ClaimsCoverage, claim_gaps, validate_claim_coverage
from trader.config import Settings
from trader.models import dt, dumps
from trader.research_team import Budget, ResearchDesk, Valuation


def valuation():
    return {
        "normalization_review": {
            "cash_flow_basis": "Conditional FCFE proxy",
            "debt_and_cash": "No excess cash assumed",
            "dilution_and_sbc": "Explicit compensation sensitivity",
            "nonrecurring_items": "Historical normalization unresolved",
            "unresolved_material_gaps": False,
            "evidence_ids": ["filing"],
            "claims_coverage": {
                name: {
                    "status": "supported_conditional_treatment",
                    "evidence_ids": ["filing"],
                    "as_of_date": "2025-12-31",
                    "treatment_or_gap": "Conditional bound using disclosed claims; semantic review still required",
                }
                for name in ClaimsCoverage.model_fields
            },
        },
        "calculation_id": None,
        "bear_value": None,
        "base_value": None,
        "bull_value": None,
        "method": "Unknown normalized valuation",
        "assumptions": [],
        "sensitivity": "Unknown",
        "margin_of_safety": "Unknown",
        "evidence_ids": ["filing"],
    }


def desk():
    cfg = Settings()
    sources = [{"id": "filing", "source": "SEC EDGAR filing passage", "content": "Original filing"}]
    return ResearchDesk(
        cfg, None, {"symbol": "ACME", "portfolio": {}}, {"sources": sources}, Budget(cfg), None
    )


@pytest.mark.parametrize("fault", ["missing", "extra", "status", "date", "blank"])
def test_fixed_four_keys_and_canonical_fields(fault):
    coverage = valuation()["normalization_review"]["claims_coverage"]
    if fault == "missing":
        coverage.pop("legacy_awards")
    elif fault == "extra":
        coverage["LegacyAwards"] = coverage["legacy_awards"]
    elif fault == "status":
        coverage["legacy_awards"]["status"] = "pass"
    elif fault == "date":
        coverage["common_shares"]["as_of_date"] = "20251231"
    else:
        coverage["legacy_awards"]["treatment_or_gap"] = " "
    with pytest.raises(ValueError):
        ClaimsCoverage.model_validate(coverage)


@pytest.mark.parametrize("fault", ["unknown", "nested_uncited", "derived", "empty", "future", "no_date"])
def test_nested_original_citations_and_point_date(fault):
    worker = desk()
    report = valuation()
    claim = report["normalization_review"]["claims_coverage"]["common_shares"]
    if fault == "unknown":
        claim["evidence_ids"] = ["unknown"]
    elif fault in {"nested_uncited", "derived"}:
        worker.sources["other"] = {
            "source": "SEC EDGAR" if fault == "nested_uncited" else "derived calculation"
        }
        claim["evidence_ids"] = ["other"]
        if fault == "derived":
            report["evidence_ids"].append("other")
    elif fault == "empty":
        claim["evidence_ids"] = []
    elif fault == "future":
        claim["as_of_date"] = "2999-01-01"
    else:
        claim["as_of_date"] = None
    with pytest.raises(ValueError):
        worker.validate(Valuation, report)


def test_truthful_unread_is_valid_despite_false_global_flag_and_source_presence():
    worker = desk()
    report = valuation()
    original = copy.deepcopy(report)
    claim = report["normalization_review"]["claims_coverage"]["common_shares"]
    claim.update(
        status="unread",
        evidence_ids=[],
        as_of_date=None,
        treatment_or_gap="Point-date table precedes read cash-flow excerpt; still needs inspection",
    )
    worker.reports["valuation"] = worker.validate(Valuation, report)
    diagnostic = worker.claims_coverage()
    assert diagnostic["contract"] == "runtime-claims-coverage-v1"
    assert [g["claim"] for g in diagnostic["unresolved"]] == ["common_shares"]
    assert report["normalization_review"]["unresolved_material_gaps"] is False
    assert worker.final_context()["claims_coverage"] == diagnostic
    # Retained source existence is never interpreted as inspection or semantic support.
    assert (
        "filing" in worker.sources
        and original["normalization_review"]["claims_coverage"]["common_shares"]["status"]
        == "supported_conditional_treatment"
    )


def test_legacy_validation_preserves_original_report_bytes_and_remains_unreviewed():
    worker = desk()
    report = valuation()
    report["normalization_review"].pop("claims_coverage")
    original = dumps(report)
    parsed = worker.validate(Valuation, report)
    assert dumps(parsed) == original
    assert len(claim_gaps(parsed)) == 4
    assert all(g["status"] == "legacy_unreviewed" for g in claim_gaps(parsed))
    assert Valuation.model_validate(report).normalization_review.claims_coverage is None
    assert report["normalization_review"].get("claims_coverage") is None


@pytest.mark.parametrize("status", ["unread", "not_disclosed_in_inspected_sources", "unsupported_treatment"])
def test_honest_missing_states_need_no_fabricated_original_citation(status):
    report = valuation()
    for claim in report["normalization_review"]["claims_coverage"].values():
        claim.update(
            status=status,
            evidence_ids=[],
            as_of_date=None,
            treatment_or_gap="Missing in inspected passages; cannot establish complete common economics",
        )
    validate_claim_coverage(report, {}, dt("2026-01-01T00:00:00Z"))
    assert len(claim_gaps(report)) == 4


@pytest.mark.parametrize("fault", ["legacy", "unread", "nested"])
def test_cached_buy_never_bypasses_claim_completion(env, decision, fault):
    from test_graph_reporting import FixtureTeam

    from trader.research import build_graph

    service, broker, _ = env
    build_graph(service, FixtureTeam(decision)).invoke(
        {"symbol": "ACME", "run_key": "claims-cache", "research_only": True}
    )
    saved = service.store.get("claims-cache", "research_result")
    coverage = saved["valuation"]["normalization_review"]["claims_coverage"]
    if fault == "legacy":
        saved["valuation"]["normalization_review"].pop("claims_coverage")
    elif fault == "unread":
        coverage["legacy_awards"]["status"] = "unread"
    else:
        coverage["legacy_awards"]["evidence_ids"] = ["unknown"]
    service.store.put("claims-cache", saved, "research_result")
    result = service.execute(decision, "claims-cache")
    assert result["status"] == "rejected"
    assert any("claim coverage" in reason for reason in result["reasons"])
    assert broker.submits == 0


@pytest.mark.parametrize("action", ["BUY", "WATCH"])
def test_false_global_flags_cannot_close_structured_gaps(env, decision, monkeypatch, action):
    from test_research_team import team

    from trader.research import build_graph

    original = ResearchDesk.result

    def result(worker, output):
        worker.reports["valuation"]["normalization_review"]["claims_coverage"]["legacy_awards"]["status"] = (
            "unread"
        )
        output["structured_response"].action = action
        if action == "WATCH":
            output["structured_response"].quantity = 0
        return original(worker, output)

    monkeypatch.setattr(ResearchDesk, "result", result)
    worker, _ = team(env, decision)
    result = build_graph(env[0], worker).invoke({"symbol": "ACME", "run_key": "claims-false-gap"})
    assert result["decision"]["action"] == "WATCH"
    assert env[1].submits == 0
    assert result["valuation"]["normalization_review"]["unresolved_material_gaps"] is False


def test_claims_source_cutoff_cannot_be_hidden_by_earlier_point_date():
    worker = desk()
    worker.sources["filing"]["published_at"] = "2999-01-01"
    with pytest.raises(ValueError, match="source was published"):
        worker.validate(Valuation, valuation())


def test_actual_graph_repairs_claims_once_and_refreshes_downstream_context(env, decision):
    import hashlib

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import PrivateAttr
    from test_research_team import TeamModel, team

    from trader.research import build_graph

    class ClaimRepairModel(TeamModel):
        _corrected: bool = PrivateAttr(default=False)
        _fresh_critique: bool = PrivateAttr(default=False)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            import json

            schema = kwargs["response_format"]["json_schema"]["name"]
            context = json.loads(messages[1].content)
            if schema == "Decision" and context.get("report_sha256") and not self._corrected:
                assert context["claims_coverage"]["unresolved"][0]["claim"] == "common_shares"
                self._corrected = True
                msg = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "request_correction",
                            "args": {
                                "role": "valuation",
                                "report_sha256": context["report_sha256"]["valuation"],
                                "reason": "Read supplied point-date table and close common claims with a conditional treatment",
                            },
                            "id": "claim-correction",
                            "type": "tool_call",
                        }
                    ],
                )
                msg.usage_metadata = {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}
                return ChatResult(generations=[ChatGeneration(message=msg)])
            if schema == "Critique" and context.get("correction"):
                assert context["claims_coverage"]["unresolved"] == []
                assert context["correction"]["claims_coverage"]["unresolved"][0]["claim"] == "common_shares"
                self._fresh_critique = True
            response = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            msg = response.generations[0].message
            if schema == "Valuation" and not msg.tool_calls:
                report = json.loads(msg.content)
                if report.get("normalization_review") and not self._corrected:
                    claim = report["normalization_review"]["claims_coverage"]["common_shares"]
                    claim.update(
                        status="unread",
                        evidence_ids=[],
                        as_of_date=None,
                        treatment_or_gap="Point-date table still unread; cannot close claim treatment",
                    )
                    msg.content = json.dumps(report)
            return response

    worker, original = team(env, decision)
    model = ClaimRepairModel(decision=original.decision)
    worker.model = model
    result = build_graph(env[0], worker).invoke(
        {"symbol": "ACME", "run_key": "claims-repair", "research_only": True}
    )
    assert model._corrected and model._fresh_critique
    assert claim_gaps(result["valuation"]) == []
    events = env[0].store.events("research_correction_requested")
    assert len(events) == 1
    before = events[0]["body"]
    assert before["role"] == "valuation" and before["invalidated"] == ["valuation", "adversarial"]
    old = before["reports"]["valuation"]
    assert old["normalization_review"]["claims_coverage"]["common_shares"]["status"] == "unread"
    assert hashlib.sha256(dumps(old).encode()).hexdigest() == before["report_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"available_at": "2999-01-01"},
        {"content": " "},
        {"content": None},
        {"original_source": False},
        {"source": "SEC EDGAR navigation index"},
        {"available": False},
    ],
)
def test_cached_claim_source_boundaries_are_independent_of_outer_validator(change):
    worker = desk()
    worker.sources["filing"].update(change)
    with pytest.raises(ValueError):
        validate_claim_coverage(valuation(), worker.sources, dt("2026-01-01T00:00:00Z"))


def test_raw_unknown_supported_status_is_not_a_closed_claim():
    report = valuation()
    report["normalization_review"]["claims_coverage"]["common_shares"]["status"] = (
        "supported_because_i_said_so"
    )
    assert len(claim_gaps(report)) == 4
    assert all(g["status"] == "invalid_coverage" for g in claim_gaps(report))


def test_exhausted_single_correction_retains_unread_report_and_budget(monkeypatch):
    import hashlib
    from unittest.mock import Mock

    worker = desk()
    report = valuation()
    report["normalization_review"]["claims_coverage"]["common_shares"].update(
        status="unread",
        evidence_ids=[],
        as_of_date=None,
        treatment_or_gap="Accessible point-date statement still unread",
    )
    worker.reports = {"business": {}, "catalysts": {}, "valuation": report, "adversarial": {}}
    worker.budget.tools = worker.cfg.research_max_tool_calls
    before = dumps(worker.reports)
    dispatch = Mock()
    monkeypatch.setattr(worker, "dispatch_roles", dispatch)
    result = worker.correction_tool().invoke(
        {
            "role": "valuation",
            "report_sha256": hashlib.sha256(dumps(report).encode()).hexdigest(),
            "reason": "Inspect and close point-date common claims",
        }
    )
    assert "insufficient remaining" in result
    assert dumps(worker.reports) == before and worker.correction is None
    assert worker.claims_coverage()["unresolved"][0]["status"] == "unread"
    assert worker.budget.tools == worker.cfg.research_max_tool_calls
    dispatch.assert_not_called()


@pytest.mark.parametrize("field", ["available_at", "published_at"])
@pytest.mark.parametrize(
    "timestamp,accepted",
    [
        ("2026-01-01T09:59:59Z", True),
        ("2026-01-01T10:00:00Z", True),
        ("2026-01-01T10:00:01Z", False),
        ("2026-01-01T06:00:01-04:00", False),
        ("2026-01-01T11:59:59+02:00", True),
        ("2026-01-01", True),
        ("2026-01-01T09:00:00", False),
    ],
)
def test_full_source_timestamp_cutoff_and_timezone(field, timestamp, accepted):
    worker = desk()
    worker.sources["filing"][field] = timestamp
    cutoff = dt("2026-01-01T10:00:00Z")
    if accepted:
        validate_claim_coverage(valuation(), worker.sources, cutoff)
    else:
        with pytest.raises(ValueError):
            validate_claim_coverage(valuation(), worker.sources, cutoff)
