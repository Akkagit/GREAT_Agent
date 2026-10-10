from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from great_agent.adapters.pppflt_preflight import PreflightItem
from great_agent.api.dependencies import AppServices, get_services
from great_agent.domain.dates import expand_dates
from great_agent.domain.plans import ExperimentSpec
from great_agent.errors import PlanFrozenError


router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


class ExperimentResponse(BaseModel):
    id: UUID
    status: str
    current_revision: int


class ExperimentDetailResponse(ExperimentResponse):
    spec: ExperimentSpec


class ProposalRequest(BaseModel):
    source: Literal["card", "chat"]
    candidate: ExperimentSpec


class ProposalResponse(BaseModel):
    id: UUID
    source: str
    candidate: ExperimentSpec
    diff: list[dict]


class AcceptRevisionRequest(BaseModel):
    proposal_id: UUID
    expected_revision: int


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
    return ExperimentResponse(id=record.id, status=record.status, current_revision=1)


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
        spec=revision.spec,
    )


@router.post("/{experiment_id}/proposals", response_model=ProposalResponse)
def preview_revision(
    experiment_id: UUID,
    request: ProposalRequest,
    services: AppServices = Depends(get_services),
) -> ProposalResponse:
    preview = services.plan_service.preview_revision(
        experiment_id, request.candidate, source=request.source
    )
    services.proposals[preview.id] = preview
    return ProposalResponse(
        id=preview.id,
        source=preview.source,
        candidate=preview.candidate,
        diff=preview.diff,
    )


@router.post("/{experiment_id}/revisions", status_code=status.HTTP_201_CREATED)
def accept_revision(
    experiment_id: UUID,
    request: AcceptRevisionRequest,
    services: AppServices = Depends(get_services),
) -> dict:
    preview = services.proposals.get(request.proposal_id)
    if preview is None or preview.experiment_id != experiment_id:
        raise HTTPException(status_code=404, detail="unknown proposal")
    record = services.plan_service.accept_revision(
        experiment_id,
        preview.candidate,
        expected_revision=request.expected_revision,
        source=preview.source,
    )
    services.proposals.pop(request.proposal_id, None)
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
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> dict:
    revision = services.plan_service.get_current_revision(experiment_id)
    missing = []
    for date in expand_dates(revision.spec):
        missing.extend(
            services.task_service.adapter.preflight_task(
                revision.spec,
                date,
                services.run_storage.task_dir(experiment_id, date),
            ).missing
        )
    return {"ok": not missing, "missing": [_preflight_detail(item) for item in missing]}


@router.post("/{experiment_id}/run")
def run_experiment(
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> dict:
    tasks = services.task_service.materialize(experiment_id)
    return {"tasks": [str(task.id) for task in tasks], "count": len(tasks)}


@router.get("/{experiment_id}/tasks")
def list_experiment_tasks(
    experiment_id: UUID, services: AppServices = Depends(get_services)
) -> list[dict]:
    services.plan_service.get_experiment(experiment_id)
    return [
        {
            "id": str(task.id),
            "year": task.year,
            "doy": task.doy,
            "status": task.status,
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
