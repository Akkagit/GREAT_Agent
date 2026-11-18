from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack

import httpx
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock, ThinkingBlock
from agentscope.model import ChatResponse, FinishedReason, OpenAIChatModel
from openai import APIError, APITimeoutError
from pydantic import ValidationError

from great_agent.adapters.model_provider import (
    InvalidModelJson,
    InvalidModelSchema,
    ModelPlanProposal,
    ModelProviderError,
    ModelTimeout,
    canonicalize_station_selection,
)
from great_agent.config import ModelSettings
from great_agent.domain.plans import ExperimentSpec


# These SDK loggers can emit request bodies or exception bodies, including at
# DEBUG. AgentScope installs its own stderr handler on import. Keep the app's
# stage/timing loggers and HTTP connection diagnostics independently available.
for _logger_name in ("as", "openai", "openai._base_client", "openai._response", "openai._legacy_response"):
    logging.getLogger(_logger_name).disabled = True


class _ClientTransport(httpx.AsyncBaseTransport):
    """Use the owned client's public routing, including environment proxies."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # The surrounding AsyncExitStack owns and closes this client. Its
        # normal transport=None path retains HTTPS_PROXY/NO_PROXY semantics.
        return await self._client.send(request, stream=True)


class _SingleRequestTransport(httpx.AsyncBaseTransport):
    """Bound a proposal to one physical request and preserve wire safety flags.

    AgentScope 2.0.11 drops OpenAI's finish_reason and refusal metadata when it
    constructs ChatResponse. Validate only that envelope metadata here; the
    candidate itself is always read from the SDK's public TextBlock objects.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport
        self._requested = False
        self.rejection: ModelProviderError | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._requested:
            self.rejection = ModelProviderError("model request limit exceeded")
            raise self.rejection
        self._requested = True
        response = await self._transport.handle_async_request(request)
        if response.is_success:
            try:
                await response.aread()
                envelope = response.json()
                choice = envelope["choices"][0]
                message = choice["message"]
                if not isinstance(message, dict):
                    raise TypeError
                if message.get("refusal") is not None or choice.get("finish_reason") != "stop":
                    self.rejection = ModelProviderError("model response was refused or incomplete")
            except (ValueError, KeyError, IndexError, TypeError):
                self.rejection = InvalidModelJson("model returned an invalid completion")
            if self.rejection is not None:
                await response.aclose()
                raise self.rejection
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


class AgentScopeModelProvider:
    def __init__(
        self,
        settings: ModelSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def propose_plan(
        self, message: str, current: ExperimentSpec | None
    ) -> ModelPlanProposal:
        settings = self._settings
        boundary: _SingleRequestTransport | None = None
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.read_timeout_seconds,
            pool=settings.connect_timeout_seconds,
        )
        try:
            async with asyncio.timeout(settings.request_timeout_seconds) as deadline:
                async with AsyncExitStack() as resources:
                    upstream = await resources.enter_async_context(httpx.AsyncClient(
                        transport=self._transport, timeout=timeout, follow_redirects=False,
                    ))
                    boundary = _SingleRequestTransport(_ClientTransport(upstream))
                    client = await resources.enter_async_context(httpx.AsyncClient(
                        transport=boundary, timeout=timeout, follow_redirects=False,
                    ))
                    current_json = current.model_dump_json() if current is not None else "null"
                    prompt = (
                        "You propose complete GREAT Agent ExperimentSpec JSON for the pppflt "
                        "workflow. Use only the enums and fields in the supplied JSON schema; "
                        "forbid paths, commands, XML, and invented files. Return a complete "
                        "candidate and a short explanation. Preserve station order; first station "
                        "means stations[0]. default_15 requires exactly 15 unique stations. "
                        "When editing its station selection, use station_group_id custom; "
                        "preserve other fields unless requested. Current plan: "
                        f"{current_json}"
                    )
                    schema = ModelPlanProposal.model_json_schema()
                    if settings.response_format == "json_object":
                        prompt += " Return only a JSON object.\nJSON schema: " + json.dumps(schema)
                        response_format = {"type": "json_object"}
                    else:
                        response_format = {
                            "type": "json_schema",
                            "json_schema": {"name": "great_agent_plan_proposal", "strict": True, "schema": schema},
                        }
                    extra_body: dict[str, object] = {"max_tokens": settings.max_tokens}
                    if settings.name.lower().startswith("glm-4.7"):
                        extra_body["thinking"] = {"type": settings.thinking or "disabled"}
                    model = OpenAIChatModel(
                        credential=OpenAICredential(api_key=settings.api_key, base_url=str(settings.base_url)),
                        model=settings.name,
                        # SDK max_tokens maps to max_completion_tokens; preserve the
                        # existing compatible-vendor max_tokens request instead.
                        parameters=OpenAIChatModel.Parameters(max_tokens=None),
                        stream=False, max_retries=0,
                        client_kwargs={"http_client": client, "max_retries": 0, "timeout": timeout},
                        extra_body=extra_body,
                    )
                    response = await model([
                        Msg(name="system", role="system", content=[TextBlock(text=prompt)]),
                        Msg(name="user", role="user", content=[TextBlock(text=message)]),
                    ], response_format=response_format)
                    # The SDK consumes CancelledError and returns INTERRUPTED even
                    # for non-streaming calls. Restore the original deadline and
                    # external cancellation semantics before considering output.
                    if deadline.expired():
                        raise ModelTimeout("model request timed out")
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise asyncio.CancelledError
                    if (
                        not isinstance(response, ChatResponse)
                        or not response.is_last
                        or response.finished_reason != FinishedReason.COMPLETED
                    ):
                        raise ModelProviderError("model response was interrupted or incomplete")
                    if any(not isinstance(block, (TextBlock, ThinkingBlock)) for block in response.content):
                        raise ModelProviderError("model response contained unsupported content")
                    content = "".join(block.text for block in response.content if isinstance(block, TextBlock))
                    try:
                        untrusted = json.loads(content)
                    except (ValueError, TypeError):
                        raise InvalidModelJson("model returned invalid JSON") from None
                    try:
                        return ModelPlanProposal.model_validate(
                            canonicalize_station_selection(untrusted, current)
                        )
                    except ValidationError:
                        raise InvalidModelSchema("model returned an invalid plan schema") from None
        except (TimeoutError, APITimeoutError, httpx.TimeoutException):
            raise ModelTimeout("model request timed out") from None
        except ModelProviderError:
            raise
        except APIError:
            if boundary is not None and boundary.rejection is not None:
                raise boundary.rejection from None
            raise ModelProviderError("model provider request failed") from None
        except Exception:
            raise ModelProviderError("model provider returned an invalid response") from None
