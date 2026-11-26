import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from test_research_team import Evidence

from trader.config import Settings
from trader.models import dec, dumps
from trader.research_checkpoint import ResearchCheckpoint
from trader.research_journal import Journal
from trader.research_team import ROLES, Budget, Guard, ResearchDesk, role_output_tokens
from trader.research_transport import ResearchTransport


def desk_for(cfg=None):
    cfg = cfg or Settings(input_cost_per_million="5", output_cost_per_million="10")
    provider = Evidence()
    return ResearchDesk(
        cfg, provider, {"symbol": "ACME", "portfolio": {}}, provider.get("ACME"), Budget(cfg), None
    )


def model_request(messages, tools):
    return SimpleNamespace(
        messages=messages,
        tools=tools,
        system_message=None,
        model_settings={},
        response_format=SimpleNamespace(schema_spec=SimpleNamespace(json_schema={})),
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )


@pytest.mark.parametrize("fault", [None, "orphan", "missing", "duplicate"])
def test_compact_lead_keeps_complete_latest_tool_batch_without_mutating_history(fault):
    reports = {role: {"unresolved_material_gaps": True} for role in ROLES}
    calls = [
        {"name": "request_correction", "args": {"reason": "too long"}, "id": name, "type": "tool_call"}
        for name in ("first", "second")
    ]
    assistant = AIMessage(content="", tool_calls=calls)
    results = [
        ToolMessage(content="Shorten reason to2000", tool_call_id="first", status="error"),
        ToolMessage(content="Repair unavailable", tool_call_id="second", status="error"),
    ]
    if fault == "orphan":
        results[-1].tool_call_id = "unknown"
    elif fault == "missing":
        results.pop()
    elif fault == "duplicate":
        results[-1].tool_call_id = "first"
    messages = [HumanMessage(content="old orchestration"), assistant, *results]
    before = copy.deepcopy(messages)
    guard = Guard(
        Budget(Settings()),
        "research_lead",
        {"request_correction"},
        reports,
        lambda: {"reports": reports},
        lambda: True,
    )
    projected, _, _ = guard._model_request(model_request(messages, [{"name": "request_correction"}]))
    assert messages == before
    assert json.loads(projected[0].content)["reports"] == reports
    if fault is None:
        assert projected[1:] == [assistant, *results]
        payload = ChatOpenAI(
            model="fixture", api_key="fixture-only", use_responses_api=True
        )._get_request_payload(projected)
        assert [row["call_id"] for row in payload["input"] if row["type"] == "function_call"] == [
            "first",
            "second",
        ]
        assert [row["call_id"] for row in payload["input"] if row["type"] == "function_call_output"] == [
            "first",
            "second",
        ]
    else:
        assert len(projected) == 1


