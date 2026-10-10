from __future__ import annotations

from typing import Protocol

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


class ModelProposalError(GreatAgentError):
    """Raised when a safe chat proposal cannot be produced."""


class ModelPlanProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: ExperimentSpec
    explanation: str = Field(min_length=1)


class ModelProvider(Protocol):
    async def propose_plan(
        self, message: str, current: ExperimentSpec | None
    ) -> ModelPlanProposal: ...
