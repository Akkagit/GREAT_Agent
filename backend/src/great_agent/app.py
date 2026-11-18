from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import mimetypes

from great_agent.api.dependencies import AppServices
from great_agent.api.conversations import router as conversations_router
from great_agent.api.chat import router as chat_router
from great_agent.api.events import router as events_router
from great_agent.api.experiments import router as experiments_router
from great_agent.api.tasks import router as tasks_router
from great_agent.api.monitor import router as monitor_router
from great_agent.api.knowledge import router as knowledge_router
from great_agent.api.diagnostics import router as diagnostics_router
from great_agent.config import AppSettings
from great_agent.errors import (
    ExperimentNotFoundError,
    ExecutionReviewError,
    PlanFrozenError,
    PendingProposalError,
    PlanValidationError,
    ProposalNotFoundError,
    PlanNotFrozenError,
    PreflightError,
    RevisionConflictError,
    TaskStateError,
    UnsafePathError,
)
from great_agent.adapters.model_provider import MODEL_ERROR_MESSAGES, ModelProposalError
from great_agent.research.errors import ResearchError


def create_app(
    settings: AppSettings, *, services: AppServices | None = None
) -> FastAPI:
    # Windows registry associations can override Python's .mjs MIME as plain
    # text. ES module workers require JavaScript, including self-hosted PDF.js.
    mimetypes.add_type('text/javascript', '.mjs')
    @asynccontextmanager
    async def lifespan(app):
        knowledge = getattr(services, 'knowledge_service', None) if services else None
        knowledge_chat = getattr(services, 'knowledge_chat_service', None) if services else None
        knowledge_translation = getattr(services, 'knowledge_translation_service', None) if services else None
        try:
            if knowledge is not None:
                await knowledge.start()
            yield
        finally:
            try:
                if knowledge_translation is not None:
                    await knowledge_translation.close()
                if knowledge_chat is not None:
                    await knowledge_chat.close()
            finally:
                if knowledge is not None:
                    await knowledge.stop()

    app = FastAPI(title="GREAT Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(diagnostics_router)
    if services is not None:
        app.state.services = services
        app.include_router(experiments_router)
        app.include_router(tasks_router)
        app.include_router(events_router)
        app.include_router(monitor_router)
        app.include_router(chat_router)
        app.include_router(conversations_router)
        app.include_router(knowledge_router)
        from great_agent.api.knowledge_chat import router as knowledge_chat_router
        app.include_router(knowledge_chat_router)
        from great_agent.api.knowledge_translation import router as knowledge_translation_router
        app.include_router(knowledge_translation_router)
        from great_agent.api.research import router as research_router
        app.include_router(research_router)

    def error_response(status_code: int, code: str, message: str, details=None):
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "details": details or [],
                }
            },
        )

    @app.exception_handler(ResearchError)
    async def research_error(request: Request, error: ResearchError):
        return error_response(error.status_code, error.code, error.message, error.details)

    @app.exception_handler(ExperimentNotFoundError)
    async def experiment_not_found(request: Request, error: ExperimentNotFoundError):
        del request
        return error_response(404, "not_found", str(error))

    @app.exception_handler(ExecutionReviewError)
    async def execution_review(request: Request, error: ExecutionReviewError):
        return error_response(409, error.code, str(error))

    @app.exception_handler(ProposalNotFoundError)
    async def proposal_not_found(request: Request, error: ProposalNotFoundError):
        return error_response(404, "not_found", str(error))

    @app.exception_handler(PlanValidationError)
    async def plan_validation(request: Request, error: PlanValidationError):
        return error_response(422, "plan_validation_error", str(error))

    @app.exception_handler(PendingProposalError)
    async def pending_proposal(request: Request, error: PendingProposalError):
        return error_response(409, "pending_proposal", str(error))

    @app.exception_handler(RevisionConflictError)
    async def revision_conflict(request: Request, error: RevisionConflictError):
        del request
        return error_response(409, "revision_conflict", str(error))

    @app.exception_handler(PlanFrozenError)
    async def plan_frozen(request: Request, error: PlanFrozenError):
        del request
        return error_response(409, "plan_frozen", str(error))

    @app.exception_handler(PlanNotFrozenError)
    async def plan_not_frozen(request: Request, error: PlanNotFrozenError):
        del request
        return error_response(409, "plan_not_frozen", str(error))

    @app.exception_handler(TaskStateError)
    async def task_state(request: Request, error: TaskStateError):
        del request
        return error_response(409, "task_state_error", str(error))

    @app.exception_handler(PreflightError)
    async def preflight_error(request: Request, error: PreflightError):
        del request
        details = [
            {
                "kind": item.kind,
                "year": item.date.year,
                "doy": item.date.doy,
                "station": item.station,
                "path": str(item.path),
                "state": item.state,
            }
            for item in error.missing
        ]
        return error_response(409, "preflight_error", str(error), details)

    @app.exception_handler(UnsafePathError)
    async def unsafe_path(request: Request, error: UnsafePathError):
        del request
        return error_response(500, "unsafe_result_path", str(error))

    @app.exception_handler(ModelProposalError)
    async def model_proposal(request: Request, error: ModelProposalError):
        del request
        return JSONResponse(status_code=502, content={"error": {
            "code": "model_proposal_error",
            "message": MODEL_ERROR_MESSAGES[error.category],
            "details": [],
            "category": error.category,
            "request_id": error.request_id,
        }})

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local"}

    frontend_dist = settings.project_root / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app
