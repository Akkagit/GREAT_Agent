from uuid import UUID

from pydantic import BaseModel, ConfigDict

from great_agent.domain.plans import ExperimentSpec


class ProposalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    candidate: ExperimentSpec
    diff: list[dict]
    base_revision_id: UUID
    base_revision_number: int
    candidate_hash: str
    status: str
    source_proposal_id: UUID | None = None
    explanation: str = ""
