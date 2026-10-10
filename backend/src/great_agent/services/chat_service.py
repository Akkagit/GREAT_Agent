from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError

from great_agent.adapters.model_provider import (
    InvalidModelJson,
    InvalidModelSchema,
    ModelPlanProposal,
    ModelProposalError,
    ModelProvider,
    ModelProviderError,
    ModelTimeout,
)
from great_agent.domain.plans import ExperimentSpec
from great_agent.services.plan_service import FieldDiff, PlanService, diff_specs


@dataclass(frozen=True)
class ChatProposal:
    candidate: ExperimentSpec
    explanation: str
    diff: tuple[FieldDiff, ...]


class ChatService:
    def __init__(
        self, *, plan_service: PlanService, provider: ModelProvider
    ) -> None:
        self.plan_service = plan_service
        self.provider = provider

    async def propose(self, experiment_id: UUID, message: str) -> ChatProposal:
        current = self.plan_service.get_current_spec(experiment_id)
        try:
            untrusted = await self.provider.propose_plan(message, current)
            proposal = ModelPlanProposal.model_validate(untrusted)
        except (
            ModelTimeout,
            InvalidModelJson,
            InvalidModelSchema,
            ModelProviderError,
            ValidationError,
        ) as error:
            category = "schema" if isinstance(
                error, (InvalidModelSchema, ValidationError)
            ) else "provider"
            raise ModelProposalError(
                f"model proposal {category} failure: {error}"
            ) from error

        # The plan service performs a second validation and enforces frozen state.
        self.plan_service.preview_revision(
            experiment_id, proposal.candidate, source="chat"
        )
        return ChatProposal(
            candidate=proposal.candidate,
            explanation=proposal.explanation,
            diff=diff_specs(current, proposal.candidate),
        )
