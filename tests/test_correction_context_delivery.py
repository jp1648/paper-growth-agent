import copy
import json

import httpx
import pytest
from deepagents.middleware.filesystem import (
    FilesystemMiddleware,
    _build_truncated_human_message,
)
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from test_research_team import Evidence

from trader.config import Settings
from trader.models import dumps
from trader.research_team import Budget, Catalysts, ResearchDesk, ResearchLimit


def context():
    shared = {
        "amounts": ["1", "2", "3"],
        "rationale": "Source-anchored conditional policy. " * 3500,
        "evidence_ids": ["original"],
    }
    return {
        "calculations": {
            "base": {"calculation_id": "base", "inputs": shared},
            "variant": {"calculation_id": "variant", "inputs": copy.deepcopy(shared)},
        },
        "correction": {
            "reason": "Compute the missing ROU exclusion and intangible replacement repairs; never repeat the original work."
        },
        "research_reference": {"price": "355.58", "executable": False},
        "reports": {"business": {"gap": "preserve", "policy": copy.deepcopy(shared)}},
        "shared_policy_text": shared["rationale"],
        "z_original_source": "Retained original source text. " * 4000,
    }


def response():
    report = {
        "developments": [],
        "catalyst_timeline": [],
        "why_now": "Offline delivery fixture",
        "thesis_development": "unknown",
        "monitoring_triggers": [],
        "requires_deeper_review": True,
        "evidence_ids": ["original"],
        "missing_information": ["Unresolved economics retained"],
    }
    return httpx.Response(
        200,
        json={
            "id": "resp_fixture",
            "object": "response",
            "created_at": 1,
            "model": "gpt-6.1-sol",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "output": [
                {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(report), "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 30,
                "total_tokens": 50,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        },
    )


@pytest.mark.parametrize("oversize", [False, True])
def test_actual_deepagent_keeps_full_correction_and_reference_before_guard_admission(oversize, monkeypatch):
    cfg = Settings(
        input_cost_per_million="1",
        output_cost_per_million="1",
        research_max_input_bytes=500000,
        research_max_cost_usd="20",
    )
    calls = []
    seen = []
    original = context()
    before = copy.deepcopy(original)
    assert len(dumps(original)) > 200000 and dumps(original).index("correction") > 80000
    evicted = _build_truncated_human_message(
        HumanMessage(content=dumps(original)), "/conversation_history/fixture.md"
    )
    assert original["correction"]["reason"] not in evicted.content and "355.58" not in evicted.content
    if oversize:
        original["unrepeated_original_source"] = "x" * 510000

    def handler(request):
        calls.append(request)
        wire = json.loads(request.content)
        text = json.dumps(wire)
        assert original["correction"]["reason"] in text and "355.58" in text
        assert "Message content too large and was saved" not in text
        assert "calculation_context_pool" in text
        contexts = []

        def inspect_context(value):
            if isinstance(value, dict):
                for child in value.values():
                    inspect_context(child)
            elif isinstance(value, list):
                for child in value:
                    inspect_context(child)
            elif isinstance(value, str):
                try:
                    decoded = json.loads(value)
                except ValueError:
                    return
                if isinstance(decoded, dict) and "calculation_context_pool" in decoded:
                    contexts.append(decoded)

        inspect_context(wire)
        assert len(contexts) == 1
        projected = contexts[0]
        pool = projected["calculation_context_pool"]

        def expand(value):
            if isinstance(value, dict):
                if set(value) == {"calculation_context_ref"}:
                    return expand(pool[value["calculation_context_ref"]])
                return {k: expand(v) for k, v in value.items()}
            if isinstance(value, list):
                return [expand(v) for v in value]
            return value

        assert (
            expand(
                {
                    k: v
                    for k, v in projected.items()
                    if k not in {"calculation_context_pool", "calculation_context_projection"}
                }
            )
            == original
        )
        assert wire["store"] is False and wire["text"]["format"]["strict"] is True
        return response()

    model = ChatOpenAI(
        model="gpt-6.1-sol",
        api_key="fixture-only",
        use_responses_api=True,
        store=False,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    from trader import research_team

    factory = research_team.create_deep_agent

    def spy(**kwargs):
        seen.extend(kwargs["middleware"])
        return factory(**kwargs)

    monkeypatch.setattr(research_team, "create_deep_agent", spy)
    provider = Evidence()
    desk = ResearchDesk(
        cfg, provider, {"symbol": "ACME", "portfolio": {}}, provider.get("ACME"), Budget(cfg), model
    )
    agent = desk.agent("valuation", Catalysts, "Offline correction delivery fixture", [])
    fs = [m for m in seen if isinstance(m, FilesystemMiddleware)]
    assert len(fs) == 1 and fs[0]._human_message_token_limit_before_evict is None
    assert fs[0]._tool_token_limit_before_evict == 20000
    if oversize:
        with pytest.raises(ResearchLimit, match="context"):
            agent.invoke({"messages": [{"role": "user", "content": dumps(original)}]})
        assert not calls
    else:
        result = agent.invoke({"messages": [{"role": "user", "content": dumps(original)}]})
        assert result["structured_response"].requires_deeper_review and len(calls) == 1
        assert original == before
