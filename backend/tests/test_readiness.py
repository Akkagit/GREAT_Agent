from datetime import timedelta
import os

from sqlalchemy import text

from great_agent.db.analysis_models import WorkerHeartbeatModel
from great_agent.db.models import utc_now
from test_api import api_context, valid_spec  # noqa: F401


def test_liveness_is_separate_from_worker_readiness(api_context):
    client, _, _ = api_context
    assert client.get('/api/v1/health').status_code == 200
    response = client.get('/api/v1/readiness')
    assert response.status_code == 503
    assert response.json()['worker']['reason'] == 'worker_not_started'
    assert client.get('/api/v1/readiness?require_worker=false').status_code == 200


def test_readiness_reports_fresh_stale_and_stopped_heartbeats(api_context):
    client, _, sessions = api_context
    with sessions.begin() as session:
        session.add(WorkerHeartbeatModel(role='solver', owner_session_id='test-owner',
            pid=os.getpid(), status='running', checked_at=utc_now()))
    response = client.get('/api/v1/readiness')
    assert response.status_code == 200
    assert response.json()['worker']['ready'] is True
    with sessions.begin() as session:
        row = session.get(WorkerHeartbeatModel, 'solver')
        row.checked_at = utc_now() - timedelta(seconds=60)
    response = client.get('/api/v1/readiness')
    assert response.status_code == 503
    assert response.json()['worker']['reason'] == 'worker_heartbeat_stale'
    with sessions.begin() as session:
        row = session.get(WorkerHeartbeatModel, 'solver')
        row.status, row.checked_at = 'stopped', utc_now()
    assert client.get('/api/v1/readiness').json()['worker']['reason'] == 'worker_stopped'


def test_readiness_rejects_schema_mismatch_without_hiding_liveness(api_context):
    client, _, sessions = api_context
    with sessions.begin() as session:
        session.execute(text('DROP TABLE analysis_jobs'))
    response = client.get('/api/v1/readiness?require_worker=false')
    assert response.status_code == 503
    assert response.json()['schema']['ready'] is False
    assert client.get('/api/v1/health').status_code == 200
