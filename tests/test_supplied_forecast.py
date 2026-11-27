import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_annual_forecast import normalization as annual_normalization
from test_annual_forecast import path as annual_path
from test_normalization import _batch_args

from trader.models import dec, dumps
from trader.research_checkpoint import ResearchCheckpoint
from trader.research_journal import Journal
from trader.research_team import Guard


def setup_hypothesis():
    desk, source, bridge = annual_normalization.__wrapped__()
    path = annual_path.__wrapped__((desk, source, bridge))
    desk.sources["annual-policy"]["content"] = (
        "Different exact fiscal2025 original policy passage; synthetic fixture only."
    )
    args = {
        **_batch_args(source, bridge),
        "growth_rates": [],
        "years": 2,
        "terminal_multiples": [
            str(dec(1) / (dec(".1") - dec(case["terminal_growth_rate"]))) for case in path["cases"]
        ],
        "annual_operating_forecast": path,
    }
    navigation = [
        {
            "id": ref,
            "url": desk.sources[ref]["url"],
            "offset": 123,
            "content_chars": len(desk.sources[ref]["content"]),
            "content_sha256": hashlib.sha256(desk.sources[ref]["content"].encode()).hexdigest(),
        }
        for ref in ("assumptions", "annual-policy")
    ]
    args["financial_source_id"] = "old-financial-source"
    desk.context["analytical_hypothesis"] = {
        "candidate_tool_name": "value_scenarios",
        "candidate_arguments": args,
        "historical_source_navigation": navigation,
    }
    desk.sources["fresh-policy"] = {**desk.sources.pop("annual-policy"), "id": "fresh-policy"}
    return desk, source, args


def test_hypothesis_uses_only_registered_exact_originals_and_same_kernel(monkeypatch):
    desk, source, _args = setup_hypothesis()
    before = copy.deepcopy((desk.context, desk.sources))
    monkeypatch.setattr(
        desk.provider, "get", lambda *a, **k: pytest.fail("Helper fetched or injected evidence")
    )
    tools = {tool.name: tool for tool in desk.tools()}
    result = tools["evaluate_supplied_forecast"].invoke({"financial_source_id": source["id"]})
    assert result["evidence_id_map"] == {"annual-policy": "fresh-policy", "assumptions": "assumptions"}
    assert set(result) == {
        "calculation_id",
        "bear_value",
        "base_value",
        "bull_value",
        "inputs_sha256",
        "evidence_id_map",
        "note",
    }
    calculation = desk.calculations[result["calculation_id"]]
    assert calculation["inputs"]["financial_source_id"] == source["id"]
    assert calculation["inputs"]["annual_operating_forecast"]["cases"][0]["segments"][0]["evidence_ids"] == [
        "fresh-policy"
    ]
    assert result["inputs_sha256"] == hashlib.sha256(dumps(calculation["inputs"]).encode()).hexdigest()
    assert tools["value_scenarios"].invoke(calculation["inputs"]) == calculation
    parts, offset = [], 0
    while offset is not None:
        page = tools["read_source"].invoke({"source_id": result["calculation_id"], "offset": offset})
        parts.append(page["content"])
        offset = page["next_offset"]
    assert json.loads("".join(parts)) == calculation
    assert (desk.context, desk.sources) == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "tampered-text",
        "wrong-url",
        "wrong-hash",
        "wrong-length",
        "missing-navigation",
        "stale",
        "malformed-hash",
        "duplicate-map",
        "future",
        "foreign",
        "oversized",
        "kernel",
        "unknown-financial",
    ],
)
def test_hypothesis_provenance_failure_is_bounded_no_fetch_no_save(fault, monkeypatch):
    desk, source, args = setup_hypothesis()
    ref = source["id"]
    nav = desk.context["analytical_hypothesis"]["historical_source_navigation"][1]
    if fault == "missing":
        desk.sources.pop("fresh-policy")
    elif fault == "tampered-text":
        desk.sources["fresh-policy"]["content"] += "tampered"
    elif fault == "wrong-url":
        nav["url"] = "https://www.sec.gov/Archives/edgar/data/1/000000000126000002/other.htm"
    elif fault == "wrong-hash":
        nav["content_sha256"] = "0" * 64
    elif fault == "wrong-length":
        nav["content_chars"] += 1
    elif fault == "missing-navigation":
        desk.context["analytical_hypothesis"]["historical_source_navigation"].pop()
    elif fault == "stale":
        desk.sources["fresh-policy"]["published_at"] = "2020-01-01T00:00:00Z"
    elif fault == "malformed-hash":
        nav["content_sha256"] = "bad"
    elif fault == "duplicate-map":
        desk.context["analytical_hypothesis"]["historical_source_navigation"].append(copy.deepcopy(nav))
    elif fault == "future":
        desk.sources["fresh-policy"]["published_at"] = "2099-01-01T00:00:00Z"
    elif fault == "foreign":
        nav["url"] = desk.sources["fresh-policy"]["url"] = (
            "https://www.sec.gov/Archives/edgar/data/2/000000000226000001/foreign.htm"
        )
    elif fault == "oversized":
        desk.context["analytical_hypothesis"]["padding"] = "x" * 64000
    elif fault == "kernel":
        args["annual_operating_forecast"]["historical_reconciliation"]["tax_provision"]["amount_usd"] = "79"
    else:
        ref = "missing-financial"
    before = copy.deepcopy(desk.sources)
    monkeypatch.setattr(desk.provider, "get", lambda *a, **k: pytest.fail("No fetch allowed"))
    result = next(t for t in desk.tools() if t.name == "evaluate_supplied_forecast").invoke(
        {"financial_source_id": ref}
    )
    assert result["available"] is False and desk.sources == before and not desk.calculations
    if fault in {"missing", "tampered-text", "wrong-url", "wrong-hash", "wrong-length"}:
        assert result["error"] == "hypothesis_originals_missing"
        assert result["missing_originals"][0] == nav
        assert "content" not in result["missing_originals"][0]


