from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from great_agent.db.repositories import TaskRepository
from great_agent.runtime.run_storage import RunStorage
from great_agent.runtime.worker import Worker
from great_agent.services.plan_service import PlanService
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

    conversation_service: object | None = None
    knowledge_service: object | None = None
    knowledge_chat_service: object | None = None
    knowledge_translation_service: object | None = None
    research_service: object | None = None

    def __post_init__(self) -> None:
        self.worker.execution_service = self.task_service.execution_service


def get_services(request: Request) -> AppServices:
    return request.app.state.services
