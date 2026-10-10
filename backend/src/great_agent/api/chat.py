from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from great_agent.api.dependencies import AppServices, get_services
from great_agent.domain.plans import ExperimentSpec


router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatProposalRequest(BaseModel):
    experiment_id: UUID
    message: str = Field(min_length=1)


class ChatProposalResponse(BaseModel):
    id: UUID
    source: str
    candidate: ExperimentSpec
    explanation: str
    diff: list[dict]


@router.post("/proposals", response_model=ChatProposalResponse)
async def create_chat_proposal(
    request: ChatProposalRequest,
    services: AppServices = Depends(get_services),
) -> ChatProposalResponse:
    if services.chat_service is None:
        raise HTTPException(status_code=503, detail="model provider unavailable")
    proposal = await services.chat_service.propose(
        request.experiment_id, request.message
    )
    preview = services.plan_service.preview_revision(
        request.experiment_id, proposal.candidate, source="chat"
    )
    services.proposals[preview.id] = preview
    return ChatProposalResponse(
        id=preview.id,
        source="chat",
        candidate=proposal.candidate,
        explanation=proposal.explanation,
        diff=[item.as_dict() for item in proposal.diff],
    )
