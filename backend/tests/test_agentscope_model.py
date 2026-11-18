from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from great_agent import cli
from great_agent.adapters.model_provider import (
    InvalidModelJson,
    InvalidModelSchema,
    ModelProviderError,
    ModelTimeout,
)
from great_agent.adapters.openai_compatible import OpenAICompatibleProvider
from great_agent.config import AppSettings, ModelSettings
from great_agent.app import create_app
from great_agent.services.chat_service import ChatService
from test_chat import base_spec, experiment, plan_service  # noqa: F401


def completion(content: str | None, *, finish_reason="stop", **message):
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "fixture",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content, **message}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def proposal_json(base_spec):
    return json.dumps({"candidate": base_spec.model_dump(mode="json"), "explanation": "unchanged"})


def provider(transport, **options):
    return OpenAICompatibleProvider(ModelSettings(
        base_url="https://model.example/v1", name="qwen-test", api_key="private-api-key", **options,
    ), transport=transport)


def test_cli_assembles_sdk_provider(valid_config):
    app = cli.build_app(AppSettings.load(valid_config, {}))
    assert type(app.state.services.chat_service.provider).__name__ == "AgentScopeModelProvider"


async def test_proposal_runs_through_real_sdk_once(base_spec, monkeypatch):
    from agentscope.model import OpenAIChatModel

    sdk_calls = []
    real_call = OpenAIChatModel.__call__

    async def tracked_call(model, *args, **kwargs):
        sdk_calls.append(model)
        return await real_call(model, *args, **kwargs)

    monkeypatch.setattr(OpenAIChatModel, "__call__", tracked_call)
    requests = []

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["max_tokens"] == 2048
        assert "max_completion_tokens" not in body
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "tools" not in body
        assert "tool_choice" not in body
        return httpx.Response(200, json=completion(proposal_json(base_spec)))

    result = await provider(httpx.MockTransport(respond)).propose_plan("private-user-input", base_spec)
    assert result.candidate == base_spec
    assert len(sdk_calls) == len(requests) == 1
    assert sdk_calls[0].client.is_closed()


@pytest.mark.parametrize("status", [400, 401, 422, 429, 500, 503])
async def test_http_errors_do_not_retry_or_expose_body(base_spec, status, caplog, capsys):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "private-provider-body"}})

    caplog.set_level(logging.DEBUG)
    with pytest.raises(ModelProviderError) as caught:
        await provider(httpx.MockTransport(respond)).propose_plan("private-user-input", base_spec)
    assert len(requests) == 1
    output = str(caught.value) + caplog.text + capsys.readouterr().err
    for secret in ("private-provider-body", "private-user-input", "private-api-key"):
        assert secret not in output


@pytest.mark.parametrize("finish_reason,message", [
    ("length", {}), ("content_filter", {}), (None, {}),
    ("tool_calls", {}), ("stop", {"refusal": "private-provider-body"}),
])
async def test_rejects_noncomplete_or_refused_valid_json(base_spec, finish_reason, message):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=completion(
        proposal_json(base_spec), finish_reason=finish_reason, **message,
    )))
    with pytest.raises(ModelProviderError):
        await provider(transport).propose_plan("change", base_spec)


@pytest.mark.parametrize("payload", [
    {}, {"choices": []}, {"choices": [{}]},
    completion(None), completion(""), completion("not-json"),
    completion(None, finish_reason="tool_calls", tool_calls=[
        {"id": "call", "type": "function", "function": {"name": "danger", "arguments": "{}"}},
    ]),
])
async def test_malformed_or_empty_response_cannot_become_proposal(base_spec, payload):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    with pytest.raises(ModelProviderError):
        await provider(httpx.MockTransport(respond)).propose_plan("change", base_spec)
    assert len(requests) == 1


async def test_non_json_completion_envelope_is_rejected_safely(base_spec):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, text="<html>private-provider-body</html>",
    ))
    with pytest.raises(InvalidModelJson) as caught:
        await provider(transport).propose_plan("change", base_spec)
    assert "private-provider-body" not in str(caught.value)


@pytest.mark.parametrize("content,error_type", [
    ("private-provider-body", InvalidModelJson),
    ('{"candidate":{"command":"private-provider-body"}}', InvalidModelSchema),
])
async def test_validation_errors_are_safe(base_spec, content, error_type, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=completion(content)))
    with pytest.raises(error_type) as caught:
        await provider(transport).propose_plan("private-user-input", base_spec)
    output = str(caught.value) + caplog.text + capsys.readouterr().err
    assert "private-provider-body" not in output
    assert "private-user-input" not in output
    assert "private-api-key" not in output


class WaitingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False
        self.closed = False
        self.requests = 0

    async def handle_async_request(self, request):
        self.requests += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("case", ["success", "status", "json", "schema", "timeout", "connection"])