@pytest.mark.parametrize("refusal_kind", ["schema", "budget"])
def test_refusal_count_persists_once_per_new_operation_and_hides_tool_not_gaps(tmp_path, refusal_kind):
    desk = (
        desk_for(
            Settings(input_cost_per_million="5", output_cost_per_million="10", research_max_cost_usd=".1")
        )
        if refusal_kind == "budget"
        else desk_for()
    )
    desk.reports = {role: {"unresolved_material_gaps": True} for role in ROLES}
    reports = copy.deepcopy(desk.reports)
    args = {
        "role": "valuation",
        "report_sha256": hashlib.sha256(dumps(desk.reports["valuation"]).encode()).hexdigest(),
        "reason": "Repair material valuation defect" if refusal_kind == "budget" else "x" * 2001,
    }
    tool_call = {"name": "request_correction", "id": "first", "type": "tool_call", "args": args}
    with Journal(tmp_path / "ops.sqlite", {"run": "refusals"}).lease() as journal:
        checkpoint = ResearchCheckpoint(
            journal, tmp_path / "graph.sqlite", phase="initial", role="research_lead", thread_id="refusals"
        )
        try:
            checkpoint.attach_desk(desk)
            tool = desk.correction_tool()
            assert tool.get_input_schema().model_json_schema()["properties"]["reason"]["maxLength"] == 2000
            guard = Guard(
                desk.budget, "research_lead", {"request_correction"}, desk.reports, checkpoint=checkpoint
            )
            first = guard.wrap_tool_call(
                SimpleNamespace(tool_call=tool_call), lambda req: tool.invoke(req.tool_call)
            )
            assert desk.continuation["correction_refusals"] == 1 and desk.correction_available()
            assert (
                "insufficient remaining estimated cost" if refusal_kind == "budget" else "1–2000"
            ) in first.content
            assert (
                guard.wrap_tool_call(
                    SimpleNamespace(tool_call=tool_call),
                    lambda req: pytest.fail("Replayed refusal dispatched"),
                )
                == first
            )
            assert desk.continuation["correction_refusals"] == 1
            saved = desk.checkpoint()
            restored = desk_for(desk.cfg)
            restored.restore(saved)
            assert restored.continuation["correction_refusals"] == 1
            second_call = {**tool_call, "id": "second"}
            second = guard.wrap_tool_call(
                SimpleNamespace(tool_call=second_call), lambda req: tool.invoke(req.tool_call)
            )
            assert desk.continuation["correction_refusals"] == 2 and not desk.correction_available()
            assert desk.reports == reports and desk.correction is None
            lead = Guard(
                desk.budget,
                "research_lead",
                {"request_correction"},
                desk.reports,
                desk.final_context,
                desk.correction_available,
            )
            assistant = AIMessage(content="", tool_calls=[second_call])
            projected, offered, _ = lead._model_request(
                model_request([HumanMessage(content="old"), assistant, second], [tool])
            )
            assert not offered and projected[-1] == second
            context = json.loads(projected[0].content)
            assert context["correction_available"] is False and context["correction_refusals"] == 2
            assert context["reports"] == reports and context["last_correction_refusal"] in second.content
        finally:
            checkpoint.close()


@pytest.mark.parametrize(
    "role,expected",
    [
        ("business", 5000),
        ("catalysts", 5000),
        ("adversarial", 5000),
        ("research_lead", 5000),
        ("valuation", 12000),
    ],
)
def test_role_output_reserve_wire_and_durable_projection_agree(role, expected, tmp_path):
    cfg = Settings(
        model="fixture",
        research_max_output_tokens=12000,
        input_cost_per_million="5",
        output_cost_per_million="10",
        research_max_cost_usd="20",
    )
    desk = desk_for(cfg)
    assert role_output_tokens(Settings(), role) == 3000
    assert role_output_tokens(cfg, role) == expected
    request = model_request([HumanMessage(content="Bounded role response")], [])
    seen = []

    def server(req):
        body = json.loads(req.content)
        seen.append(body)
        assert body["max_output_tokens"] == expected
        return httpx.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "model": "fixture",
                "created_at": 1,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "id": "msg_fixture",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Done", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    with Journal(tmp_path / "ops.sqlite", {"run": role}).lease() as journal:
        checkpoint = ResearchCheckpoint(
            journal, tmp_path / "graph.sqlite", phase="initial", role=role, thread_id=role
        )
        try:
            checkpoint.attach_desk(desk)
            guard = Guard(desk.budget, role, set(), checkpoint=checkpoint)
            with httpx.Client(transport=ResearchTransport(httpx.MockTransport(server))) as client:
                model = ChatOpenAI(
                    model="fixture",
                    api_key="fixture-only",
                    use_responses_api=True,
                    store=False,
                    service_tier="default",
                    reasoning={"effort": "medium"},
                    max_tokens=12000,
                    max_retries=0,
                    http_client=client,
                )

                def handler(updated):
                    projected = journal.accounting_evidence()["exchanges"][-1]["projected_request"]
                    assert projected["max_output_tokens"] == expected
                    size = next(
                        body["total_bytes"]
                        for kind, body in reversed(desk.budget.events)
                        if kind == "research_context"
                    )
                    assert desk.budget.reserved == (dec(size + 2048) * 5 + dec(expected) * 10) / dec(1000000)
                    message = model.invoke(updated.messages, **updated.model_settings)
                    from trader.research_accounting import _current_usage

                    usage, _ = _current_usage(journal.accounting_evidence(), cfg, role)
                    assert usage["input_tokens"] == usage["output_tokens"] == 1
                    return ModelResponse(result=[message])

                guard.wrap_model_call(request, handler)
                assert len(seen) == 1 and journal.inspect_operations() == {
                    "complete_operations": 1,
                    "pending_operations": [],
                }
        finally:
            checkpoint.close()
