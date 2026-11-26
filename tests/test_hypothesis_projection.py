import copy
import json

from langchain_core.messages import HumanMessage

from trader.models import dumps
from trader.research_team import Guard, compact_hypothesis


def test_policy_pool_is_lossless_keeps_all_vectors_and_exact_citations():
    policy = {
        "rationale": "Explicit recurring policy and caveat. " * 10,
        "evidence_ids": ["original-exact-id", "another-original"],
    }
    hypothesis = {
        "status": "untrusted hypothesis",
        "limitations": ["Economic support not certified"],
        "candidate_arguments": {
            "annual_operating_forecast": {
                "cases": [
                    {
                        "scenario": scenario,
                        "cash_rows": [
                            {"category": "tax", "amounts_usd": ["1", "2", "3", "4", "5", "6"], **policy}
                        ],
                    }
                    for scenario in ("bear", "base", "bull")
                ]
            }
        },
    }
    before = copy.deepcopy(hypothesis)
    projected = compact_hypothesis(hypothesis)
    assert len(dumps(projected)) < len(dumps(hypothesis)) and hypothesis == before
    assert len(projected["policy_catalog"]) == 1

    def expand(value):
        if isinstance(value, dict):
            policy_ref = value.get("policy_ref")
            return {
                **{key: expand(item) for key, item in value.items() if key != "policy_ref"},
                **(projected["policy_catalog"][policy_ref] if policy_ref else {}),
            }
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    assert expand(projected["candidate_arguments"]) == hypothesis["candidate_arguments"]
    assert projected["limitations"] == hypothesis["limitations"]
    assert "NOT executable tool arguments" in projected["projection_note"]
    messages = [HumanMessage(content=dumps({"analytical_hypothesis": hypothesis, "other": "retained"}))]
    original_messages = copy.deepcopy(messages)
    bounded = Guard.bounded_messages(messages)
    assert messages == original_messages
    assert json.loads(bounded[0].content)["analytical_hypothesis"] == projected
    assert json.loads(bounded[0].content)["other"] == "retained"
    assert Guard.bounded_messages(bounded) == bounded
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, ToolMessage

    from trader.config import Settings
    from trader.research_team import ROLES, Budget

    assistant = AIMessage(
        content="",
        tool_calls=[
            {"name": "request_correction", "args": {"reason": "bad"}, "id": "repair", "type": "tool_call"}
        ],
    )
    feedback = ToolMessage(content="Rejected correction", tool_call_id="repair", status="error")
    lead = Guard(
        Budget(Settings()),
        "research_lead",
        {"request_correction"},
        {role: {} for role in ROLES},
        lambda: {"analytical_hypothesis": hypothesis},
        lambda: True,
    )
    request = SimpleNamespace(
        messages=[HumanMessage(content="old"), assistant, feedback],
        tools=[{"name": "request_correction"}],
        system_message=None,
        response_format=SimpleNamespace(schema_spec=SimpleNamespace(json_schema={})),
    )
    projected_messages, _, _ = lead._model_request(request)
    assert json.loads(projected_messages[0].content)["analytical_hypothesis"] == projected
    assert projected_messages[1:] == [assistant, feedback]
    projected["limitations"].append("projection-only mutation")
    assert hypothesis == before
    collision = copy.deepcopy(hypothesis)
    collision["candidate_arguments"]["annual_operating_forecast"]["cases"][0]["cash_rows"][0][
        "policy_ref"
    ] = "original-user-field"
    assert compact_hypothesis(collision) is collision
    collision = {**hypothesis, "projection_note": "Original user note must not be overwritten"}
    assert compact_hypothesis(collision) is collision


def test_nonrepeated_or_already_pooled_hypothesis_remains_unchanged():
    for value in [
        None,
        [],
        {"candidate_arguments": {}},
        {"candidate_arguments": {}, "policy_catalog": {"p1": "existing"}},
    ]:
        assert compact_hypothesis(value) is value


def test_exact_context_boundary_is_reduced_without_dropping_source_text():
    from types import SimpleNamespace
    from unittest.mock import patch

    import pytest

    from trader.config import Settings
    from trader.research_team import Budget, ResearchLimit

    policy = {"rationale": "Retained source-anchored policy rationale. " * 40, "evidence_ids": ["original"]}
    hypothesis = {
        "candidate_arguments": {
            "cases": [{"amounts_usd": ["1", "2", "3", "4", "5", "6"], **policy} for _ in range(3)]
        }
    }
    context = {"analytical_hypothesis": hypothesis, "original_source_text": ""}
    cfg = Settings(
        research_max_input_bytes=320000,
        input_cost_per_million="5",
        output_cost_per_million="10",
        research_max_cost_usd="20",
    )
    guard = Guard(Budget(cfg), "valuation", set())
    request = SimpleNamespace(
        messages=[HumanMessage(content=dumps(context))],
        tools=[],
        system_message=None,
        response_format=SimpleNamespace(schema_spec=SimpleNamespace(json_schema={})),
    )
    with patch.object(Guard, "bounded_messages", staticmethod(lambda items: items)):
        initial = guard.request_bytes(guard._model_request(request)[2])
        context["original_source_text"] = "s" * (320752 - initial)
        request.messages = [HumanMessage(content=dumps(context))]
        assert guard.request_bytes(guard._model_request(request)[2]) == 320752
    projected, _, payload = guard._model_request(request)
    assert guard.request_bytes(payload) < 320000
    assert json.loads(projected[0].content)["original_source_text"] == context["original_source_text"]
    with pytest.raises(ResearchLimit):
        guard.budget.model(320752, "valuation", admit_only=True)
    assert guard.budget.model(guard.request_bytes(payload), "valuation", admit_only=True) > 0
