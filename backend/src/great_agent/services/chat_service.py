from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from time import perf_counter
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
    ModelErrorCategory,
)
from great_agent.domain.plans import ExperimentSpec
from great_agent.services.plan_service import FieldDiff, PlanService, diff_specs

logger = logging.getLogger("uvicorn.error.great_agent.chat")


def _log_stage(
    *, request_id: str | None, experiment_id: UUID, stage: str, event: str,
    elapsed_ms: float | None = None, error_category: str | None = None,
) -> None:
    record: dict[str, str | float | None] = {
        "request_id": request_id,
        "experiment_id": str(experiment_id),
        "stage": stage,
        "event": event,
    }
    if elapsed_ms is not None:
        record["elapsed_ms"] = elapsed_ms
    if error_category is not None:
        record["error_category"] = error_category
    logger.info("chat_proposal_stage %s", json.dumps(record))


@dataclass(frozen=True)
class ChatProposal:
    candidate: ExperimentSpec
    explanation: str
    diff: tuple[FieldDiff, ...]
    timings: dict[str, float]
    base_revision_id: UUID


class ChatService:
    def __init__(
        self, *, plan_service: PlanService, provider: ModelProvider
    ) -> None:
        self.plan_service = plan_service
        self.provider = provider

    async def propose(
        self, experiment_id: UUID, message: str, *, request_id: str | None = None
    ) -> ChatProposal:
        started = stage_started = perf_counter()
        stage = "plan_load"
        timings: dict[str, float] = {}
        outcome = "failed"
        _log_stage(request_id=request_id, experiment_id=experiment_id,
                   stage=stage, event="started")
        try:
            base_revision = self.plan_service.get_current_revision(experiment_id)
            current = base_revision.spec
            timings[f"{stage}_ms"] = (perf_counter() - stage_started) * 1000
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="completed", elapsed_ms=timings[f"{stage}_ms"])
            stage, stage_started = "model_request", perf_counter()
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="started")
            untrusted = await self.provider.propose_plan(message, current)
            timings[f"{stage}_ms"] = (perf_counter() - stage_started) * 1000
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="completed", elapsed_ms=timings[f"{stage}_ms"])
            stage, stage_started = "validation", perf_counter()
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="started")
            proposal = ModelPlanProposal.model_validate(untrusted)
            # Preserve all validation/frozen-plan checks; never accept or execute.
            self.plan_service.validate_candidate(
                experiment_id, proposal.candidate, base_revision_id=base_revision.id,
            )
            differences = diff_specs(current, proposal.candidate)
            timings[f"{stage}_ms"] = (perf_counter() - stage_started) * 1000
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="completed", elapsed_ms=timings[f"{stage}_ms"])
            outcome = "completed"
        except asyncio.CancelledError:
            outcome = "cancelled"
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="cancelled",
                       elapsed_ms=(perf_counter() - stage_started) * 1000)
            raise
        except (
            ModelTimeout,
            InvalidModelJson,
            InvalidModelSchema,
            ModelProviderError,
            ValidationError,
        ) as error:
            category: ModelErrorCategory
            if isinstance(error, ModelTimeout):
                category = "timeout"
            elif isinstance(error, (InvalidModelSchema, ValidationError)):
                category = "schema"
            elif isinstance(error, InvalidModelJson):
                category = "invalid_json"
            else:
                category = "provider"
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="failed", error_category=category,
                       elapsed_ms=(perf_counter() - stage_started) * 1000)
            raise ModelProposalError(category=category, request_id=request_id) from None
        except Exception:
            _log_stage(request_id=request_id, experiment_id=experiment_id,
                       stage=stage, event="failed", error_category="internal",
                       elapsed_ms=(perf_counter() - stage_started) * 1000)
            raise
        finally:
            timings.setdefault(f"{stage}_ms", (perf_counter() - stage_started) * 1000)
            timings["total_ms"] = (perf_counter() - started) * 1000
            logger.info("chat_proposal_timing %s", json.dumps({
                "request_id": request_id, "experiment_id": str(experiment_id),
                "outcome": outcome, "timings": timings,
            }))
        return ChatProposal(
            candidate=proposal.candidate,
            explanation=proposal.explanation,
            diff=differences,
            timings=timings,
            base_revision_id=base_revision.id,
        )
