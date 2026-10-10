from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from great_agent.api.dependencies import AppServices, get_services
from great_agent.errors import UnsafePathError


router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("/{task_id}")
def get_task(task_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    task = services.task_repository.get_task(task_id)
    attempts = services.task_repository.list_attempts(task_id)
    return {
        "id": str(task.id),
        "experiment_id": str(task.experiment_id),
        "year": task.year,
        "doy": task.doy,
        "status": task.status,
        "active_attempt_id": str(task.active_attempt.id) if task.active_attempt else None,
        "attempts": [
            {
                "id": str(attempt.id),
                "number": attempt.number,
                "status": attempt.status,
                "exit_code": attempt.exit_code,
                "error_code": attempt.error_code,
            }
            for attempt in attempts
        ],
    }


@router.post("/{task_id}/retry")
def retry_task(task_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    services.worker.retry(task_id)
    return {"status": "pending"}


@router.get("/{task_id}/results")
def task_results(task_id: UUID, services: AppServices = Depends(get_services)) -> list[dict]:
    task = services.task_repository.get_task(task_id)
    results = []
    for item in task.result_files():
        path = _safe_run_path(item.path, services, task_id)
        results.append(
            {
                "station": item.station,
                "kind": item.kind,
                "path": str(path),
                "size_bytes": item.size_bytes,
            }
        )
    return results


def _safe_run_path(
    path_text: str, services: AppServices, task_id: UUID
) -> Path:
    path = Path(path_text).resolve()
    if not path.is_relative_to(services.run_storage.root):
        services.task_repository.publish_event(
            task_id,
            "security_error",
            {"code": "unsafe_result_path", "path": str(path)},
        )
        raise UnsafePathError("attempt artifact is outside run_root")
    return path


@router.get("/{task_id}/attempts/{attempt_id}/log")
def attempt_log(
    task_id: UUID, attempt_id: UUID, services: AppServices = Depends(get_services)
) -> dict:
    attempt = services.task_repository.get_attempt(task_id, attempt_id)
    if attempt.log_path is None:
        return {"content": ""}
    path = _safe_run_path(attempt.log_path, services, task_id)
    return {"content": path.read_text(encoding="utf-8", errors="replace")}


@router.get("/{task_id}/attempts/{attempt_id}/results")
def attempt_results(
    task_id: UUID, attempt_id: UUID, services: AppServices = Depends(get_services)
) -> list[dict]:
    results = []
    for item in services.task_repository.list_attempt_results(task_id, attempt_id):
        path = _safe_run_path(item.path, services, task_id)
        results.append({
            "station": item.station,
            "kind": item.kind,
            "filename": path.name,
            "size_bytes": item.size_bytes,
            "url": f"/api/v1/tasks/{task_id}/attempts/{attempt_id}/results/{item.id}",
        })
    return results


@router.get("/{task_id}/attempts/{attempt_id}/results/{file_id}")
def download_result(
    task_id: UUID,
    attempt_id: UUID,
    file_id: UUID,
    services: AppServices = Depends(get_services),
) -> FileResponse:
    item = services.task_repository.get_result_file(task_id, attempt_id, file_id)
    path = _safe_run_path(item.path, services, task_id)
    return FileResponse(path, filename=path.name)
