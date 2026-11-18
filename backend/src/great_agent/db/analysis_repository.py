"""Analysis never changes the solver attempt state or grants execution rights."""
from __future__ import annotations

from dataclasses import dataclass
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text

from great_agent.db.analysis_models import AnalysisJobModel
from great_agent.db.models import DailyTaskModel, TaskAttemptModel, utc_now
from great_agent.db.repositories import TaskRepository
from great_agent.errors import TaskStateError


@dataclass(frozen=True)
class ClaimedAnalysis:
    id: UUID
    task_id: UUID
    attempt_id: UUID
    revision_id: UUID
    owner_session_id: str
    run_count: int
    config: dict


class AnalysisJobRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def enqueue_in_session(session, task, attempt, config):
        existing = session.scalar(select(AnalysisJobModel).where(AnalysisJobModel.attempt_id == attempt.id))
        if existing:
            return existing.id
        row = AnalysisJobModel(id=str(uuid4()), task_id=task.id, attempt_id=attempt.id,
            revision_id=task.revision_id, config_json=json.dumps(config, sort_keys=True), status='pending')
        session.add(row)
        attempt.analysis_status = 'not_started'
        session.flush()
        TaskRepository._publish_event(session, task.id, 'artifacts_changed', {
            'attempt_id': attempt.id, 'analysis_job_id': row.id, 'analysis_job_status': 'pending',
        })
        return row.id

    def claim(self, owner_session_id):
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.scalar(select(AnalysisJobModel).where(AnalysisJobModel.status == 'pending')
                                 .order_by(AnalysisJobModel.created_at, AnalysisJobModel.id).limit(1))
            if row is None:
                return None
            row.status, row.owner_session_id = 'running', owner_session_id
            row.run_count += 1
            row.updated_at = utc_now()
            row.error_message = None
            TaskRepository._publish_event(session, row.task_id, 'analysis_started', {
                'attempt_id': row.attempt_id, 'analysis_job_id': row.id,
            })
            return ClaimedAnalysis(UUID(row.id), UUID(row.task_id), UUID(row.attempt_id),
                UUID(row.revision_id), owner_session_id, row.run_count, json.loads(row.config_json))

    def finish(self, job, *, status, error_message=None, files=()):
        if status not in ('completed', 'failed'):
            raise ValueError('invalid analysis terminal state')
        from great_agent.db.monitor_repository import register_result_files
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(AnalysisJobModel, str(job.id))
            if (row is None or row.owner_session_id != job.owner_session_id
                    or row.run_count != job.run_count):
                raise TaskStateError('analysis ownership changed')
            if row.status == status:
                return  # A commit may have succeeded before the connection failed.
            if row.status != 'running':
                raise TaskStateError('analysis job is already closed')
            row.status, row.error_message, row.updated_at = status, error_message, utc_now()
            if status == 'completed':
                register_result_files(TaskRepository(self.sessions), job.task_id, job.attempt_id,
                    files, analysis_payload={'analysis_job_id': str(job.id), **job.config}, session=session)
            else:
                TaskRepository._publish_event(session, row.task_id, 'analysis_failed', {
                    'attempt_id': row.attempt_id, 'analysis_job_id': row.id,
                    'message': error_message or 'analysis failed',
                })

    def recover(self, owner_session_id):
        """Called only by the single Solver Worker after acquiring WorkerLock."""
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            rows = list(session.scalars(select(AnalysisJobModel).where(
                AnalysisJobModel.status == 'running', AnalysisJobModel.owner_session_id != owner_session_id)))
            for row in rows:
                row.status, row.owner_session_id, row.updated_at = 'pending', None, utc_now()
                attempt = session.get(TaskAttemptModel, row.attempt_id)
                attempt.analysis_status = 'not_started'
                TaskRepository._publish_event(session, row.task_id, 'artifacts_changed', {
                    'attempt_id': row.attempt_id, 'analysis_job_id': row.id, 'analysis_job_status': 'pending',
                    'reason': 'analysis_worker_restarted',
                })
            return len(rows)

    @staticmethod
    def _serialize(row):
        return {'id': row.id, 'task_id': row.task_id, 'attempt_id': row.attempt_id,
            'revision_id': row.revision_id, 'status': row.status, 'run_count': row.run_count,
            'config': json.loads(row.config_json), 'error_message': row.error_message}

    def list_for_task(self, task_id):
        with self.sessions() as session:
            if session.get(DailyTaskModel, str(task_id)) is None:
                raise LookupError('unknown task')
            return [self._serialize(row) for row in session.scalars(select(AnalysisJobModel).where(
                AnalysisJobModel.task_id == str(task_id)).order_by(AnalysisJobModel.created_at))]

    def retry(self, task_id, job_id):
        with self.sessions.begin() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(AnalysisJobModel, str(job_id))
            if row is None or row.task_id != str(task_id):
                raise LookupError('unknown analysis job')
            if row.status in ('pending', 'running'):
                return self._serialize(row)
            if row.status != 'failed':
                raise TaskStateError('only failed analysis jobs can be retried')
            task, attempt = session.get(DailyTaskModel, row.task_id), session.get(TaskAttemptModel, row.attempt_id)
            if task.active_attempt_id != row.attempt_id or attempt.status != 'succeeded':
                raise TaskStateError('analysis target is no longer the successful active attempt')
            row.status, row.owner_session_id, row.error_message, row.updated_at = 'pending', None, None, utc_now()
            attempt.analysis_status = 'not_started'
            TaskRepository._publish_event(session, row.task_id, 'artifacts_changed', {
                'attempt_id': row.attempt_id, 'analysis_job_id': row.id, 'analysis_job_status': 'pending',
            })
            return self._serialize(row)
