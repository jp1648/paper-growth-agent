import copy
import hashlib

import pytest
from test_research_team import Evidence

from trader.config import Settings
from trader.models import dumps
from trader.research_projection import project_revision_history
from trader.research_team import Budget, ResearchDesk


def context():
    return {
        "analytical_hypothesis": {"superseded": "unverified hypothesis"},
        "portfolio": {
            "prior_thesis": {"old": "unaccepted"},
            "snapshot": {"cash": "500", "positions": [{"symbol": "ACME", "qty": "1"}]},
        },
        "supplement_navigation": ["old navigation"],
        "review_focus": ["old checklist"],
        "research_reference": {"price": "355.58", "executable": False},
        "runtime_control": {"account_id": "paper"},
        "reports": {
            role: {"unresolved_material_gaps": True}
            for role in ("business", "catalysts", "valuation", "adversarial")
        },
        "calculations": {
            f"calc-{i}": {"inputs": {"policy": str(i)}, "annual": [i] * 5, "provenance": ["source"]}
            for i in range(41)
        },
        "arithmetic": [{"result": "123"}],
        "evidence": [{"id": "source", "content_sha256": "exact"}],
        "reviewed_synthesis": {
            "addendum": {"text": "Complete economic evidence"},
            "revision": {
                "previous_decision": {"action": "HOLD"},
                "independent_review": {"verdict": "changes_required"},
            },
        },
    }


def test_revision_omits_only_named_audit_history_without_mutation():
    original = context()
    before = copy.deepcopy(original)
    projected = project_revision_history(original)
    assert original == before
    assert "prior_thesis" not in projected["portfolio"]
    assert projected["portfolio"]["snapshot"] == original["portfolio"]["snapshot"]
    for key in (
        "calculations",
        "reports",
        "reviewed_synthesis",
        "arithmetic",
        "evidence",
        "research_reference",
        "runtime_control",
    ):
        assert projected[key] == original[key]
    note = projected["historical_context_projection"]
    assert note["preprojection_model_context_sha256"] == hashlib.sha256(dumps(original).encode()).hexdigest()
    for field in note["omitted_fields"]:
        value = original
        for key in field["path"].split("."):
            value = value[key]
        assert field["value_sha256"] == hashlib.sha256(dumps(value).encode()).hexdigest()
    assert {x["path"] for x in note["omitted_fields"]} == {
        "analytical_hypothesis",
        "portfolio.prior_thesis",
        "supplement_navigation",
        "review_focus",
    }
    assert "not lossless" in note["note"] and "immutable audit" in note["note"]
    projected["calculations"]["calc-0"]["inputs"]["policy"] = "changed"
    assert original == before


def test_ordinary_synthesis_view_unchanged_and_marker_collision_rejected():
    original = context()
    original["reviewed_synthesis"].pop("revision")
    assert project_revision_history(original) is original
    original = context()
    original["historical_context_projection"] = {"untrusted": True}
    with pytest.raises(ValueError, match="marker already"):
        project_revision_history(original)


@pytest.mark.parametrize(
    "new_hash,old_hash,accepted",
    [
        (hashlib.sha256(b"actual effective prompt").hexdigest(), "a" * 64, True),
        ("b" * 64, "a" * 64, False),
        (
            hashlib.sha256(b"actual effective prompt").hexdigest(),
            hashlib.sha256(b"actual effective prompt").hexdigest(),
            False,
        ),
    ],
)
def test_revision_checks_actual_effective_prompt_before_agent(monkeypatch, new_hash, old_hash, accepted):
    import trader.research_team as module

    cfg = Settings()
    provider = Evidence()
    desk = ResearchDesk(
        cfg, provider, {"symbol": "ACME", "portfolio": {}}, provider.get("ACME"), Budget(cfg), None
    )
    desk.reports = {r: {} for r in ("business", "catalysts", "valuation", "adversarial")}
    desk.continuation["reviewed_synthesis"] = {
        "revision": {
            "prompt_change_review": {
                "new_effective_prompt_sha256": new_hash,
                "old_effective_prompt_sha256": old_hash,
            }
        }
    }
    monkeypatch.setattr(desk, "agent_prompt", lambda role, prompt, **kwargs: "actual effective prompt")

    class Captured(Exception):
        pass

    def create(**kwargs):
        assert accepted
        assert kwargs["system_prompt"] == "actual effective prompt"
        assert kwargs["tools"] == []
        assert kwargs["middleware"][0].dispatch_limit == 1
        raise Captured

    monkeypatch.setattr(module, "create_agent", create)
    with pytest.raises(Captured if accepted else ValueError):
        desk.run_reviewed_synthesis()
    assert desk.budget.models == 0


def test_desk_projection_runs_after_full_evidence_index_and_keeps_saved_context():
    cfg = Settings()
    provider = Evidence()
    raw = {
        "symbol": "ACME",
        "portfolio": {"prior_thesis": {"old": "legacy"}},
        "review_focus": ["fixture-sec"],
        "analytical_hypothesis": {"old": True},
    }
    desk = ResearchDesk(cfg, provider, raw, provider.get("ACME"), Budget(cfg), None)
    desk.continuation["reviewed_synthesis"] = {"addendum": {"text": "unchanged"}}
    saved = copy.deepcopy(desk.context)
    initial = desk.final_context()
    assert any(source["id"] == "fixture-sec" for source in initial["evidence"])
    desk.continuation["reviewed_synthesis"]["revision"] = {"previous_decision": {"action": "HOLD"}}
    projected = desk.final_context()
    assert projected["evidence"] == initial["evidence"]
    assert desk.context == saved
    assert "review_focus" not in projected and "analytical_hypothesis" not in projected


def test_tool_free_synthesis_does_not_request_robinhood_tools(monkeypatch):
    import trader.research_team as module

    cfg = Settings(robinhood_enabled=True)
    provider = Evidence()
    desk = ResearchDesk(
        cfg, provider, {"symbol": "ACME", "portfolio": {}}, provider.get("ACME"), Budget(cfg), None
    )
    desk.continuation["reviewed_synthesis"] = {}
    desk.reports = {role: {} for role in ("business", "catalysts", "valuation", "adversarial")}
    assert "research_data_catalog" in desk.agent_prompt("business", "Original prompt")

    class Captured(Exception):
        pass

    def create(**kwargs):
        assert kwargs["tools"] == []
        assert "research_data_catalog" not in kwargs["system_prompt"]
        assert "research_data's" not in kwargs["system_prompt"]
        raise Captured

    monkeypatch.setattr(module, "create_agent", create)
    with pytest.raises(Captured):
        desk.run_reviewed_synthesis()
