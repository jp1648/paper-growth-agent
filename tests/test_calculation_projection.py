import copy
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_research_field_projection import expand_fields
from test_valuation_variants import setup, variant

from trader.config import Settings
from trader.models import dumps
from trader.research_team import ROLES, Budget, Guard, compact_calculation_context


def expand(context):
    context = expand_fields(context)
    pool = context["calculation_context_pool"]

    def resolve(value, visited=()):
        if isinstance(value, dict):
            if set(value) == {"calculation_context_ref"}:
                key = value["calculation_context_ref"]
                assert key not in visited
                return resolve(pool[key], (*visited, key))
            return {k: resolve(v, visited) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v, visited) for v in value]
        return value

    return resolve(context["calculations"])


def context():
    desk, tools, base = setup()
    tools["evaluate_valuation_variants"].invoke(
        {"calculation_id": base, "variants": [variant(), variant("120")]}
    )
    return desk, {
        "calculations": desk.calculation_context(),
        "reports": {r: {"material_gap": "Preserve honest original uncertainty " + r} for r in ROLES},
        "source_text": "Original source kept verbatim",
    }


def test_exact_shared_calculation_objects_resolve_losslessly_with_complete_lineage():
    desk, original = context()
    before = copy.deepcopy((original, desk.calculations))
    pooled = compact_calculation_context(original)
    assert len(dumps(pooled)) < len(dumps(original))
    assert expand(pooled) == original["calculations"]
    for identifier, calculation in original["calculations"].items():
        header = pooled["calculations"][identifier]
        for field in ("calculation_id", "bear_value", "base_value", "bull_value"):
            assert header[field] == calculation[field]
        if "variation_provenance" in calculation:
            assert header["variation_provenance"]["label"] == calculation["variation_provenance"]["label"]
            assert [patch["path"] for patch in header["variation_provenance"]["patches"]] == [
                patch["path"] for patch in calculation["variation_provenance"]["patches"]
            ]
    assert pooled["reports"] == original["reports"] and pooled["source_text"] == original["source_text"]
    assert (original, desk.calculations) == before
    assert compact_calculation_context(original) == pooled
    assert compact_calculation_context(json.loads(dumps(original))) == pooled
    assert compact_calculation_context(pooled) is pooled
    assert "same-message" in pooled["calculation_context_projection"]
    # References use strict subtree edges: full recursive expansion cannot self-reference.
    for key in pooled["calculation_context_pool"]:
        assert {"calculation_context_ref": key} != pooled["calculation_context_pool"][key]
    pooled["reports"]["business"]["material_gap"] = "projection mutation"
    assert original == before[0]


@pytest.mark.parametrize(
    "collision", ["calculation_context_pool", "calculation_context_projection", "nested"]
)
def test_reserved_user_fields_are_never_overwritten(collision):
    _, original = context()
    if collision == "nested":
        original["calculations"]["user"] = {"calculation_context_ref": "user-defined"}
    else:
        original[collision] = {"user-defined": "retained"}
    before = copy.deepcopy(original)
    assert compact_calculation_context(original) is original
    assert original == before


def test_nonrepeated_context_is_unchanged():
    original = {"calculations": {"only": {"value": "5"}}, "reports": {"business": "retained"}}
    assert compact_calculation_context(original) is original


def test_final_lead_pool_survives_serialization_and_preserves_entire_refusal_bundle():
    _, original = context()
    calls = [
        {"name": "request_correction", "args": {"reason": r}, "id": r, "type": "tool_call"}
        for r in ("first", "second")
    ]
    ai = AIMessage(content="Preserve latest refusal reasoning", tool_calls=calls)
    feedback = [
        ToolMessage(content="Refusal " + r, tool_call_id=r, status="error") for r in ("first", "second")
    ]
    messages = [HumanMessage(content="old"), ai, *feedback]
    saved = copy.deepcopy(messages)
    guard = Guard(
        Budget(Settings()),
        "research_lead",
        {"request_correction"},
        original["reports"],
        lambda: original,
        lambda: True,
    )
    request = SimpleNamespace(
        messages=messages,
        tools=[{"name": "request_correction"}],
        system_message=None,
        response_format=SimpleNamespace(schema_spec=SimpleNamespace(json_schema={})),
    )
    projected, _, _ = guard._model_request(request)
    result = json.loads(projected[0].content)
    assert expand(result) == original["calculations"] and result["reports"] == original["reports"]
    assert projected[1:] == [ai, *feedback] and messages == saved
    assert Guard.bounded_messages(projected) == projected


def test_full_human_pool_shares_cross_section_objects_without_changing_originals():
    shared = {"rationale": "Exact conditional policy. " * 80, "evidence_ids": ["source-original"]}
    original = {
        "calculations": {"one": {"inputs": shared}},
        "reports": {"valuation": {"policy": copy.deepcopy(shared)}},
        "correction": {
            "reason": "Keep the actual reference and all material gaps",
            "policy": copy.deepcopy(shared),
        },
        "research_reference": {"price": "355.58", "executable": False},
        "evidence": [{"content": "Original text", "policy": copy.deepcopy(shared)}],
    }
    before = copy.deepcopy(original)
    pooled = compact_calculation_context(original)
    envelope = {
        "calculations": {
            k: v
            for k, v in pooled.items()
            if k not in {"calculation_context_pool", "calculation_context_projection"}
        },
        "calculation_context_pool": pooled["calculation_context_pool"],
    }
    assert expand(envelope) == original
    assert len(dumps(pooled).encode()) < len(dumps(original).encode())
    assert original == before and compact_calculation_context(pooled) is pooled
    assert pooled == compact_calculation_context(json.loads(dumps(original)))
    assert pooled["correction"]["reason"] == original["correction"]["reason"]
    assert pooled["research_reference"] == original["research_reference"]


def test_reserved_reference_outside_calculations_prevents_ambiguous_projection():
    _, original = context()
    original["reports"]["external"] = {"calculation_context_ref": "existing-user-value"}
    before = copy.deepcopy(original)
    assert compact_calculation_context(original) is original
    assert original == before


def test_full_human_projection_never_rewrites_assistant_tool_arguments():
    _, original = context()
    original["correction"] = {"saved": copy.deepcopy(original["calculations"])}
    ai = AIMessage(
        content="Exact argument history",
        tool_calls=[{"name": "value_scenarios", "args": original, "id": "call", "type": "tool_call"}],
    )
    feedback = ToolMessage(content="Original result", tool_call_id="call")
    messages = [HumanMessage(content=dumps(original)), ai, feedback]
    before = copy.deepcopy(messages)
    projected = Guard.bounded_messages(messages)
    assert projected[0].content != messages[0].content
    assert projected[1:] == messages[1:] and messages == before
