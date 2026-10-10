from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from great_agent.api.dependencies import AppServices, get_services
from great_agent.errors import UnsafePathError


router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("/{task_id}")
def get_task(task_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    task = services.task_repository.get_task(task_id)
    return {
        "id": str(task.id),
        "experiment_id": str(task.experiment_id),
        "year": task.year,
        "doy": task.doy,
        "status": task.status,
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
        path = __import__("pathlib").Path(item.path).resolve()
        if not path.is_relative_to(services.run_storage.root):
            services.task_repository.publish_event(
                task_id,
                "security_error",
                {"code": "unsafe_result_path", "path": str(path)},
            )
            raise UnsafePathError("result path is outside run_root")
        results.append(
            {
                "station": item.station,
                "kind": item.kind,
                "path": str(path),
                "size_bytes": item.size_bytes,
            }
        )
    return results
