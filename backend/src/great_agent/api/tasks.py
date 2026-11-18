from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from great_agent.api.dependencies import AppServices, get_services
from great_agent.errors import ExecutionReviewError, TaskStateError, UnsafePathError
from great_agent.db.recovery_repository import RecoveryRepository
from great_agent.db.analysis_repository import AnalysisJobRepository


router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class RecoveryRequest(BaseModel):
    expected_attempt_id: UUID
    request_id: UUID


@router.get('/{task_id}/analysis-jobs')
def analysis_jobs(task_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    try:
        return {'items': AnalysisJobRepository(services.task_repository._session_factory).list_for_task(task_id)}
    except LookupError as error:
        raise HTTPException(404, detail='unknown task') from error


@router.post('/{task_id}/analysis-jobs/{job_id}/retry')
def retry_analysis(task_id: UUID, job_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    try:
        return AnalysisJobRepository(services.task_repository._session_factory).retry(task_id, job_id)
    except LookupError as error:
        raise HTTPException(404, detail='unknown analysis job') from error


@router.get('/{task_id}/recovery')
def task_recovery(task_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    try:
        return RecoveryRepository(services.task_repository._session_factory).get(task_id)
    except LookupError as error:
        raise HTTPException(404, detail='unknown task') from error


@router.post('/{task_id}/recovery/{action}')
def request_recovery(task_id: UUID, action: str, body: RecoveryRequest,
                     services: AppServices = Depends(get_services)) -> dict:
    if action not in ('recheck', 'stop'):
        raise HTTPException(404, detail='unknown recovery action')
    try:
        return RecoveryRepository(services.task_repository._session_factory).request_action(
            task_id, body.expected_attempt_id, body.request_id, action)
    except LookupError as error:
        raise HTTPException(404, detail='unknown task') from error


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
    task = services.task_repository.get_task(task_id)
    if task.status not in ("failed", "interrupted"):
        raise TaskStateError("only failed or interrupted tasks can be retried")
    raise ExecutionReviewError("请先复核下一次尝试的 XML，再明确确认重试。", "execution_confirmation_required")


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


@router.get("/{task_id}/attempts/{attempt_id}/xml")
def attempt_xml(
    task_id: UUID, attempt_id: UUID, services: AppServices = Depends(get_services)
) -> dict:
    task = services.task_repository.get_task(task_id)
    try:
        attempt = services.task_repository.get_attempt(task_id, attempt_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail="unknown task attempt") from error
    directory = (services.run_storage.root / str(task.experiment_id) / "tasks"
        / f"{task.year:04d}_{task.doy:03d}" / "attempts" / f"{attempt.number:04d}")
    path = (directory / "config.xml").resolve()
    if not path.is_relative_to(directory.absolute()):
        raise UnsafePathError("archived XML is outside the selected attempt")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="archived XML is unavailable")
    return {"xml_path": str(path), "xml_content": path.read_bytes().decode("utf-8")}


@router.get("/{task_id}/attempts/{attempt_id}/results")
def attempt_results(
    task_id: UUID, attempt_id: UUID, services: AppServices = Depends(get_services)
) -> list[dict]:
    task = services.task_repository.get_task(task_id)
    attempt = services.task_repository.get_attempt(task_id, attempt_id)
    summary_directory = (services.run_storage.root / str(task.experiment_id) / 'tasks'
        / f'{task.year:04d}_{task.doy:03d}' / 'attempts' / f'{attempt.number:04d}' / 'analysis').resolve()
    summary_types = {'enu.png': 'enu', 'convergence.png': 'convergence', 'fix_rate.png': 'fix_rate'}
    results = []
    for item in services.task_repository.list_attempt_results(task_id, attempt_id):
        path = _safe_run_path(item.path, services, task_id)
        # Classify the canonical attempt summaries, not an identically named
        # CDF or a station image retained among archived result files.
        plot_type = summary_types.get(path.name) if (
            item.kind == 'plot' and item.station is None and path.parent == summary_directory
        ) else None
        results.append({
            "station": item.station,
            "kind": item.kind,
            "filename": path.name,
            "size_bytes": item.size_bytes,
            "plot_type": plot_type,
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
