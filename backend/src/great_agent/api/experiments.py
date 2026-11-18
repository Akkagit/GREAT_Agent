from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from great_agent.adapters.pppflt_preflight import PreflightItem
from great_agent.api.dependencies import AppServices, get_services
from great_agent.api.proposal_models import ProposalResponse
from great_agent.domain.dates import expand_dates
from great_agent.domain.plans import ExperimentSpec
from great_agent.errors import ExecutionReviewError, RevisionConflictError


router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


class ExperimentResponse(BaseModel):
    id: UUID
    status: str
    current_revision: int
    current_revision_id: UUID


class ExperimentDetailResponse(ExperimentResponse):
    spec: ExperimentSpec


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["card", "chat"]
    candidate: ExperimentSpec
    base_revision_id: UUID
    source_proposal_id: UUID | None = None


class AcceptRevisionRequest(BaseModel):
    proposal_id: UUID
    expected_revision: int


class InputValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision_id: UUID
    expected_revision: StrictInt


class ExecutionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int
    task_id: UUID | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: UUID
    expected_revision: int
    confirmed: StrictBool


def _revision_response(record) -> dict:
    return {
        "id": str(record.id),
        "number": record.number,
        "source": record.source,
        "is_frozen": record.is_frozen,
        "spec": record.spec.model_dump(mode="json"),
    }


