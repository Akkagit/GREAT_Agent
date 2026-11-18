"""Read-only diagnostics; API liveness does not imply a ready solver."""
from datetime import timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from great_agent.db.analysis_models import AnalysisJobModel, WorkerHeartbeatModel
from great_agent.db.models import TaskAttemptModel, utc_now
from great_agent.db.schema import require_runtime_schema

router = APIRouter(prefix='/api/v1', tags=['diagnostics'])
HEARTBEAT_MAX_AGE_SECONDS = 10


def diagnostics(services):
    result = {'schema': {'ready': False}, 'worker': {'ready': False, 'reason': 'worker_not_started'},
        'analysis_queue': {}, 'knowledge': {'configured': False}}
    if services is None:
        result['schema']['reason'] = 'services_not_configured'
        return result
    sessions = services.task_repository._session_factory
    try:
        require_runtime_schema(sessions.kw['bind'])
        result['schema'] = {'ready': True, 'revision': '0009'}
        with sessions() as session:
            row = session.get(WorkerHeartbeatModel, 'solver')
            if row is not None:
                timestamp = row.checked_at.replace(tzinfo=timezone.utc) if row.checked_at.tzinfo is None else row.checked_at
                age = (utc_now() - timestamp).total_seconds()
                ready = row.status == 'running' and 0 <= age <= HEARTBEAT_MAX_AGE_SECONDS
                reason = 'ready' if ready else ('worker_stopped' if row.status != 'running' else 'worker_heartbeat_stale')
                result['worker'] = {'ready': ready, 'reason': reason, 'pid': row.pid,
                    'heartbeat_age_seconds': round(age, 3), 'owner_session_id': row.owner_session_id}
            result['worker']['blocked_recoveries'] = session.scalar(select(func.count()).select_from(TaskAttemptModel).where(
                TaskAttemptModel.status == 'running', TaskAttemptModel.recovery_state.in_(('blocked', 'observing'))))
            result['analysis_queue'] = dict(session.execute(select(AnalysisJobModel.status, func.count())
                .group_by(AnalysisJobModel.status)).all())
    except Exception:
        # Diagnostics must remain available for old/missing/corrupt schemas.
        # Driver errors may contain paths or statements; return only a stable code.
        result['schema'] = {'ready': False, 'reason': 'runtime_schema_unavailable'}
        return result
    knowledge = getattr(services, 'knowledge_service', None)
    if knowledge is not None:
        task = getattr(knowledge, '_task', None)
        result['knowledge'] = {'configured': True, 'executor_running': task is not None and not task.done()}
    return result


@router.get('/readiness')
def readiness(request: Request, require_worker: bool = True):
    result = diagnostics(getattr(request.app.state, 'services', None))
    result['ready'] = result['schema']['ready'] and (not require_worker or result['worker']['ready'])
    result['require_worker'] = require_worker
    return JSONResponse(result, status_code=200 if result['ready'] else 503)


@router.get('/diagnostics')
def diagnostic_details(request: Request):
    return diagnostics(getattr(request.app.state, 'services', None))
