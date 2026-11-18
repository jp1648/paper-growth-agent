import httpx
import pytest

from trader.research_checkpoint import PausedOperation, ResearchCheckpoint
from trader.research_journal import Journal, JournalError, RequestHashMismatch
from trader.research_transport import ResearchTransport


class Desk:
    def __init__(self):
        self.calls = 0

    def checkpoint(self):
        return {"calls": self.calls}

    def restore(self, state):
        self.calls = state["calls"]


@pytest.mark.parametrize("changed_request", [None, "body", "header", "credential"])
def test_response_survives_crash_before_parsing_without_network_retry(tmp_path, changed_request):
    sends = []

    def server(request):
        sends.append(request)
        return httpx.Response(200, json={"id": "original", "usage": {"input_tokens": 12}})

    path = tmp_path / "private" / "operations.sqlite"
    for attempt in range(2):
        with Journal(path, {"run": "one"}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="one"
            )
            desk = Desk()
            checkpoint.attach_desk(desk)
            try:

                def handler(checkpoint=checkpoint, attempt=attempt, desk=desk):
                    identifier = checkpoint.record_dispatch({"projected_request": "same"})
                    with httpx.Client(transport=ResearchTransport(httpx.MockTransport(server))) as client:
                        response = client.post(
                            "https://api.openai.com/v1/responses",
                            json={"model": "changed" if attempt and changed_request == "body" else "fixture"},
                            headers={
                                "X-Client-Request-Id": identifier,
                                "OpenAI-Beta": "changed"
                                if attempt and changed_request == "header"
                                else "original",
                                "Authorization": "changed"
                                if attempt and changed_request == "credential"
                                else "fixture-only",
                            },
                        )
                    if attempt == 0:
                        raise SystemExit("process lost after durable response, before parsing")
                    desk.calls += 1
                    return response.json()

                if attempt == 0:
                    with pytest.raises(SystemExit):
                        checkpoint.call("model", "business", "one", {}, handler)
                elif changed_request:
                    with pytest.raises(RequestHashMismatch):
                        checkpoint.call("model", "business", "one", {}, handler)
                else:
                    assert checkpoint.call("model", "business", "one", {}, handler)["id"] == "original"
                    assert journal.load_state() == {"calls": 1}
                assert len(sends) == 1
            finally:
                checkpoint.close()


def test_missing_response_remains_ambiguous_and_unscoped_transport_cannot_send(tmp_path):
    sends = []

    def lost(request):
        sends.append(request)
        raise httpx.ReadError("response lost")

    with httpx.Client(transport=ResearchTransport(httpx.MockTransport(lost))) as client:
        with pytest.raises(JournalError, match="outside"):
            client.post("https://api.openai.com/v1/responses", json={})
        path = tmp_path / "private" / "operations.sqlite"
        with Journal(path, {"run": "one"}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="one"
            )
            checkpoint.attach_desk(Desk())
            try:

                def handler():
                    identifier = checkpoint.record_dispatch({})
                    return client.post(
                        "https://api.openai.com/v1/responses",
                        json={},
                        headers={"X-Client-Request-Id": identifier},
                    )

                with pytest.raises(httpx.ReadError):
                    checkpoint.call("model", "business", "one", {}, handler)
                with pytest.raises(PausedOperation):
                    checkpoint.call("model", "business", "one", {}, handler)
                assert len(sends) == 1
            finally:
                checkpoint.close()


def test_real_responses_client_parses_saved_wire_response_and_usage(tmp_path):
    from types import SimpleNamespace

    from langchain.agents.middleware.types import ModelResponse
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI

    from trader.config import Settings
    from trader.models import dec
    from trader.research_team import Budget, Guard

    cfg = Settings(model="fixture", input_cost_per_million="1", output_cost_per_million="1")

    class AccountingDesk:
        def __init__(self):
            self.budget = Budget(cfg)

        def checkpoint(self):
            return {"budget": self.budget.checkpoint()}

        def restore(self, state):
            self.budget.restore(state["budget"])

    request = SimpleNamespace(
        messages=[HumanMessage(content="Investigate")],
        tools=[],
        system_message=None,
        model_settings={},
        response_format=SimpleNamespace(schema_spec=SimpleNamespace(json_schema={})),
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    sends = []

    def server(request):
        sends.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_original",
                "object": "response",
                "created_at": 1,
                "model": "fixture",
                "status": "completed",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_original",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Exact original", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 5,
                    "total_tokens": 17,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    path = tmp_path / "private" / "operations.sqlite"
    for attempt in range(2):
        with Journal(path, {"run": "sdk"}).lease() as journal:
            checkpoint = ResearchCheckpoint(
                journal, path.parent / "graph.sqlite", phase="initial", role="business", thread_id="sdk"
            )
            desk = AccountingDesk()
            checkpoint.attach_desk(desk)
            guard = Guard(desk.budget, "business", set(), checkpoint=checkpoint)
            with httpx.Client(transport=ResearchTransport(httpx.MockTransport(server))) as http_client:
                model = ChatOpenAI(
                    model="fixture",
                    api_key="fixture-only",
                    use_responses_api=True,
                    store=False,
                    max_retries=0,
                    http_client=http_client,
                )

                def handler(updated, model=model, attempt=attempt):
                    message = model.invoke(updated.messages, **updated.model_settings)
                    if attempt == 0:
                        raise SystemExit("lost parsed response before outer commit")
                    return ModelResponse(result=[message])

                try:
                    if attempt == 0:
                        with pytest.raises(SystemExit):
                            guard.wrap_model_call(request, handler)
                    else:
                        message = guard.wrap_model_call(request, handler).result[0]
                        assert message.usage_metadata["input_tokens"] == 12
                        assert message.usage_metadata["output_tokens"] == 5
                        assert "Exact original" in str(message.content)
                        assert len(sends) == 1
                        saved = journal.load_state()["budget"]
                        assert saved["models"] == 1 and saved["reserved"] == dec("0.000017")
                        charges = [event for event in saved["events"] if event[0] == "model_cost"]
                        assert len(charges) == 1 and charges[0][1]["estimated_usd"] == dec("0.000017")
                finally:
                    checkpoint.close()
