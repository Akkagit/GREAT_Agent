from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from great_agent.db.models import (
    DailyTaskModel,
    ExperimentModel,
    PlanRevisionModel,
    TaskAttemptModel,
    TaskEventModel,
    utc_now,
)
from great_agent.errors import ExperimentNotFoundError, TaskStateError


class PlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def require_experiment(self, experiment_id: str) -> ExperimentModel:
        experiment = self.session.get(ExperimentModel, experiment_id)
        if experiment is None:
            raise ExperimentNotFoundError(f"unknown experiment: {experiment_id}")
        return experiment

    def require_current_revision(
        self, experiment: ExperimentModel
    ) -> PlanRevisionModel:
        if experiment.current_revision_id is None:
            raise RuntimeError(f"experiment {experiment.id} has no current revision")
        revision = self.session.get(
            PlanRevisionModel, experiment.current_revision_id
        )
        if revision is None:
            raise RuntimeError(
                f"experiment {experiment.id} references a missing revision"
            )
        return revision


@dataclass(frozen=True)
class ClaimedTask:
    task_id: UUID
    experiment_id: UUID
    revision_id: UUID
    year: int
    doy: int
    worker_id: str
    attempt_id: UUID
    attempt_number: int


@dataclass(frozen=True)
class AttemptRecord:
    id: UUID
    number: int
    status: str
    pid: int | None
    exit_code: int | None
    error_code: str | None
    log_path: str | None


@dataclass(frozen=True)
class TaskRecord:
    id: UUID
    experiment_id: UUID
    revision_id: UUID
    year: int
    doy: int
    status: str
    worker_id: str | None
    active_attempt: AttemptRecord | None


@dataclass(frozen=True)
class TaskEventRecord:
    sequence: int
    kind: str
    payload: dict[str, Any]


class TaskRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim_next(self, worker_id: str) -> ClaimedTask | None:
        now = utc_now()
        with self._session_factory.begin() as session:
            row = session.execute(
                text(
                    """
                    UPDATE daily_tasks
                    SET status = 'running', worker_id = :worker_id, updated_at = :now
                    WHERE id = (
                      SELECT id FROM daily_tasks
                      WHERE status = 'pending'
                      ORDER BY year, doy
                      LIMIT 1
                    )
                    AND status = 'pending'
                    RETURNING id, experiment_id, revision_id, year, doy
                    """
                ),
                {"worker_id": worker_id, "now": now},
            ).mappings().first()
            if row is None:
                return None
            attempt_number = (
                session.scalar(
                    select(func.max(TaskAttemptModel.number)).where(
                        TaskAttemptModel.task_id == row["id"]
                    )
                )
                or 0
            ) + 1
            attempt_id = uuid4()
            session.add(
                TaskAttemptModel(
                    id=str(attempt_id),
                    task_id=row["id"],
                    number=attempt_number,
                    status="running",
                    started_at=now,
                )
            )
            session.execute(
                update(DailyTaskModel)
                .where(DailyTaskModel.id == row["id"])
                .values(active_attempt_id=str(attempt_id))
            )
            self._publish_event(
                session,
                row["id"],
                "task_status",
                {"status": "running", "worker_id": worker_id},
            )
            return ClaimedTask(
                task_id=UUID(row["id"]),
                experiment_id=UUID(row["experiment_id"]),
                revision_id=UUID(row["revision_id"]),
                year=row["year"],
                doy=row["doy"],
                worker_id=worker_id,
                attempt_id=attempt_id,
                attempt_number=attempt_number,
            )

    def record_pid(self, task_id: UUID, attempt_id: UUID, pid: int) -> None:
        with self._session_factory.begin() as session:
            changed = session.execute(
                update(TaskAttemptModel)
                .where(
                    TaskAttemptModel.id == str(attempt_id),
                    TaskAttemptModel.task_id == str(task_id),
                    TaskAttemptModel.status == "running",
                )
                .values(pid=pid)
            )
            if changed.rowcount != 1:
                raise TaskStateError("PID can only be recorded for a running attempt")

    def mark_attempt_succeeded(
        self, task_id: UUID, attempt_id: UUID, *, exit_code: int
    ) -> None:
        self._finish(task_id, attempt_id, "succeeded", exit_code, None)

    def recover_stale_tasks(self, active_pids: set[int]) -> None:
        with self._session_factory.begin() as session:
            tasks = session.scalars(
                select(DailyTaskModel).where(DailyTaskModel.status == "running")
            ).all()
            for task in tasks:
                attempt = (
                    session.get(TaskAttemptModel, task.active_attempt_id)
                    if task.active_attempt_id
                    else None
                )
                if attempt is not None and attempt.pid in active_pids:
                    self._publish_event(
                        session,
                        task.id,
                        "recovery_blocked",
                        {"pid": attempt.pid, "action_required": True},
                    )
                    continue
                task.status = "interrupted"
                task.updated_at = utc_now()
                if attempt is not None:
                    attempt.status = "interrupted"
                    attempt.error_code = "interrupted"
                    attempt.finished_at = utc_now()
                self._publish_event(
                    session, task.id, "task_status", {"status": "interrupted"}
                )

    def get_task(self, task_id: UUID) -> TaskRecord:
        with self._session_factory() as session:
            task = session.get(DailyTaskModel, str(task_id))
            if task is None:
                raise LookupError(f"unknown task: {task_id}")
            attempt = (
                session.get(TaskAttemptModel, task.active_attempt_id)
                if task.active_attempt_id
                else None
            )
            return TaskRecord(
                id=UUID(task.id),
                experiment_id=UUID(task.experiment_id),
                revision_id=UUID(task.revision_id),
                year=task.year,
                doy=task.doy,
                status=task.status,
                worker_id=task.worker_id,
                active_attempt=self._attempt_record(attempt) if attempt else None,
            )

    def list_events(self, task_id: UUID) -> tuple[TaskEventRecord, ...]:
        with self._session_factory() as session:
            events = session.scalars(
                select(TaskEventModel)
                .where(TaskEventModel.task_id == str(task_id))
                .order_by(TaskEventModel.sequence)
            ).all()
            return tuple(
                TaskEventRecord(
                    sequence=event.sequence,
                    kind=event.kind,
                    payload=json.loads(event.payload_json),
                )
                for event in events
            )

    def _finish(
        self,
        task_id: UUID,
        attempt_id: UUID,
        status: str,
        exit_code: int | None,
        error_code: str | None,
    ) -> None:
        with self._session_factory.begin() as session:
            task = session.get(DailyTaskModel, str(task_id))
            attempt = session.get(TaskAttemptModel, str(attempt_id))
            if (
                task is None
                or attempt is None
                or task.status != "running"
                or attempt.status != "running"
                or task.active_attempt_id != attempt.id
            ):
                raise TaskStateError("attempt is not the active running attempt")
            task.status = status
            task.updated_at = utc_now()
            attempt.status = status
            attempt.exit_code = exit_code
            attempt.error_code = error_code
            attempt.finished_at = utc_now()
            self._publish_event(session, task.id, "task_status", {"status": status})

    @staticmethod
    def _attempt_record(attempt: TaskAttemptModel) -> AttemptRecord:
        return AttemptRecord(
            id=UUID(attempt.id),
            number=attempt.number,
            status=attempt.status,
            pid=attempt.pid,
            exit_code=attempt.exit_code,
            error_code=attempt.error_code,
            log_path=attempt.log_path,
        )

    @staticmethod
    def _publish_event(
        session: Session, task_id: str, kind: str, payload: dict[str, Any]
    ) -> None:
        sequence = (
            session.scalar(
                select(func.max(TaskEventModel.sequence)).where(
                    TaskEventModel.task_id == task_id
                )
            )
            or 0
        ) + 1
        session.add(
            TaskEventModel(
                id=str(uuid4()),
                task_id=task_id,
                sequence=sequence,
                kind=kind,
                payload_json=json.dumps(payload, sort_keys=True),
            )
        )
