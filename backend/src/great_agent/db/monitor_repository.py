from __future__ import annotations

import json
import re
from contextlib import nullcontext
from collections import defaultdict
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from great_agent.db.models import DailyTaskModel, ExperimentModel, ResultFileModel, TaskAttemptModel
from great_agent.db.monitor_models import ExperimentEventModel, MonitorStateModel
from great_agent.errors import ExperimentNotFoundError


class CursorError(ValueError):
    code = "invalid_cursor"
    status_code = 400


class CursorResetRequired(CursorError):
    code = "cursor_reset_required"
    status_code = 409


def _state(session: Session) -> MonitorStateModel:
    state = session.get(MonitorStateModel, 1)
    if state is None:
        session.execute(insert(MonitorStateModel).values(
            id=1, epoch=str(uuid4()), retention_floor_json="{}",
        ).on_conflict_do_nothing(index_elements=["id"]))
        state = session.get(MonitorStateModel, 1)
    assert state is not None
    return state


def _cursor(epoch: str, experiment_id: str, position: int) -> str:
    return f"v1:{epoch}:{experiment_id}:{position}"


def _recovery(attempt: TaskAttemptModel | None) -> dict:
    actions = []
    if attempt and attempt.status == "running" and attempt.recovery_state in ("blocked", "observing"):
        actions.append("recheck")
        if attempt.recovery_state == "observing" and all((attempt.pid, attempt.process_created_at,
                attempt.process_executable, attempt.command_sha256)):
            actions.append("stop")
    return {
        "state": attempt.recovery_state if attempt else "none",
        "reason": attempt.recovery_reason if attempt else None,
        "last_checked_at": attempt.last_checked_at.isoformat() if attempt and attempt.last_checked_at else None,
        "version": attempt.version if attempt else 0,
        "allowed_actions": actions,
    }


def _attempt(attempt: TaskAttemptModel) -> dict:
    return {
        "id": attempt.id, "number": attempt.number, "status": attempt.status,
        "pid": attempt.pid, "exit_code": attempt.exit_code, "error_code": attempt.error_code,
        "log_path": attempt.log_path, "analysis_status": attempt.analysis_status,
        "artifact_version": attempt.artifact_version, "recovery": _recovery(attempt),
    }


def _task(task: DailyTaskModel, attempts: list[TaskAttemptModel], results: list[ResultFileModel]) -> dict:
    active = next((item for item in attempts if item.id == task.active_attempt_id), None)
    return {
        "id": task.id, "experiment_id": task.experiment_id, "revision_id": task.revision_id,
        "year": task.year, "doy": task.doy, "status": task.status, "worker_id": task.worker_id,
        "active_attempt_id": task.active_attempt_id, "active_attempt": _attempt(active) if active else None,
        "attempts": [_attempt(item) for item in attempts],
        "results": [{"station": item.station, "kind": item.kind, "size_bytes": item.size_bytes}
            for item in results if item.attempt_id == task.active_attempt_id],
        "analysis_status": active.analysis_status if active else "not_started",
        "artifact_version": active.artifact_version if active else 0,
        "next_attempt_number": max((item.number for item in attempts), default=0) + 1,
        "recovery": _recovery(active),
    }


_KINDS = {
    "task_status": "task.status", "task_created": "task.created",
    "attempt_queued": "attempt.queued", "attempt_started": "attempt.started",
    "cancel_requested": "task.cancel_requested", "artifacts_changed": "artifacts.changed",
    "analysis_started": "analysis.started", "analysis_completed": "analysis.completed",
    "analysis_failed": "analysis.failed", "analysis_skipped": "analysis.skipped",
    "recovery_changed": "recovery.changed", "recovery_blocked": "recovery.changed",
}