async def test_client_is_closed_after_each_outcome(base_spec, case):
    class TrackingTransport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True
            await super().aclose()

    def respond(request):
        if case == "timeout":
            raise httpx.ReadTimeout("private-provider-body", request=request)
        if case == "connection":
            raise httpx.ConnectError("private-provider-body", request=request)
        content = proposal_json(base_spec)
        if case == "json":
            content = "not-json"
        if case == "schema":
            content = '{}'
        return httpx.Response(503 if case == "status" else 200, json=completion(content))

    transport = TrackingTransport(respond)
    if case == "success":
        assert (await provider(transport).propose_plan("change", base_spec)).candidate == base_spec
    else:
        with pytest.raises(ModelProviderError):
            await provider(transport).propose_plan("change", base_spec)
    assert transport.closed


async def test_total_deadline_closes_client_even_when_sdk_swallows_cancel(base_spec):
    transport = WaitingTransport()
    with pytest.raises(ModelTimeout):
        # Allow SDK construction to finish so this case reaches the pending
        # request and specifically exercises its swallowed CancelledError.
        await asyncio.wait_for(provider(transport, request_timeout_seconds=1).propose_plan("change", base_spec), 3)
    assert transport.requests == 1
    assert transport.cancelled and transport.closed


async def test_external_cancellation_propagates_and_closes_client(base_spec):
    transport = WaitingTransport()
    task = asyncio.create_task(provider(transport).propose_plan("change", base_spec))
    await asyncio.wait_for(transport.started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.requests == 1
    assert transport.cancelled and transport.closed


async def test_redirect_is_not_followed(base_spec):
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/v1/chat/completions"})

    with pytest.raises(ModelProviderError):
        await provider(httpx.MockTransport(redirect)).propose_plan("change", base_spec)
    assert len(requests) == 1


async def test_transport_guard_blocks_an_unexpected_second_sdk_request(base_spec, monkeypatch):
    from agentscope.model import OpenAIChatModel

    requests = []
    real_call = OpenAIChatModel.__call__

    async def repeated_call(model, *args, **kwargs):
        await real_call(model, *args, **kwargs)
        return await real_call(model, *args, **kwargs)

    monkeypatch.setattr(OpenAIChatModel, "__call__", repeated_call)

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion(proposal_json(base_spec)))

    with pytest.raises(ModelProviderError, match="limit"):
        await provider(httpx.MockTransport(respond)).propose_plan("change", base_spec)
    assert len(requests) == 1


@pytest.mark.parametrize("is_last,finished_reason", [(False, "completed"), (True, "interrupted")])
async def test_rejects_nonterminal_public_sdk_response(base_spec, monkeypatch, is_last, finished_reason):
    from agentscope.model import OpenAIChatModel

    real_call = OpenAIChatModel.__call__

    async def incomplete_call(model, *args, **kwargs):
        response = await real_call(model, *args, **kwargs)
        response.is_last = is_last
        response.finished_reason = finished_reason
        return response

    monkeypatch.setattr(OpenAIChatModel, "__call__", incomplete_call)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=completion(proposal_json(base_spec))))
    with pytest.raises(ModelProviderError, match="incomplete"):
        await provider(transport).propose_plan("change", base_spec)


async def test_joins_public_text_blocks_without_using_thinking_as_json(base_spec, monkeypatch):
    from agentscope.message import TextBlock, ThinkingBlock
    from agentscope.model import OpenAIChatModel

    real_call = OpenAIChatModel.__call__

    async def split_call(model, *args, **kwargs):
        response = await real_call(model, *args, **kwargs)
        content = response.content[0].text
        response.content = [ThinkingBlock(thinking="private-not-json"),
                            TextBlock(text=content[:20]), TextBlock(text=content[20:])]
        return response

    monkeypatch.setattr(OpenAIChatModel, "__call__", split_call)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=completion(proposal_json(base_spec))))
    result = await provider(transport).propose_plan("change", base_spec)
    assert result.candidate == base_spec


@pytest.mark.parametrize("status", [200, 401, 503])
def test_api_failure_never_exposes_provider_content(
    valid_config, plan_service, experiment, base_spec, caplog, capsys, status,
):
    def respond(request):
        payload = completion('{"candidate":{"command":"private-provider-body"}}')
        if status != 200:
            payload = {"error": {"message": "private-provider-body"}}
        return httpx.Response(status, json=payload)

    sdk_provider = provider(httpx.MockTransport(respond))
    services = SimpleNamespace(plan_service=plan_service,
        chat_service=ChatService(plan_service=plan_service, provider=sdk_provider))
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(AppSettings.load(valid_config, {}), services=services)) as client:
        response = client.post("/api/v1/chat/proposals", json={
            "experiment_id": str(experiment.id), "message": "private-user-input",
        })
    assert response.status_code == 502
    assert not plan_service.list_proposals(experiment.id)
    assert plan_service.get_current_revision(experiment.id).number == 1
    output = response.text + caplog.text + capsys.readouterr().err
    for secret in ("private-provider-body", "private-user-input", "private-api-key"):
        assert secret not in output
