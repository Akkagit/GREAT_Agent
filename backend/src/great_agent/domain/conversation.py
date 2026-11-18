from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field
from great_agent.api.proposal_models import ProposalResponse
from great_agent.domain.plans import ExperimentSpec

class TextBlock(BaseModel):
    type: Literal['text'] = 'text'
    text: str
class ProposalBlock(BaseModel):
    type: Literal['proposal'] = 'proposal'
    proposal: ProposalResponse
class ExecutionBlock(BaseModel):
    type: Literal['execution'] = 'execution'
    experiment_id: UUID
    preview_id: UUID
    task_ids: list[UUID]
class ExperimentCreatedBlock(BaseModel):
    type: Literal['experiment_created'] = 'experiment_created'
    experiment_id: UUID
    status: Literal['draft'] = 'draft'
    current_revision: Literal[1] = 1
    spec: ExperimentSpec
class Message(BaseModel):
    id: UUID
    role: Literal['user','assistant']
    status: Literal['completed','failed','interrupted']
    blocks: list[TextBlock | ProposalBlock | ExecutionBlock | ExperimentCreatedBlock]
    request_id: str | None = None
class ConversationSummary(BaseModel):
    id: UUID
    experiment_id: UUID | None
    title: str
class ConversationDetail(ConversationSummary):
    messages: list[Message]
class TurnResponse(BaseModel):
    conversation_id: UUID
    run_id: UUID
    request_id: str
    status: Literal['completed']
    user_message: Message
    assistant_message: Message
    timings: dict
class EnsureConversationRequest(BaseModel):
    experiment_id: UUID | None = None
class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32000)
    client_request_id: UUID
    execution_preview_id: UUID | None = None
