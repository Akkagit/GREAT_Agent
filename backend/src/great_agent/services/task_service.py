from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.adapters.pppflt_preflight import PreflightItem
from great_agent.db.models import DailyTaskModel
from great_agent.domain.dates import YearDoy, expand_dates
from great_agent.errors import PlanNotFrozenError, PreflightError
from great_agent.runtime.run_storage import RunStorage
from great_agent.services.plan_service import PlanService, RevisionRecord


@dataclass(frozen=True)
class DailyTaskRecord:
    id: UUID
    experiment_id: UUID
    revision_id: UUID
    year: int
    doy: int
    status: str
    station_count: int


class TaskService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        plan_service: PlanService,
        adapter: GreatPppfltAdapter,
        run_storage: RunStorage,
    ) -> None:
        self._session_factory = session_factory
        self.plan_service = plan_service
        self.adapter = adapter
        self.run_storage = run_storage

    def materialize(self, experiment_id: UUID) -> tuple[DailyTaskRecord, ...]:
        revision = self.plan_service.get_current_revision(experiment_id)
        if not revision.is_frozen:
            raise PlanNotFrozenError(
                f"experiment {experiment_id} must be frozen before materialization"
            )
        dates = expand_dates(revision.spec)
        existing = self._existing_tasks(experiment_id, revision)
        if existing:
            if len(existing) != len(dates):
                raise RuntimeError(
                    f"experiment {experiment_id} has a partial materialization"
                )
            return existing

        preflight_items: list[PreflightItem] = []
        missing: list[PreflightItem] = []
        for date in dates:
            report = self.adapter.preflight_task(
                revision.spec, date, self.run_storage.task_dir(experiment_id, date)
            )
            preflight_items.extend(report.items)
            missing.extend(report.missing)
        if missing:
            raise PreflightError("Required inputs are missing", tuple(missing))

        self._write_snapshots(experiment_id, revision, dates, preflight_items)
        try:
            with self._session_factory.begin() as session:
                models = []
                for date in dates:
                    model = DailyTaskModel(
                        id=str(uuid4()),
                        experiment_id=str(experiment_id),
                        revision_id=str(revision.id),
                        year=date.year,
                        doy=date.doy,
                        status="pending",
                        worker_id=None,
                        active_attempt_id=None,
                    )
                    session.add(model)
                    models.append(model)
                session.flush()
                records = tuple(
                    self._record(model, len(revision.spec.stations))
                    for model in models
                )
        except IntegrityError:
            existing = self._existing_tasks(experiment_id, revision)
            if len(existing) == len(dates):
                return existing
            raise
        return records

    def _existing_tasks(
        self, experiment_id: UUID, revision: RevisionRecord
    ) -> tuple[DailyTaskRecord, ...]:
        with self._session_factory() as session:
            models = session.scalars(
                select(DailyTaskModel)
                .where(
                    DailyTaskModel.experiment_id == str(experiment_id),
                    DailyTaskModel.revision_id == str(revision.id),
                )
                .order_by(DailyTaskModel.year, DailyTaskModel.doy)
            ).all()
            return tuple(
                self._record(model, len(revision.spec.stations)) for model in models
            )

    def _write_snapshots(
        self,
        experiment_id: UUID,
        revision: RevisionRecord,
        dates: tuple[YearDoy, ...],
        preflight_items: list[PreflightItem],
    ) -> None:
        experiment_dir = self.run_storage.experiment_dir(experiment_id)
        frozen_path = experiment_dir / "plan" / "frozen_plan.json"
        revision_path = (
            experiment_dir / "plan" / "revisions" / f"{revision.number:04d}.json"
        )
        plan_json = revision.spec.model_dump(mode="json")
        self.run_storage.atomic_json(frozen_path, plan_json)
        self.run_storage.atomic_json(revision_path, plan_json)
        plan_hash = hashlib.sha256(frozen_path.read_bytes()).hexdigest()

        for date in dates:
            task_dir = self.run_storage.task_dir(experiment_id, date)
            (task_dir / "attempts").mkdir(parents=True, exist_ok=True)
            (task_dir / "results").mkdir(parents=True, exist_ok=True)

        template = self.adapter.settings.template_path.resolve()
        template_stat = template.stat()
        manifest = {
            "experiment_id": str(experiment_id),
            "revision": revision.number,
            "plan_sha256": plan_hash,
            "template": {
                "path": str(template),
                "size_bytes": template_stat.st_size,
                "mtime_ns": template_stat.st_mtime_ns,
            },
            "executable": str(self.adapter.settings.executable.resolve()),
            "stations": list(revision.spec.stations),
            "inputs": [
                {
                    "kind": item.kind,
                    "year": item.date.year,
                    "doy": item.date.doy,
                    "station": item.station,
                    "path": str(item.path),
                }
                for item in preflight_items
            ],
        }
        self.run_storage.atomic_json(experiment_dir / "manifest.json", manifest)
        self.run_storage.atomic_json(
            experiment_dir / "summary.json",
            {
                "experiment_id": str(experiment_id),
                "status": "pending",
                "total_tasks": len(dates),
            },
        )

    @staticmethod
    def _record(model: DailyTaskModel, station_count: int) -> DailyTaskRecord:
        return DailyTaskRecord(
            id=UUID(model.id),
            experiment_id=UUID(model.experiment_id),
            revision_id=UUID(model.revision_id),
            year=model.year,
            doy=model.doy,
            status=model.status,
            station_count=station_count,
        )
