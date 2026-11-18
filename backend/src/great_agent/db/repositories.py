from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from great_agent.db.models import (
    DailyTaskModel,
    ExperimentModel,
    PlanRevisionModel,
    ResultFileModel,
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
    owner_session_id: str | None = None


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
    files: tuple["StoredResultFile", ...]

    def result_files(self, *, kind: str | None = None) -> tuple["StoredResultFile", ...]:
        if kind is None:
            return self.files
        return tuple(item for item in self.files if item.kind == kind)


@dataclass(frozen=True)
class StoredResultFile:
    id: UUID
    attempt_id: UUID
    station: str | None
    kind: str
    path: str
    size_bytes: int


@dataclass(frozen=True)
class TaskEventRecord:
    sequence: int
    kind: str
    payload: dict[str, Any]


class TaskRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def request_experiment_cancel(self, experiment_id: UUID) -> None:
        with self._session_factory.begin() as session:
            ids = session.scalars(update(DailyTaskModel).where(
                DailyTaskModel.experiment_id == str(experiment_id), DailyTaskModel.status == "pending",
            ).values(status="cancelled", updated_at=utc_now()).returning(DailyTaskModel.id)).all()
            for task_id in ids:
                self._publish_event(session, task_id, "task_status", {"status": "cancelled"})
            running = session.scalars(select(DailyTaskModel).where(
                DailyTaskModel.experiment_id == str(experiment_id), DailyTaskModel.status == "running",
            )).all()
            for task in running:
                self._publish_event(session, task.id, "cancel_requested", {"attempt_id": task.active_attempt_id})

    def cancellation_requested(self, task_id: UUID, attempt_id: UUID) -> bool:
        with self._session_factory() as session:
            payload = session.scalar(select(TaskEventModel.payload_json).where(
                TaskEventModel.task_id == str(task_id), TaskEventModel.kind == "cancel_requested",
            ).order_by(TaskEventModel.sequence.desc()).limit(1))
            return payload is not None and json.loads(payload).get("attempt_id") == str(attempt_id)

    def claim_next(
        self, worker_id: str, *, max_concurrency: int | None = None,
        owner_session_id: str | None = None,
    ) -> ClaimedTask | None:
        now = utc_now()
        with self._session_factory.begin() as session:
            # Acquire SQLite's writer lock before reading any quota. Independent
            # repositories wait here, then observe the preceding claim's commit.
            session.execute(text("BEGIN IMMEDIATE"))
            if max_concurrency is not None:
                running = session.scalar(
                    select(func.count()).select_from(DailyTaskModel).where(
                        DailyTaskModel.status == "running"
                    )
                )
                if running >= max_concurrency:
                    return None
            row = session.execute(
                text(
                    """
                    UPDATE daily_tasks
                    SET status = 'running', worker_id = :worker_id, updated_at = :now
                    WHERE id = (
                      SELECT candidate.id FROM daily_tasks AS candidate
                      JOIN plan_revisions AS revision
                        ON revision.id = candidate.revision_id
                       AND revision.experiment_id = candidate.experiment_id
                      WHERE candidate.status = 'pending'
                        AND revision.is_frozen = 1
                        AND (
                          SELECT COUNT(*) FROM daily_tasks AS occupied
                          WHERE occupied.status = 'running'
                            AND occupied.experiment_id = candidate.experiment_id
                        ) < COALESCE(json_extract(revision.spec_json, '$.concurrency'), 1)
                      ORDER BY candidate.year, candidate.doy
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
                    owner_session_id=owner_session_id,
                    launch_phase="reserved",
                    analysis_status="not_started",
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
                owner_session_id=owner_session_id,
            )

    @staticmethod
    def _require_owned_attempt(session, task_id, attempt_id, owner_session_id, expected_version=None):
        session.execute(text("BEGIN IMMEDIATE"))
        task = session.get(DailyTaskModel, str(task_id))
        attempt = session.get(TaskAttemptModel, str(attempt_id))
        if (task is None or attempt is None or task.status != "running"
                or task.active_attempt_id != attempt.id or attempt.status != "running"
                or attempt.owner_session_id != owner_session_id
                or (expected_version is not None and attempt.version != expected_version)):
            raise TaskStateError("attempt is not the active running attempt owned by this session")
        return task, attempt

    def prepare_spawn(self, task_id: UUID, attempt_id: UUID, command: tuple[str, ...], *,
                      owner_session_id: str | None = None, expected_version: int | None = None) -> int:
        from great_agent.runtime.process_identity import command_sha256
        with self._session_factory.begin() as session:
            _, attempt = self._require_owned_attempt(session, task_id, attempt_id, owner_session_id, expected_version)
            if attempt.launch_phase != "reserved":
                raise TaskStateError("only a reserved attempt can spawn")
            attempt.launch_phase = "spawning"
            attempt.process_executable = str(Path(command[0]).resolve())
            attempt.command_sha256 = command_sha256(command)
            attempt.version += 1
            return attempt.version

    def record_pid(self, task_id: UUID, attempt_id: UUID, pid: int, *,
                   owner_session_id: str | None = None, identity=None, expected_version: int | None = None) -> int:
        with self._session_factory.begin() as session:
            _, attempt = self._require_owned_attempt(session, task_id, attempt_id, owner_session_id, expected_version)
            attempt.pid = pid
            attempt.launch_phase = "started"
            if identity is not None:
                if identity.pid != pid:
                    raise TaskStateError("process identity PID mismatch")
                attempt.process_created_at = identity.created_at
                attempt.process_executable = identity.executable
                attempt.command_sha256 = identity.command_sha256
            attempt.version += 1
            return attempt.version

    def mark_attempt_succeeded(
        self, task_id: UUID, attempt_id: UUID, *, exit_code: int,
        owner_session_id: str | None = None, expected_version: int | None = None,
    ) -> None:
        self._finish(task_id, attempt_id, "succeeded", exit_code, None, owner_session_id, expected_version)

    def complete_solver(self, claim: ClaimedTask, files, *, analysis_config: dict | None) -> None:
        """Raw results, solver success and analysis enqueue must commit together."""
        from great_agent.db.analysis_repository import AnalysisJobRepository
        from great_agent.db.monitor_repository import register_result_files
        with self._session_factory.begin() as session:
            task, attempt = self._require_owned_attempt(session, claim.task_id, claim.attempt_id, claim.owner_session_id)
            task.status = attempt.status = 'succeeded'
            task.updated_at = attempt.finished_at = utc_now()
            attempt.exit_code, attempt.error_code, attempt.launch_phase = 0, None, 'exited'
            attempt.version += 1
            register_result_files(self, claim.task_id, claim.attempt_id, files, session=session)
            if analysis_config is not None:
                AnalysisJobRepository.enqueue_in_session(session, task, attempt, analysis_config)
            else:
                self._publish_event(session, task.id, 'analysis_skipped', {
                    'attempt_id': attempt.id,
                    'reason': 'select a static DF-float reference experiment to analyze this scenario',
                })
            self._publish_event(session, task.id, 'task_status', {'status': 'succeeded', 'attempt_id': attempt.id})

    def mark_attempt_failed(
        self,
        task_id: UUID,
        attempt_id: UUID,
        *,
        exit_code: int | None,
        error_code: str,
        owner_session_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        self._finish(task_id, attempt_id, "failed", exit_code, error_code, owner_session_id, expected_version)

    def mark_attempt_cancelled(
        self, task_id: UUID, attempt_id: UUID, *, exit_code: int | None,
        owner_session_id: str | None = None, expected_version: int | None = None,
    ) -> None:
        self._finish(task_id, attempt_id, "cancelled", exit_code, None, owner_session_id, expected_version)

    def set_attempt_log_path(
        self, task_id: UUID, attempt_id: UUID, log_path: str, *,
        owner_session_id: str | None = None, expected_version: int | None = None,
    ) -> int:
        with self._session_factory.begin() as session:
            _, attempt = self._require_owned_attempt(session, task_id, attempt_id, owner_session_id, expected_version)
            attempt.log_path = log_path
            attempt.version += 1
            return attempt.version

    def add_result_files(
        self,
        task_id: UUID,
        attempt_id: UUID,
        files: tuple[object, ...],
    ) -> None:
        from great_agent.db.monitor_repository import register_result_files
        register_result_files(self, task_id, attempt_id, files)

    def retry_task(self, task_id: UUID) -> None:
        with self._session_factory.begin() as session:
            changed = session.execute(
                update(DailyTaskModel)
                .where(
                    DailyTaskModel.id == str(task_id),
                    DailyTaskModel.status.in_(("failed", "interrupted")),
                )
                .values(status="pending", worker_id=None, updated_at=utc_now())
            )
            if changed.rowcount != 1:
                raise TaskStateError("only failed or interrupted tasks can be retried")
            self._publish_event(session, str(task_id), "attempt_queued", {"status": "pending"})

    def running_task_ids(self, experiment_id: UUID) -> tuple[UUID, ...]:
        with self._session_factory() as session:
            ids = session.scalars(
                select(DailyTaskModel.id).where(
                    DailyTaskModel.experiment_id == str(experiment_id),
                    DailyTaskModel.status == "running",
                )
            ).all()
            return tuple(UUID(value) for value in ids)

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
                # Compatibility for legacy callers only. A PID snapshot has
                # no authority over attempts managed by session ownership.
                if attempt is not None and attempt.owner_session_id is not None:
                    continue
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
            files = session.scalars(
                select(ResultFileModel).where(
                    ResultFileModel.task_id == task.id,
                    ResultFileModel.attempt_id == task.active_attempt_id,
                )
            ).all() if task.active_attempt_id else []
            return TaskRecord(
                id=UUID(task.id),
                experiment_id=UUID(task.experiment_id),
                revision_id=UUID(task.revision_id),
                year=task.year,
                doy=task.doy,
                status=task.status,
                worker_id=task.worker_id,
                active_attempt=self._attempt_record(attempt) if attempt else None,
                files=tuple(
                    StoredResultFile(
                        id=UUID(item.id),
                        attempt_id=UUID(item.attempt_id),
                        station=item.station,
                        kind=item.kind,
                        path=item.path,
                        size_bytes=item.size_bytes,
                    )
                    for item in files
                ),
            )

    def list_attempts(self, task_id: UUID) -> tuple[AttemptRecord, ...]:
        with self._session_factory() as session:
            attempts = session.scalars(
                select(TaskAttemptModel)
                .where(TaskAttemptModel.task_id == str(task_id))
                .order_by(TaskAttemptModel.number)
            ).all()
            return tuple(self._attempt_record(attempt) for attempt in attempts)

    def get_attempt(self, task_id: UUID, attempt_id: UUID) -> AttemptRecord:
        with self._session_factory() as session:
            attempt = session.get(TaskAttemptModel, str(attempt_id))
            if attempt is None or attempt.task_id != str(task_id):
                raise LookupError(f"unknown attempt: {attempt_id}")
            return self._attempt_record(attempt)

    def list_attempt_results(
        self, task_id: UUID, attempt_id: UUID
    ) -> tuple[StoredResultFile, ...]:
        self.get_attempt(task_id, attempt_id)
        with self._session_factory() as session:
            files = session.scalars(
                select(ResultFileModel)
                .where(
                    ResultFileModel.task_id == str(task_id),
                    ResultFileModel.attempt_id == str(attempt_id),
                )
                .order_by(ResultFileModel.station, ResultFileModel.kind)
            ).all()
            return tuple(self._stored_result(item) for item in files)

    def get_result_file(
        self, task_id: UUID, attempt_id: UUID, file_id: UUID
    ) -> StoredResultFile:
        with self._session_factory() as session:
            item = session.get(ResultFileModel, str(file_id))
            if (
                item is None
                or item.task_id != str(task_id)
                or item.attempt_id != str(attempt_id)
            ):
                raise LookupError(f"unknown result file: {file_id}")
            return self._stored_result(item)

    def list_events(
        self, task_id: UUID, *, after_sequence: int = 0
    ) -> tuple[TaskEventRecord, ...]:
        with self._session_factory() as session:
            events = session.scalars(
                select(TaskEventModel)
                .where(
                    TaskEventModel.task_id == str(task_id),
                    TaskEventModel.sequence > after_sequence,
                )
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

    def publish_event(
        self, task_id: UUID, kind: str, payload: dict[str, Any]
    ) -> None:
        with self._session_factory.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self._publish_event(session, str(task_id), kind, payload)

    def list_tasks(self, experiment_id: UUID) -> tuple[TaskRecord, ...]:
        with self._session_factory() as session:
            ids = session.scalars(
                select(DailyTaskModel.id)
                .where(DailyTaskModel.experiment_id == str(experiment_id))
                .order_by(DailyTaskModel.year, DailyTaskModel.doy)
            ).all()
        return tuple(self.get_task(UUID(value)) for value in ids)

    def _finish(
        self,
        task_id: UUID,
        attempt_id: UUID,
        status: str,
        exit_code: int | None,
        error_code: str | None,
        owner_session_id: str | None = None,
        expected_version: int | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            task, attempt = self._require_owned_attempt(session, task_id, attempt_id, owner_session_id, expected_version)
            task.status = status
            task.updated_at = utc_now()
            attempt.status = status
            attempt.exit_code = exit_code
            attempt.error_code = error_code
            attempt.finished_at = utc_now()
            attempt.launch_phase = "exited"
            attempt.version += 1
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
    def _stored_result(item: ResultFileModel) -> StoredResultFile:
        return StoredResultFile(
            id=UUID(item.id),
            attempt_id=UUID(item.attempt_id),
            station=item.station,
            kind=item.kind,
            path=item.path,
            size_bytes=item.size_bytes,
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
        event_id = str(uuid4())
        session.add(
            TaskEventModel(id=event_id, task_id=task_id, sequence=sequence,
                kind=kind, payload_json=json.dumps(payload, sort_keys=True))
        )
        from great_agent.db.monitor_repository import append_monitor_event
        append_monitor_event(session, task_id, kind, payload, task_event_id=event_id)