def _preflight_detail(item: PreflightItem) -> dict:
    return {
        "kind": item.kind,
        "year": item.date.year,
        "doy": item.date.doy,
        "station": item.station,
        "path": str(item.path),
        "state": item.state,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_experiment(
    spec: ExperimentSpec, services: AppServices = Depends(get_services)
) -> ExperimentResponse:
    record = services.plan_service.create_experiment(spec)
    return ExperimentResponse(
        id=record.id, status=record.status, current_revision=1,
        current_revision_id=record.current_revision_id,
    )


@router.get("")
def list_experiments(limit: int = Query(20, ge=1, le=100), services: AppServices = Depends(get_services)) -> list[dict]:
    return services.plan_service.list_experiments(limit)


@router.get("/{experiment_id}", response_model=ExperimentDetailResponse)
def get_experiment(
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> ExperimentDetailResponse:
    experiment = services.plan_service.get_experiment(experiment_id)
    revision = services.plan_service.get_current_revision(experiment_id)
    return ExperimentDetailResponse(
        id=experiment.id,
        status=experiment.status,
        current_revision=revision.number,
        current_revision_id=revision.id,
        spec=revision.spec,
    )


@router.post("/{experiment_id}/proposals", response_model=ProposalResponse)
def preview_revision(
    experiment_id: UUID,
    request: ProposalRequest,
    services: AppServices = Depends(get_services),
) -> ProposalResponse:
    preview = services.plan_service.preview_revision(
        experiment_id, request.candidate, source=request.source,
        base_revision_id=request.base_revision_id,
        source_proposal_id=request.source_proposal_id,
    )
    return ProposalResponse.model_validate(preview)


@router.get("/{experiment_id}/proposals", response_model=list[ProposalResponse])
def list_proposals(
    experiment_id: UUID,
    proposal_status: Literal["pending", "accepted", "rejected", "superseded", "stale"] | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    services: AppServices = Depends(get_services),
) -> list[ProposalResponse]:
    return [ProposalResponse.model_validate(item) for item in
            services.plan_service.list_proposals(experiment_id, status=proposal_status, limit=limit)]


@router.get("/{experiment_id}/proposals/{proposal_id}", response_model=ProposalResponse)
def get_proposal(
    experiment_id: UUID, proposal_id: UUID,
    services: AppServices = Depends(get_services),
) -> ProposalResponse:
    return ProposalResponse.model_validate(services.plan_service.get_proposal(experiment_id, proposal_id))


@router.post("/{experiment_id}/proposals/{proposal_id}/reject", response_model=ProposalResponse)
def reject_proposal(
    experiment_id: UUID, proposal_id: UUID,
    services: AppServices = Depends(get_services),
) -> ProposalResponse:
    return ProposalResponse.model_validate(services.plan_service.reject_proposal(experiment_id, proposal_id))


@router.post("/{experiment_id}/revisions", status_code=status.HTTP_201_CREATED)
def accept_revision(
    experiment_id: UUID,
    request: AcceptRevisionRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    record = services.plan_service.accept_proposal(
        experiment_id, request.proposal_id, expected_revision=request.expected_revision,
    )
    return _revision_response(record)


@router.post("/{experiment_id}/freeze")
def freeze_experiment(
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> dict:
    current = services.plan_service.get_current_revision(experiment_id)
    return _revision_response(
        services.plan_service.freeze_revision(
            experiment_id, expected_revision=current.number
        )
    )


@router.post("/{experiment_id}/validate")
def validate_experiment(
    experiment_id: UUID, request: InputValidationRequest | None = None,
    services: AppServices = Depends(get_services),
) -> dict:
    revision = services.plan_service.get_current_revision(experiment_id)
    if request is not None and (
        revision.id != request.expected_revision_id
        or revision.number != request.expected_revision
    ):
        raise RevisionConflictError("input check is based on a stale revision")

    # Inspect only this immutable revision's spec, including for legacy no-body calls.
    missing = []
    for date in expand_dates(revision.spec):
        missing.extend(
            services.task_service.adapter.preflight_task(
                revision.spec,
                date,
                services.run_storage.task_dir(experiment_id, date),
            ).missing
        )
    current = services.plan_service.get_current_revision(experiment_id)
    if current.id != revision.id or current.number != revision.number:
        raise RevisionConflictError("experiment changed during input check")
    return {
        "revision_id": str(revision.id), "revision": revision.number,
        "ok": not missing, "missing": [_preflight_detail(item) for item in missing],
    }


@router.post("/{experiment_id}/run")
def run_experiment(
    experiment_id: UUID, request: RunRequest | None = None,
    services: AppServices = Depends(get_services),
) -> dict:
    if request is None:
        raise ExecutionReviewError("请先生成 XML，并在执行预览中明确确认。", "execution_confirmation_required")
    tasks = services.task_service.execution_service.confirm(
        experiment_id, preview_id=request.preview_id, expected_revision=request.expected_revision, confirmed=request.confirmed,
    )
    return {"tasks": [str(task.id) for task in tasks], "count": len(tasks)}


@router.post("/{experiment_id}/execution-preview")
def prepare_execution(
    experiment_id: UUID, request: ExecutionPreviewRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    return services.task_service.execution_service.prepare(
        experiment_id, expected_revision=request.expected_revision, task_id=request.task_id,
    ).model_dump(mode="json")


@router.get("/{experiment_id}/execution-previews/{preview_id}")
def get_execution_preview(experiment_id: UUID, preview_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    preview = services.task_service.execution_service.get(preview_id)
    if preview.experiment_id != experiment_id:
        raise ExecutionReviewError("执行预览不属于当前实验。")
    return preview.model_dump(mode="json")


@router.get("/{experiment_id}/tasks")
def list_experiment_tasks(
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> list[dict]:
    services.plan_service.get_experiment(experiment_id)
    return [
        {
            "id": str(task.id),
            "experiment_id": str(task.experiment_id),
            "year": task.year,
            "doy": task.doy,
            "status": task.status,
            "results": [
                {
                    "station": item.station,
                    "kind": item.kind,
                    "size_bytes": item.size_bytes,
                }
                for item in task.result_files(kind="flt")
            ],
        }
        for task in services.task_repository.list_tasks(experiment_id)
    ]


@router.post("/{experiment_id}/stop")
async def stop_experiment(
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> dict:
    services.plan_service.get_experiment(experiment_id)
    await services.worker.cancel_experiment(experiment_id)
    return {"status": "stopping"}
