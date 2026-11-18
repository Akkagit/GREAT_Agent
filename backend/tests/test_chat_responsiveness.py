from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from great_agent.adapters.model_provider import ModelProposalError, ModelProviderError
from great_agent.adapters.openai_compatible import OpenAICompatibleProvider
from great_agent.api.chat import ChatProposalRequest, create_chat_proposal
from great_agent.app import create_app
from great_agent.config import AppSettings, ModelSettings
from great_agent.services.chat_service import ChatService
from test_chat import base_spec, experiment, model_completion, plan_service, provider  # noqa: F401


@pytest.mark.parametrize("model,thinking,expected", [
    ("glm-4.7", None, {"type": "disabled"}),
    ("glm-4.7", "enabled", {"type": "enabled"}),
    ("other-provider", None, None),
    ("qwen-plus", "disabled", None),
    ("qwen-plus", "enabled", None),
])
async def test_thinking_control_reaches_transport_with_output_cap(base_spec, model, thinking, expected):
    def respond(request):
        body = json.loads(request.content)
        assert body.get("thinking") == expected
        assert body["max_tokens"] == 2048
        assert "request_timeout_seconds" not in body
        return httpx.Response(200, json=model_completion(json.dumps({
            "candidate": base_spec.model_dump(mode="json"), "explanation": "unchanged"
        })))

    options = {} if thinking is None else {"thinking": thinking}
    settings = ModelSettings(base_url="https://model.example/v1", name=model, api_key="fixture-key", **options)
    result = await OpenAICompatibleProvider(settings, transport=httpx.MockTransport(respond)).propose_plan("change", base_spec)
    assert result.candidate == base_spec


def timing_records(caplog):
    return [json.loads(r.getMessage().split("chat_proposal_timing ", 1)[1])
            for r in caplog.records if r.getMessage().startswith("chat_proposal_timing ")]


def stage_records(caplog):
    return [json.loads(r.getMessage().split("chat_proposal_stage ", 1)[1])
            for r in caplog.records if r.getMessage().startswith("chat_proposal_stage ")]


async def test_service_measures_stages_without_logging_content(plan_service, provider, experiment, caplog):
    class DelayedProvider:
        async def propose_plan(self, message, current):
            await asyncio.sleep(.02)
            return await provider.propose_plan(message, current)

    caplog.set_level(logging.INFO)
    result = await ChatService(plan_service=plan_service, provider=DelayedProvider()).propose(experiment.id, "private-user-content")
    assert set(result.timings) == {"plan_load_ms", "model_request_ms", "validation_ms", "total_ms"}
    assert result.timings["model_request_ms"] >= 10
    assert result.timings["total_ms"] >= result.timings["model_request_ms"]
    assert all(value >= 0 for value in result.timings.values())
    assert timing_records(caplog)[-1]["outcome"] == "completed"
    assert [(record["stage"], record["event"]) for record in stage_records(caplog)] == [
        ("plan_load", "started"),
        ("plan_load", "completed"),
        ("model_request", "started"),
        ("model_request", "completed"),
        ("validation", "started"),
        ("validation", "completed"),
    ]
    assert "private-user-content" not in caplog.text
    assert plan_service.get_current_revision(experiment.id).number == 1


async def test_error_timing_excludes_provider_error_and_prompt(plan_service, provider, experiment, caplog):
    caplog.set_level(logging.INFO)
    provider.raise_on_call = ModelProviderError("secret-provider-text")
    with pytest.raises(ModelProposalError):
        await ChatService(plan_service=plan_service, provider=provider).propose(experiment.id, "private-user-content")
    record = timing_records(caplog)[-1]
    assert record["outcome"] == "failed"
    assert record["timings"]["model_request_ms"] >= 0
    failed_stage = stage_records(caplog)[-1]
    assert failed_stage["request_id"] is None
    assert failed_stage["experiment_id"] == str(experiment.id)
    assert failed_stage["stage"] == "model_request"
    assert failed_stage["event"] == "failed"
    assert failed_stage["error_category"] == "provider"
    assert failed_stage["elapsed_ms"] >= 0
    assert "secret-provider-text" not in caplog.text
    assert "private-user-content" not in caplog.text


def test_api_returns_request_id_and_stage_timings(valid_config, plan_service, provider, experiment, caplog):
    services = SimpleNamespace(plan_service=plan_service,
        chat_service=ChatService(plan_service=plan_service, provider=provider), proposals={})
    app = create_app(AppSettings.load(valid_config, {}), services=services)
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/proposals", json={"experiment_id": str(experiment.id), "message": "change"})
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"]
    assert data["timings"]["api_total_ms"] >= data["timings"]["model_request_ms"]
    assert data["id"] in {str(item.id) for item in plan_service.list_proposals(experiment.id)}
    assert plan_service.get_current_revision(experiment.id).number == 1
    started = [json.loads(r.getMessage().split("chat_request_started ", 1)[1])
               for r in caplog.records if r.getMessage().startswith("chat_request_started ")][-1]
    assert started["model"] == "qwen-plus"
    assert started["request_timeout_seconds"] == 180
    assert started["max_tokens"] == 2048


async def test_disconnect_cancels_upstream_without_storing_proposal(plan_service, experiment, caplog):
    started, cancelled, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class WaitingProvider:
        async def propose_plan(self, message, current):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    caplog.set_level(logging.INFO)
    services = SimpleNamespace(plan_service=plan_service,
        chat_service=ChatService(plan_service=plan_service, provider=WaitingProvider()), proposals={})
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/chat/proposals", "headers": []}, receive=receive)
    task = asyncio.create_task(create_chat_proposal(
        request=ChatProposalRequest(experiment_id=experiment.id, message="private-user-content"),
        http_request=request, services=services))
    await asyncio.wait_for(started.wait(), timeout=1)
    disconnected.set()
    with pytest.raises(HTTPException) as error:
        await asyncio.wait_for(task, timeout=1)
    assert error.value.status_code == 499
    assert cancelled.is_set()
    assert not plan_service.list_proposals(experiment.id)
    assert plan_service.get_current_revision(experiment.id).number == 1
    assert timing_records(caplog)[-1]["outcome"] == "cancelled"
    assert "private-user-content" not in caplog.text


async def test_final_preview_failure_is_logged_as_failed(plan_service, provider, experiment, caplog):
    class RejectingPlanService:
        def preview_revision(self, *args, **kwargs):
            raise RuntimeError("private-failure-detail")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    # The model service validates normally; fail only the final API preview.
    services = SimpleNamespace(plan_service=RejectingPlanService(),
        chat_service=ChatService(plan_service=plan_service, provider=provider), proposals={})
    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError, match="private-failure-detail"):
        await create_chat_proposal(
            request=ChatProposalRequest(experiment_id=experiment.id, message="change"),
            http_request=Request({"type": "http", "method": "POST", "headers": []}, receive=receive),
            services=services)
    logs = [json.loads(r.getMessage().split("chat_request_timing ", 1)[1])
            for r in caplog.records if r.getMessage().startswith("chat_request_timing ")]
    assert logs[-1]["outcome"] == "failed"
    assert not plan_service.list_proposals(experiment.id)
    assert "private-failure-detail" not in caplog.text
