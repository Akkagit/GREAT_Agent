from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from great_agent.api.dependencies import AppServices
from great_agent.api.events import router as events_router
from great_agent.api.experiments import router as experiments_router
from great_agent.api.tasks import router as tasks_router
from great_agent.config import AppSettings
from great_agent.errors import (
    ExperimentNotFoundError,
    PlanFrozenError,
    PlanNotFrozenError,
    PreflightError,
    RevisionConflictError,
    TaskStateError,
    UnsafePathError,
)


def create_app(
    settings: AppSettings, *, services: AppServices | None = None
) -> FastAPI:
    app = FastAPI(title="GREAT Agent", version="0.1.0")
    app.state.settings = settings
    if services is not None:
        app.state.services = services
        app.include_router(experiments_router)
        app.include_router(tasks_router)
        app.include_router(events_router)

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

    @app.exception_handler(ExperimentNotFoundError)
    async def experiment_not_found(request: Request, error: ExperimentNotFoundError):
        del request
        return error_response(404, "not_found", str(error))

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

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local"}

    return app
