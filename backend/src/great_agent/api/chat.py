from __future__ import annotations

import asyncio
import json
import logging
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from great_agent.api.dependencies import AppServices, get_services
from great_agent.api.proposal_models import ProposalResponse
from great_agent.adapters.model_provider import ModelProposalError


router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
logger = logging.getLogger("uvicorn.error.great_agent.chat")


class ChatProposalRequest(BaseModel):
    experiment_id: UUID
    message: str = Field(min_length=1)


class ChatProposalResponse(ProposalResponse):
    request_id: str
    timings: dict[str, float]


async def _wait_for_disconnect(request: Request) -> None:
    while not await request.is_disconnected():
        await asyncio.sleep(.1)


@router.post("/proposals", response_model=ChatProposalResponse)
async def create_chat_proposal(
    request: ChatProposalRequest,
    http_request: Request,
    services: AppServices = Depends(get_services),
) -> ChatProposalResponse:
    if services.chat_service is None:
        raise HTTPException(status_code=503, detail="model provider unavailable")
    started = perf_counter()
    request_id = str(uuid4())
    start_record: dict[str, str | int | float] = {
        "request_id": request_id,
        "experiment_id": str(request.experiment_id),
    }
    app = http_request.scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    if settings is not None:
        start_record.update({
            "model": settings.model.name,
            "request_timeout_seconds": settings.model.request_timeout_seconds,
            "read_timeout_seconds": settings.model.read_timeout_seconds,
            "max_tokens": settings.model.max_tokens,
        })
    logger.info("chat_request_started %s", json.dumps(start_record))
    generation = asyncio.create_task(services.chat_service.propose(
        request.experiment_id, request.message, request_id=request_id
    ))
    disconnection = asyncio.create_task(_wait_for_disconnect(http_request))
    outcome = "failed"
    try:
        await asyncio.wait((generation, disconnection), return_when=asyncio.FIRST_COMPLETED)
        if disconnection.done() or await http_request.is_disconnected():
            outcome = "cancelled"
            raise HTTPException(status_code=499, detail="proposal request cancelled")
        proposal = await generation
        preview = services.plan_service.preview_revision(
            request.experiment_id, proposal.candidate, source="chat",
            base_revision_id=proposal.base_revision_id,
            explanation=proposal.explanation,
        )
        response = ChatProposalResponse(
            **ProposalResponse.model_validate(preview).model_dump(),
            request_id=request_id,
            timings={**proposal.timings, "api_total_ms": (perf_counter() - started) * 1000},
        )
        outcome = "completed"
        return response
    except ModelProposalError as error:
        # Correlate even a directly raised service error with this API request.
        raise ModelProposalError(category=error.category, request_id=request_id) from None
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        for task in (generation, disconnection):
            if not task.done():
                task.cancel()
        await asyncio.gather(generation, disconnection, return_exceptions=True)
        logger.info("chat_request_timing %s", json.dumps({"request_id": request_id,
            "outcome": outcome, "api_total_ms": (perf_counter() - started) * 1000}))
