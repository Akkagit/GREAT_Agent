from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from great_agent.domain.plans import ExperimentSpec
from great_agent.errors import GreatAgentError


class ModelProviderError(Exception):
    """Base error for failures at the model-provider boundary."""


class ModelTimeout(ModelProviderError):
    """Raised when the configured model does not answer in time."""


class InvalidModelJson(ModelProviderError):
    """Raised when a model response is not valid JSON."""


class InvalidModelSchema(ModelProviderError):
    """Raised when model JSON does not match the allowed plan schema."""


ModelErrorCategory = Literal["schema", "invalid_json", "timeout", "provider"]
MODEL_ERROR_MESSAGES = {
    "schema": "模型建议未通过计划校验，请重试。",
    "invalid_json": "模型返回格式无效，请重试。",
    "timeout": "模型响应超时，请稍后重试。",
    "provider": "模型服务暂时不可用，请稍后重试。",
}


class ModelProposalError(GreatAgentError):
    """A classified failure whose public text never contains provider content."""

    def __init__(
        self, message: str = "", *, category: ModelErrorCategory | None = None,
        request_id: str | None = None,
    ) -> None:
        self.category = category if category in MODEL_ERROR_MESSAGES else "provider"
        self.request_id = request_id
        super().__init__(MODEL_ERROR_MESSAGES[self.category])


def canonicalize_station_selection(
    payload: object, current: ExperimentSpec | None,
) -> object:
    """Detach an explicitly edited station snapshot from its inherited preset."""
    if current is None or current.station_group_id != "default_15" or not isinstance(payload, dict):
        return payload
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict) or candidate.get("station_group_id") != "default_15":
        return payload
    stations = candidate.get("stations")
    if not isinstance(stations, list) or not stations or any(not isinstance(s, str) for s in stations):
        return payload
    normalized = tuple(s.strip().upper() for s in stations)
    if (not all(normalized) or len(set(normalized)) != len(normalized)
            or normalized == current.stations):
        return payload
    return {**payload, "candidate": {**candidate, "station_group_id": "custom"}}


class ModelPlanProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: ExperimentSpec
    explanation: str = Field(min_length=1)


class ModelProvider(Protocol):
    async def propose_plan(
        self, message: str, current: ExperimentSpec | None
    ) -> ModelPlanProposal: ...
