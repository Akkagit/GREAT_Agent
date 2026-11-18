from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select, text

from great_agent.db.models import (DailyTaskModel, ExecutionApprovalModel,
    ExecutionPreviewModel, TaskAttemptModel, TaskEventModel, utc_now)
from great_agent.db.repositories import TaskRepository
from great_agent.errors import TaskStateError


@dataclass(frozen=True)
class RecoveryAttempt:
    task_id: str
    attempt_id: str
    owner_session_id: str | None
    version: int
    launch_phase: str
    pid: int | None
    process_created_at: int | None
    process_executable: str | None
    command_sha256: str | None
    recovery_state: str
    command: tuple[str, ...] | None
    recheck_requests: tuple[str, ...] = ()


class RecoveryRepository:
    def __init__(self, session_factory):
        self.sessions = session_factory

    def block_cleanup(self, claim) -> None:
        with self.sessions.begin() as session:
            task, attempt = TaskRepository._require_owned_attempt(
                session, claim.task_id, claim.attempt_id, claim.owner_session_id)
            if (attempt.recovery_state, attempt.recovery_reason) == ('blocked', 'process_cleanup_uncertain'):
                return
            attempt.recovery_state = 'blocked'
            attempt.recovery_reason = 'process_cleanup_uncertain'
            attempt.version += 1
            TaskRepository._publish_event(session, task.id, 'recovery_changed', {
                'attempt_id': attempt.id, 'recovery_state': attempt.recovery_state,
                'recovery_reason': attempt.recovery_reason, 'status': task.status,
            })

    @staticmethod
    def _pending_rechecks(session, task_id: str, attempt_id: str) -> tuple[str, ...]:
        requests = []
        checked = set()
        events = session.execute(select(TaskEventModel.kind, TaskEventModel.payload_json).where(
            TaskEventModel.task_id == task_id,
            TaskEventModel.kind.in_(('recovery_requested', 'recovery_checked')),
        ).order_by(TaskEventModel.sequence)).all()
        for kind, payload_json in events:
            payload = json.loads(payload_json)
            if payload.get('attempt_id') != attempt_id:
                continue
            request_id = payload.get('request_id')
            if kind == 'recovery_checked':
                checked.add(request_id)
            elif payload.get('action') == 'recheck' and request_id:
                requests.append(request_id)
        return tuple(request_id for request_id in requests if request_id not in checked)

    def candidates(self, owner_session_id: str) -> tuple[RecoveryAttempt, ...]:
        with self.sessions() as session:
            rows = session.execute(select(DailyTaskModel, TaskAttemptModel).join(
                TaskAttemptModel, DailyTaskModel.active_attempt_id == TaskAttemptModel.id
            ).where(DailyTaskModel.status == 'running', TaskAttemptModel.status == 'running',
                or_(TaskAttemptModel.owner_session_id.is_(None),
                    TaskAttemptModel.owner_session_id != owner_session_id,
                    TaskAttemptModel.recovery_state.in_(('observing', 'blocked'))))).all()
            result = []
            for task, attempt in rows:
                command = None
                approval = session.get(ExecutionApprovalModel, (task.id, attempt.number))
                if approval:
                    preview = session.get(ExecutionPreviewModel, approval.preview_id)
                    if preview and preview.confirmed_at:
                        try:
                            data = json.loads(preview.payload_json)
                            job = data['jobs'][approval.job_index]
                            candidate = job['command']
                            if isinstance(candidate, list) and candidate and all(isinstance(arg, str) for arg in candidate):
                                command = tuple(candidate)
                        except (ValueError, TypeError, IndexError, KeyError):
                            # A damaged historical approval is insufficient
                            # identity evidence, never a reason to stop the loop.
                            command = None
                result.append(RecoveryAttempt(task.id, attempt.id, attempt.owner_session_id,
                    attempt.version, attempt.launch_phase, attempt.pid, attempt.process_created_at,
                    attempt.process_executable, attempt.command_sha256, attempt.recovery_state, command,
                    self._pending_rechecks(session, task.id, attempt.id)))
            return tuple(result)

    def apply(self, expected: RecoveryAttempt, *, owner_session_id: str, state: str,
              reason: str | None, identity=None, checked: bool = True) -> bool:
        """Only a matching active attempt/owner/version can change state or free quota."""
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            task = session.get(DailyTaskModel, expected.task_id)
            if task is None or task.status != 'running' or task.active_attempt_id != expected.attempt_id:
                return False
            attempt = session.get(TaskAttemptModel, expected.attempt_id)
            if (attempt is None or attempt.status != 'running' or attempt.version != expected.version
                    or attempt.owner_session_id != expected.owner_session_id):
                return False
            changed = attempt.recovery_state != state or attempt.recovery_reason != reason or attempt.owner_session_id != owner_session_id
            attempt.owner_session_id = owner_session_id
            if checked:
                attempt.last_checked_at = utc_now()
            attempt.version += 1
            if identity is not None:
                attempt.pid = identity.pid
                attempt.process_created_at = identity.created_at
                attempt.process_executable = identity.executable
                attempt.command_sha256 = identity.command_sha256
                attempt.launch_phase = 'started'
            if state in ('interrupted', 'cancelled'):
                task.status = attempt.status = state
                task.updated_at = attempt.finished_at = utc_now()
                attempt.exit_code = None
                attempt.error_code = reason
                attempt.launch_phase = 'exited'
                attempt.recovery_state = 'resolved'
                attempt.recovery_reason = reason
                if attempt.analysis_status == 'running':
                    attempt.analysis_status = 'failed'
                    TaskRepository._publish_event(session, task.id, 'analysis_failed', {
                        'attempt_id': attempt.id, 'reason': 'analysis_interrupted',
                        'cause': reason, 'message': 'Analysis interrupted during worker recovery',
                    })
                TaskRepository._publish_event(session, task.id, 'task_status', {
                    'status': state, 'attempt_id': attempt.id, 'reason': reason})
            else:
                attempt.recovery_state, attempt.recovery_reason = state, reason
            if changed:
                TaskRepository._publish_event(session, task.id, 'recovery_changed', {
                    'attempt_id': attempt.id, 'recovery_state': attempt.recovery_state,
                    'recovery_reason': reason, 'status': task.status})
            if checked and expected.recheck_requests:
                pending = set(self._pending_rechecks(session, task.id, attempt.id))
                # The candidate contains only requests seen before this probe.
                # A request arriving during inspection waits for a fresh probe.
                for request_id in expected.recheck_requests:
                    if request_id in pending:
                        TaskRepository._publish_event(session, task.id, 'recovery_checked', {
                            'attempt_id': attempt.id, 'request_id': request_id,
                            'version': attempt.version, 'last_checked_at': attempt.last_checked_at.isoformat(),
                            'recovery_state': attempt.recovery_state, 'recovery_reason': attempt.recovery_reason,
                        })
            return True

    def get(self, task_id: UUID) -> dict:
        with self.sessions() as session:
            task = session.get(DailyTaskModel, str(task_id))
            if task is None:
                raise LookupError(f'unknown task: {task_id}')
            attempt = session.get(TaskAttemptModel, task.active_attempt_id) if task.active_attempt_id else None
            recovery = attempt.recovery_state if attempt else 'none'
            actions = []
            if task.status == 'running' and recovery in ('blocked', 'observing'):
                actions.append('recheck')
                if recovery == 'observing' and attempt and all((attempt.pid, attempt.process_created_at,
                        attempt.process_executable, attempt.command_sha256)):
                    actions.append('stop')
            reason = attempt.recovery_reason if attempt else None
            checked = attempt.last_checked_at.isoformat() if attempt and attempt.last_checked_at else None
            version = attempt.version if attempt else 0
            return {'task_id': task.id, 'status': task.status,
                'attempt_id': attempt.id if attempt else None,
                'recovery_state': recovery,
                'recovery_reason': reason, 'last_checked_at': checked, 'version': version,
                'recovery': {'state': recovery, 'reason': reason, 'last_checked_at': checked, 'version': version},
                'allowed_actions': actions}

    def request_action(self, task_id: UUID, expected_attempt_id: UUID, request_id: UUID, action: str) -> dict:
        if action not in ('recheck', 'stop'):
            raise ValueError('unknown recovery action')
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            task = session.get(DailyTaskModel, str(task_id))
            if task is None:
                raise LookupError(f'unknown task: {task_id}')
            if task.active_attempt_id != str(expected_attempt_id):
                raise TaskStateError('active attempt has changed')
            request = session.scalar(select(TaskEventModel).where(TaskEventModel.task_id == task.id,
                TaskEventModel.kind == 'recovery_requested',
                TaskEventModel.payload_json.contains(str(request_id))))
            if request:
                payload = json.loads(request.payload_json)
                if payload.get('action') != action or payload.get('attempt_id') != str(expected_attempt_id):
                    raise TaskStateError('request_id is already used for a different recovery action')
                return {'accepted': True, 'request_id': str(request_id)}
            if task.status != 'running':
                raise TaskStateError('recovery requires a running task')
            payload = {'attempt_id': task.active_attempt_id, 'request_id': str(request_id), 'action': action}
            TaskRepository._publish_event(session, task.id, 'recovery_requested', payload)
            if action == 'stop':
                TaskRepository._publish_event(session, task.id, 'cancel_requested', payload)
        return {'accepted': True, 'request_id': str(request_id)}

    def stop_requested(self, attempt: RecoveryAttempt) -> bool:
        return TaskRepository(self.sessions).cancellation_requested(UUID(attempt.task_id), UUID(attempt.attempt_id))
