import copy
import json

import pytest
from langchain_core.messages import HumanMessage

from trader.models import dumps
from trader.research_projection import share_context_fields
from trader.research_team import Guard, compact_calculation_context


def expand_fields(context):
    catalog = context.get("exact_field_catalog", {})

    def expand(value, seen=()):
        if isinstance(value, dict):
            out = {
                key: expand(item, seen)
                for key, item in value.items()
                if key not in {"shared_field_refs", "exact_field_catalog", "field_projection_note"}
            }
            for reference in value.get("shared_field_refs", []):
                assert reference not in seen
                key, item = catalog[reference]
                assert key not in out
                out[key] = expand(item, (*seen, reference))
            return out
        if isinstance(value, list):
            return [expand(item, seen) for item in value]
        return value

    return expand(context)


def fixture():
    return {
        "calculations": {
            f"calculation-{i}": {
                "calculation_id": f"calculation-{i}",
                "annual_operating_forecast": [
                    {
                        "cash_classification_policy": "Conditional source-based cash treatment",
                        "replacement_investment_cash": "1234567890.0000",
                        "evidence_ids": ["passage-original-policy"],
                        "year": year,
                    }
                    for year in range(6)
                ],
                "rationale": f"Different treatment {i}; not economically merged",
                "value": ["1.00", "1.000", True, 1][i % 4],
            }
            for i in range(12)
        },
        "report": {"unresolved_material_gaps": True, "note": "Literal shared_field_refs text"},
    }


def test_field_projection_roundtrip_preserves_semantic_differences_and_originals():
    original = fixture()
    before = copy.deepcopy(original)
    projected = share_context_fields(original)
    assert len(dumps(projected).encode()) < len(dumps(original).encode())
    assert expand_fields(projected) == original == before
    assert projected == share_context_fields(json.loads(dumps(original)))
    assert share_context_fields(projected) is projected
    assert "literal field name" in projected["field_projection_note"]
    projected["exact_field_catalog"].clear()
    assert original == before


@pytest.mark.parametrize("key", ["shared_field_refs", "exact_field_catalog", "field_projection_note"])
@pytest.mark.parametrize("nested", [True, False])
def test_reserved_fields_refuse_projection_at_every_depth(key, nested):
    original = fixture()
    target = original["calculations"]["calculation-0"] if nested else original
    target[key] = "source-owned value"
    before = copy.deepcopy(original)
    assert share_context_fields(original) is original
    assert original == before


def test_empty_and_unprofitable_projection_keep_original():
    for original in ({}, {"calculations": {}}, {"calculations": {"a": {"value": 1}}}):
        assert share_context_fields(original) is original


def test_pair_projection_composes_with_existing_subtree_aliases():
    original = fixture()
    subtree = compact_calculation_context(original)
    combined = share_context_fields(subtree)
    assert expand_fields(combined) == subtree
    pool = subtree.get("calculation_context_pool", {})

    def resolve(value):
        if isinstance(value, dict):
            if set(value) == {"calculation_context_ref"}:
                return resolve(pool[value["calculation_context_ref"]])
            return {
                k: resolve(v)
                for k, v in value.items()
                if k not in {"calculation_context_pool", "calculation_context_projection"}
            }
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    assert resolve(expand_fields(combined)) == original


def test_real_guard_serialization_applies_both_layers_without_changing_input():
    original = fixture()
    message = HumanMessage(content=dumps(original))
    output = Guard.bounded_messages([message])
    projected = json.loads(output[0].content)
    assert expand_fields(projected) == compact_calculation_context(original)
    assert message.content == dumps(original)