def test_hypothesis_checkpoint_replay_does_not_revalidate_or_duplicate(tmp_path):
    desk, source, _ = setup_hypothesis()
    invocation = {
        "name": "evaluate_supplied_forecast",
        "id": "candidate",
        "type": "tool_call",
        "args": {"financial_source_id": source["id"]},
    }
    with Journal(tmp_path / "ops.sqlite", {"run": "supplied"}).lease() as journal:
        checkpoint = ResearchCheckpoint(
            journal, tmp_path / "graph.sqlite", phase="initial", role="valuation", thread_id="supplied"
        )
        try:
            checkpoint.attach_desk(desk)
            tool = next(t for t in desk.tools() if t.name == "evaluate_supplied_forecast")
            guard = Guard(desk.budget, "valuation", {"evaluate_supplied_forecast"}, checkpoint=checkpoint)
            result = guard.wrap_tool_call(
                SimpleNamespace(tool_call=invocation), lambda req: tool.invoke(req.tool_call)
            )
            assert json.loads(result.content)["calculation_id"] in desk.calculations
            assert (
                guard.wrap_tool_call(
                    SimpleNamespace(tool_call=invocation), lambda req: pytest.fail("Must replay")
                )
                == result
            )
            assert len(desk.calculations) == 1 and desk.budget.tools == 1
            assert journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
        finally:
            checkpoint.close()


def test_repeated_registered_originals_and_alias_ids_remap_deterministically():
    desk, source, args = setup_hypothesis()
    baseline = next(t for t in desk.tools() if t.name == "evaluate_supplied_forecast").invoke(
        {"financial_source_id": source["id"]}
    )
    desk.sources["z-policy-copy"] = {**desk.sources["fresh-policy"], "id": "z-policy-copy"}
    repeated = next(t for t in desk.tools() if t.name == "evaluate_supplied_forecast").invoke(
        {"financial_source_id": source["id"]}
    )
    assert repeated == baseline and len(desk.calculations) == 1
    alias = {
        **desk.context["analytical_hypothesis"]["historical_source_navigation"][1],
        "id": "old-policy-alias",
    }
    desk.context["analytical_hypothesis"]["historical_source_navigation"].append(alias)
    args["annual_operating_forecast"]["cases"][0]["terminal_evidence_ids"].append("old-policy-alias")
    result = next(t for t in desk.tools() if t.name == "evaluate_supplied_forecast").invoke(
        {"financial_source_id": source["id"]}
    )
    assert (
        result["evidence_id_map"]["old-policy-alias"]
        == result["evidence_id_map"]["annual-policy"]
        == "fresh-policy"
    )
    assert result["base_value"] == baseline["base_value"]


def test_invalid_helper_schema_finishes_and_replays_operation(tmp_path):
    desk, _, _ = setup_hypothesis()
    invocation = {
        "name": "evaluate_supplied_forecast",
        "id": "bad-schema",
        "type": "tool_call",
        "args": {"financial_source_id": []},
    }
    with Journal(tmp_path / "ops.sqlite", {"run": "schema"}).lease() as journal:
        checkpoint = ResearchCheckpoint(
            journal, tmp_path / "graph.sqlite", phase="initial", role="valuation", thread_id="schema"
        )
        try:
            checkpoint.attach_desk(desk)
            tool = next(t for t in desk.tools() if t.name == "evaluate_supplied_forecast")
            guard = Guard(desk.budget, "valuation", {"evaluate_supplied_forecast"}, checkpoint=checkpoint)
            response = guard.wrap_tool_call(
                SimpleNamespace(tool_call=invocation), lambda req: tool.invoke(req.tool_call)
            )
            assert json.loads(response.content)["available"] is False
            assert (
                guard.wrap_tool_call(
                    SimpleNamespace(tool_call=invocation),
                    lambda req: pytest.fail("Schema feedback must replay"),
                )
                == response
            )
            assert (
                journal.inspect_operations() == {"complete_operations": 1, "pending_operations": []}
                and not desk.calculations
            )
        finally:
            checkpoint.close()