def append_monitor_event(
    session: Session, task_id: str, kind: str, payload: dict[str, Any], task_event_id: str | None = None,
) -> ExperimentEventModel:
    """Append and project using the caller's transaction; never commit independently."""
    session.flush()
    if task_event_id:
        previous = session.scalar(select(ExperimentEventModel).where(
            ExperimentEventModel.task_event_id == task_event_id,
        ))
        if previous is not None:
            return previous
    task = session.get(DailyTaskModel, str(task_id))
    if task is None:
        raise LookupError(f"unknown task: {task_id}")
    _state(session)
    event_type = _KINDS.get(kind, kind.replace("_", "."))
    if event_type == "task.status" and payload.get("status") == "running":
        event_type = "attempt.started"
    if kind.startswith("recovery_"):
        event_type = "recovery.changed"
    queued = event_type in ("task.created", "attempt.queued") or (
        event_type == "task.status" and payload.get("status") == "pending"
    )
    attempt_id = None if queued else payload.get("attempt_id", task.active_attempt_id)
    attempt = session.get(TaskAttemptModel, str(attempt_id)) if attempt_id else None
    if attempt is not None and attempt.task_id != task.id:
        raise ValueError("event attempt does not belong to task")
    if attempt_id and attempt is None:
        raise LookupError(f"unknown attempt: {attempt_id}")
    data = dict(payload)
    if event_type.startswith("analysis.") and attempt:
        status = event_type.split(".", 1)[1]
        attempt.analysis_status = "running" if status == "started" else status
        attempt.artifact_version += 1
    elif event_type == "artifacts.changed" and attempt:
        attempt.artifact_version += 1
    session.flush()
    attempts = list(session.scalars(select(TaskAttemptModel).where(
        TaskAttemptModel.task_id == task.id,
    ).order_by(TaskAttemptModel.number)))
    results = list(session.scalars(select(ResultFileModel).where(ResultFileModel.task_id == task.id)))
    data["task"] = _task(task, attempts, results)
    if queued:
        data["next_attempt_number"] = data["task"]["next_attempt_number"]
    if attempt:
        data["analysis_status"] = attempt.analysis_status
        data["artifact_version"] = attempt.artifact_version
    row = ExperimentEventModel(
        experiment_id=task.experiment_id, task_id=task.id, attempt_id=attempt_id,
        task_event_id=task_event_id, kind=event_type, payload_json=json.dumps(data, ensure_ascii=False),
        schema_version=1,
    )
    session.add(row)
    session.flush()
    return row


def register_result_files(repository, task_id: UUID, attempt_id: UUID, files: tuple[object, ...],
                          *, analysis_payload: dict | None = None, session: Session | None = None) -> None:
    """Result metadata, artifact revision and analysis completion share one commit."""
    own_transaction = session is None
    with repository._session_factory.begin() if own_transaction else nullcontext(session) as session:
        if own_transaction:
            session.execute(text("BEGIN IMMEDIATE"))
        attempt = session.get(TaskAttemptModel, str(attempt_id))
        if attempt is None or attempt.task_id != str(task_id):
            raise LookupError(f"unknown attempt: {attempt_id}")
        existing = {item.path: item for item in session.scalars(select(ResultFileModel).where(
            ResultFileModel.task_id == str(task_id), ResultFileModel.attempt_id == str(attempt_id),
        ))}
        for item in files:
            path = str(getattr(item, "path"))
            stored = existing.get(path)
            if stored is None:
                stored = ResultFileModel(id=str(uuid4()), task_id=str(task_id), attempt_id=str(attempt_id),
                    path=path, station=getattr(item, "station"), kind=getattr(item, "kind"),
                    size_bytes=getattr(item, "size_bytes"))
                session.add(stored)
                existing[path] = stored
            else:
                stored.size_bytes = getattr(item, "size_bytes")
                stored.station = getattr(item, "station")
                stored.kind = getattr(item, "kind")
        if files:
            repository._publish_event(session, str(task_id), "artifacts_changed", {
                "attempt_id": str(attempt_id), "artifact_count": len(files),
            })
        if analysis_payload is not None:
            repository._publish_event(session, str(task_id), "analysis_completed", {
                **analysis_payload, "attempt_id": str(attempt_id),
            })


class MonitorRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        # Migrations initialize this row. This also supports isolated create_all databases.
        with session_factory.begin() as session:
            _state(session)

    @staticmethod
    def _require(session: Session, experiment_id: str) -> None:
        if session.get(ExperimentModel, experiment_id) is None:
            raise ExperimentNotFoundError(f"unknown experiment: {experiment_id}")

    def snapshot(self, experiment_id: UUID | str) -> dict:
        experiment_id = str(experiment_id)
        with self._session_factory() as session:
            # SELECT does not start a SQLite transaction in legacy sqlite3 mode.
            # BEGIN (not IMMEDIATE) fixes one read snapshot without reserving a writer.
            session.execute(text("BEGIN"))
            self._require(session, experiment_id)
            state = session.get(MonitorStateModel, 1)
            assert state is not None
            high_water = session.scalar(select(func.max(ExperimentEventModel.id)).where(
                ExperimentEventModel.experiment_id == experiment_id,
            )) or 0
            tasks = list(session.scalars(select(DailyTaskModel).where(
                DailyTaskModel.experiment_id == experiment_id,
            ).order_by(DailyTaskModel.year, DailyTaskModel.doy)))
            attempts: dict[str, list] = defaultdict(list)
            results: dict[str, list] = defaultdict(list)
            for item in session.scalars(select(TaskAttemptModel).join(
                DailyTaskModel, DailyTaskModel.id == TaskAttemptModel.task_id,
            ).where(DailyTaskModel.experiment_id == experiment_id).order_by(TaskAttemptModel.number)):
                attempts[item.task_id].append(item)
            for item in session.scalars(select(ResultFileModel).join(
                DailyTaskModel, DailyTaskModel.id == ResultFileModel.task_id,
            ).where(DailyTaskModel.experiment_id == experiment_id)):
                results[item.task_id].append(item)
            return {"experiment_id": experiment_id, "cursor": _cursor(state.epoch, experiment_id, high_water),
                "tasks": [_task(task, attempts[task.id], results[task.id]) for task in tasks]}

    @staticmethod
    def _parse(cursor: str, experiment_id: str) -> tuple[str, int]:
        parts = cursor.split(":") if isinstance(cursor, str) else []
        if len(parts) != 4 or parts[0] != "v1" or not re.fullmatch(r"0|[1-9][0-9]*", parts[3]):
            raise CursorError("Last-Event-ID must contain a snapshot cursor")
        try:
            if str(UUID(parts[1])) != parts[1] or str(UUID(parts[2])) != parts[2]:
                raise ValueError
        except ValueError as error:
            raise CursorError("invalid cursor identity") from error
        if parts[2] != experiment_id or len(parts[3]) > 19 or int(parts[3]) > 2**63 - 1:
            raise CursorError("cursor does not belong to this experiment or is out of range")
        return parts[1], int(parts[3])

    def events(self, experiment_id: UUID | str, cursor: str, limit: int = 200) -> list[dict]:
        experiment_id = str(experiment_id)
        epoch, position = self._parse(cursor, experiment_id)
        with self._session_factory() as session:
            session.execute(text("BEGIN"))
            self._require(session, experiment_id)
            state = session.get(MonitorStateModel, 1)
            assert state is not None
            high_water = session.scalar(select(func.max(ExperimentEventModel.id)).where(
                ExperimentEventModel.experiment_id == experiment_id,
            )) or 0
            floor = int(json.loads(state.retention_floor_json).get(experiment_id, 0))
            if epoch != state.epoch or position > high_water or position < floor:
                raise CursorResetRequired("reload the experiment monitor snapshot")
            rows = session.scalars(select(ExperimentEventModel).where(
                ExperimentEventModel.experiment_id == experiment_id, ExperimentEventModel.id > position,
            ).order_by(ExperimentEventModel.id).limit(max(1, min(limit, 200)))).all()
            return [{"schema_version": row.schema_version,
                "event_id": _cursor(state.epoch, experiment_id, row.id), "experiment_id": experiment_id,
                "task_id": row.task_id, "attempt_id": row.attempt_id, "type": row.kind,
                "data": json.loads(row.payload_json)} for row in rows]

    def rotate_epoch(self) -> str:
        with self._session_factory.begin() as session:
            state = _state(session)
            state.epoch = str(uuid4())
            return state.epoch
