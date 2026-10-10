from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from fastapi import Request

from great_agent.db.repositories import TaskRepository
from great_agent.runtime.run_storage import RunStorage
from great_agent.runtime.worker import Worker
from great_agent.services.plan_service import PlanService, RevisionPreview
from great_agent.services.task_service import TaskService
from great_agent.services.chat_service import ChatService


@dataclass
class AppServices:
    plan_service: PlanService
    task_service: TaskService
    task_repository: TaskRepository
    worker: Worker
    run_storage: RunStorage
    chat_service: ChatService | None = None
    proposals: dict[UUID, RevisionPreview] = field(default_factory=dict)


def get_services(request: Request) -> AppServices:
    return request.app.state.services
