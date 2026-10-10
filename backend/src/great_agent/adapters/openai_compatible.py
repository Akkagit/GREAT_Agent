from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from great_agent.adapters.model_provider import (
    InvalidModelJson,
    InvalidModelSchema,
    ModelPlanProposal,
    ModelProviderError,
    ModelTimeout,
)
from great_agent.config import ModelSettings
from great_agent.domain.plans import ExperimentSpec


class OpenAICompatibleProvider:
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
        current_json = (
            current.model_dump_json() if current is not None else "null"
        )
        system_prompt = (
            "You propose complete GREAT Agent ExperimentSpec JSON for the pppflt "
            "workflow. Use only the enums and fields in the supplied JSON schema; "
            "forbid paths, commands, XML, and invented files. Return a complete "
            "candidate and a short explanation. Current plan: "
            f"{current_json}"
        )
        request_body = {
            "model": self._settings.name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "great_agent_plan_proposal",
                    "strict": True,
                    "schema": ModelPlanProposal.model_json_schema(),
                },
            },
        }
        timeout = httpx.Timeout(
            connect=self._settings.connect_timeout_seconds,
            read=self._settings.read_timeout_seconds,
            write=self._settings.read_timeout_seconds,
            pool=self._settings.connect_timeout_seconds,
        )
        headers = {
            "Authorization": (
                f"Bearer {self._settings.api_key.get_secret_value()}"
            )
        }
        try:
            async with httpx.AsyncClient(
                base_url=f"{str(self._settings.base_url).rstrip('/')}/",
                headers=headers,
                timeout=timeout,
                transport=self._transport,
            ) as client:
                response = await client.post("chat/completions", json=request_body)
                response.raise_for_status()
        except httpx.TimeoutException as error:
            raise ModelTimeout(str(error)) from error
        except httpx.HTTPError as error:
            raise ModelProviderError(str(error)) from error

        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise InvalidModelJson(response.text) from error

        try:
            raw_proposal = json.loads(content)
        except (json.JSONDecodeError, TypeError) as error:
            raise InvalidModelJson(str(content)) from error
        try:
            return ModelPlanProposal.model_validate(raw_proposal)
        except ValidationError as error:
            raise InvalidModelSchema(raw_proposal) from error
