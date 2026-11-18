from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base, DailyTaskModel, ExperimentModel, PlanRevisionModel, TaskAttemptModel
from test_execution_preview import api_context, valid_spec, preview_context, prepare


@pytest.fixture
def monitor_context(tmp_path: Path):
    from great_agent.db.monitor_repository import MonitorRepository

    engine, sessions = create_engine_and_session_factory(tmp_path / 'monitor.db')
    Base.metadata.create_all(engine)
    repository = MonitorRepository(sessions)
    yield engine, sessions, repository
    engine.dispose()


def seed(sessions, *, status='pending', attempt=False):
    experiment_id, revision_id, task_id = (str(uuid4()) for _ in range(3))
    with sessions.begin() as session:
        session.add(ExperimentModel(id=experiment_id, status='ready'))
        session.flush()
        session.add(PlanRevisionModel(id=revision_id, experiment_id=experiment_id,
            number=1, source='initial', spec_json='{"concurrency":100}', is_frozen=True))
        session.flush()
        task = DailyTaskModel(id=task_id, experiment_id=experiment_id, revision_id=revision_id,
            year=2024, doy=1, status=status)
        session.add(task)
        session.flush()
        if attempt:
            row = TaskAttemptModel(id=str(uuid4()), task_id=task_id, number=1, status=status)
            session.add(row)
            session.flush()
            task.active_attempt_id = row.id
    return experiment_id, task_id


def test_state_and_monitor_event_roll_back_together(monitor_context):
    from great_agent.db.monitor_models import ExperimentEventModel
    from great_agent.db.monitor_repository import append_monitor_event

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions)
    before = monitor.snapshot(experiment_id)
    with pytest.raises(RuntimeError), sessions.begin() as session:
        session.get(DailyTaskModel, task_id).status = 'cancelled'
        append_monitor_event(session, task_id, 'task_status', {'status': 'cancelled'})
        raise RuntimeError('rollback')
    assert monitor.snapshot(experiment_id) == before
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExperimentEventModel)) == 0


def test_cursor_scope_gaps_and_epoch_rotation(monitor_context):
    from great_agent.db.monitor_repository import CursorError, CursorResetRequired, append_monitor_event

    _, sessions, monitor = monitor_context
    first, a = seed(sessions)
    second, b = seed(sessions)
    cursor = monitor.snapshot(first)['cursor']
    for task_id in (a, b, a):
        with sessions.begin() as session:
            append_monitor_event(session, task_id, 'task_status', {'status': 'pending'})
    events = monitor.events(first, cursor)
    assert [item['event_id'].rsplit(':', 1)[1] for item in events] == ['1', '3']
    assert all(item['experiment_id'] == first for item in events)
    with pytest.raises(CursorError):
        monitor.events(second, cursor)
    with pytest.raises(CursorError):
        monitor.events(first, cursor.rsplit(':', 1)[0] + ':01')
    with pytest.raises(CursorResetRequired):
        monitor.events(first, cursor.rsplit(':', 1)[0] + ':999')
    monitor.rotate_epoch()
    with pytest.raises(CursorResetRequired):
        monitor.events(first, cursor)


def test_snapshot_projects_historical_metadata_and_queued_retry(monitor_context):
    from great_agent.db.monitor_repository import append_monitor_event

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions, status='failed', attempt=True)
    task = monitor.snapshot(experiment_id)['tasks'][0]
    assert task['analysis_status'] == 'unknown'
    assert task['artifact_version'] == 0
    assert len(task['attempts']) == 1
    with sessions.begin() as session:
        session.get(DailyTaskModel, task_id).status = 'pending'
        row = append_monitor_event(session, task_id, 'attempt_queued', {'next_attempt_number': 2})
        assert row.attempt_id is None
        payload = json.loads(row.payload_json)
        assert payload['task']['status'] == 'pending'
        assert payload['next_attempt_number'] == 2
    snapshot = monitor.snapshot(experiment_id)
    assert snapshot['tasks'][0]['next_attempt_number'] == 2


def test_snapshot_remains_consistent_when_writer_commits_after_high_water(monitor_context):
    from great_agent.db.monitor_repository import append_monitor_event

    engine, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions)
    wrote = False

    def after_query(conn, cursor, statement, parameters, context, executemany):
        nonlocal wrote
        if 'max(experiment_events.id)' in statement.lower() and not wrote:
            wrote = True
            with sessions.begin() as writer:
                writer.get(DailyTaskModel, task_id).status = 'cancelled'
                append_monitor_event(writer, task_id, 'task_status', {'status': 'cancelled'})

    event.listen(engine, 'after_cursor_execute', after_query)
    try:
        snapshot = monitor.snapshot(experiment_id)
    finally:
        event.remove(engine, 'after_cursor_execute', after_query)
    assert wrote
    assert snapshot['tasks'][0]['status'] == 'pending'
    replay = monitor.events(experiment_id, snapshot['cursor'])
    assert len(replay) == 1 and replay[0]['data']['task']['status'] == 'cancelled'


def test_analysis_registration_and_status_event_are_atomic(monitor_context, monkeypatch):
    from great_agent.db.monitor_repository import register_result_files
    from great_agent.db.repositories import TaskRepository
    from great_agent.runtime.result_inspector import ResultFileInfo

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions, status='succeeded', attempt=True)
    repository = TaskRepository(sessions)
    attempt_id = monitor.snapshot(experiment_id)['tasks'][0]['active_attempt_id']
    files = (ResultFileInfo(None, 'plot', Path('plot.png'), 123),)
    original = repository._publish_event
    def reject_completed(session, task_id, kind, payload):
        original(session, task_id, kind, payload)
        if kind == 'analysis_completed':
            raise RuntimeError('event persistence failed')
    monkeypatch.setattr(repository, '_publish_event', reject_completed)
    with pytest.raises(RuntimeError):
        register_result_files(repository, UUID(task_id), UUID(attempt_id), files,
            analysis_payload={'artifact_count': 1})
    assert repository.list_attempt_results(UUID(task_id), UUID(attempt_id)) == ()
    assert monitor.snapshot(experiment_id)['tasks'][0]['analysis_status'] == 'unknown'


def test_snapshot_reads_bounded_queries_for_one_hundred_tasks(monitor_context):
    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions)
    with sessions.begin() as session:
        template = session.get(DailyTaskModel, task_id)
        session.add_all(DailyTaskModel(id=str(uuid4()), experiment_id=experiment_id,
            revision_id=template.revision_id, year=2024, doy=day, status='pending') for day in range(2, 101))
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(monitor_context[0], 'before_cursor_execute', record)
    try:
        snapshot = monitor.snapshot(experiment_id)
    finally:
        event.remove(monitor_context[0], 'before_cursor_execute', record)
    assert len(snapshot['tasks']) == 100
    assert len(statements) <= 8
    assert statements[0].strip().upper() == 'BEGIN'


def test_legacy_events_project_analysis_and_artifacts_in_same_transaction(monitor_context):
    from great_agent.db.repositories import TaskRepository

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions, status='succeeded', attempt=True)
    repository = TaskRepository(sessions)
    initial = monitor.snapshot(experiment_id)
    repository.publish_event(UUID(task_id), 'analysis_started', {})
    repository.publish_event(UUID(task_id), 'analysis_skipped', {'reason': 'reference unavailable'})
    latest = monitor.snapshot(experiment_id)['tasks'][0]
    assert latest['analysis_status'] == 'skipped'
    assert latest['artifact_version'] == 2
    replay = monitor.events(experiment_id, initial['cursor'])
    assert [item['type'] for item in replay] == ['analysis.started', 'analysis.skipped']
    assert replay[-1]['data']['task'] == latest


def test_pending_events_use_chat_caller_transaction_and_confirm_idempotence(preview_context):
    from great_agent.db.monitor_repository import MonitorRepository
    client, services, sessions, experiment = preview_context
    preview = prepare(preview_context).json()
    monitor = MonitorRepository(sessions)
    before = monitor.snapshot(experiment.id)
    with pytest.raises(RuntimeError), sessions.begin() as session:
        services.task_service.execution_service.confirm(experiment.id,
            preview_id=UUID(preview['id']), expected_revision=1, confirmed=True, session=session)
        raise RuntimeError('chat receipt failed')
    assert monitor.snapshot(experiment.id) == before
    body = {'preview_id': preview['id'], 'expected_revision': 1, 'confirmed': True}
    assert client.post(f'/api/v1/experiments/{experiment.id}/run', json=body).status_code == 200
    assert client.post(f'/api/v1/experiments/{experiment.id}/run', json=body).status_code == 200
    replay = monitor.events(experiment.id, before['cursor'])
    assert len(replay) == 1
    assert replay[0]['type'] == 'task.created' and replay[0]['attempt_id'] is None


def test_monitor_api_snapshot_sse_once_and_cursor_errors(preview_context):
    client, services, sessions, experiment = preview_context
    snapshot = client.get(f'/api/v1/experiments/{experiment.id}/monitor')
    assert snapshot.status_code == 200
    cursor = snapshot.json()['cursor']
    preview = prepare(preview_context).json()
    client.post(f'/api/v1/experiments/{experiment.id}/run', json={
        'preview_id': preview['id'], 'expected_revision': 1, 'confirmed': True,
    })
    endpoint = f'/api/v1/experiments/{experiment.id}/events?once=true'
    response = client.get(endpoint, headers={'Last-Event-ID': cursor})
    assert response.status_code == 200
    assert 'event: task.created\n' in response.text
    data = json.loads(next(line[6:] for line in response.text.splitlines() if line.startswith('data: ')))
    assert data['schema_version'] == 1 and data['data']['task']['status'] == 'pending'
    assert client.get(endpoint).status_code == 400
    invalid = client.get(endpoint, headers={'Last-Event-ID': 'broken'})
    assert invalid.status_code == 400 and invalid.json()['error']['code'] == 'invalid_cursor'
    future = client.get(endpoint, headers={'Last-Event-ID': cursor.rsplit(':', 1)[0] + ':9999'})
    assert future.status_code == 409 and future.json()['error']['code'] == 'cursor_reset_required'
    assert client.get(f'/api/v1/experiments/{uuid4()}/monitor').status_code == 404


def test_replay_bounded_and_retention_floor_requires_snapshot(monitor_context):
    from great_agent.db.monitor_models import ExperimentEventModel, MonitorStateModel
    from great_agent.db.monitor_repository import CursorResetRequired

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions)
    cursor = monitor.snapshot(experiment_id)['cursor']
    with sessions.begin() as session:
        session.add_all(ExperimentEventModel(experiment_id=experiment_id, task_id=task_id,
            kind='future.known.version', payload_json='{}') for _ in range(205))
    first = monitor.events(experiment_id, cursor, limit=10000)
    assert len(first) == 200
    assert len(monitor.events(experiment_id, first[-1]['event_id'])) == 5
    with sessions.begin() as session:
        session.get(MonitorStateModel, 1).retention_floor_json = json.dumps({experiment_id: 1})
    with pytest.raises(CursorResetRequired):
        monitor.events(experiment_id, cursor)


def test_blocked_recovery_has_recheck_but_no_stop_action(monitor_context):
    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions, status='running', attempt=True)
    with sessions.begin() as session:
        task = session.get(DailyTaskModel, task_id)
        session.get(TaskAttemptModel, task.active_attempt_id).recovery_state = 'blocked'
    recovery = monitor.snapshot(experiment_id)['tasks'][0]['recovery']
    assert recovery['allowed_actions'] == ['recheck']


def test_result_registration_reserves_writer_before_reading(monitor_context):
    from great_agent.db.monitor_repository import register_result_files
    from great_agent.db.repositories import TaskRepository

    engine, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions, status='succeeded', attempt=True)
    attempt_id = monitor.snapshot(experiment_id)['tasks'][0]['active_attempt_id']
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', record)
    try:
        register_result_files(TaskRepository(sessions), UUID(task_id), UUID(attempt_id), ())
    finally:
        event.remove(engine, 'before_cursor_execute', record)
    assert statements[0].strip().upper() == 'BEGIN IMMEDIATE'


def test_claim_emits_attempt_started_with_actual_attempt_and_new_metadata(monitor_context):
    from great_agent.db.repositories import TaskRepository

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions)
    cursor = monitor.snapshot(experiment_id)['cursor']
    claim = TaskRepository(sessions).claim_next('test-worker')
    assert claim is not None
    replay = monitor.events(experiment_id, cursor)
    assert len(replay) == 1
    assert replay[0]['type'] == 'attempt.started'
    assert replay[0]['attempt_id'] == str(claim.attempt_id)
    assert replay[0]['data']['task']['analysis_status'] == 'not_started'


def test_successful_analysis_registration_projects_same_metadata_into_event(monitor_context):
    from great_agent.db.monitor_repository import register_result_files
    from great_agent.db.repositories import TaskRepository
    from great_agent.runtime.result_inspector import ResultFileInfo

    _, sessions, monitor = monitor_context
    experiment_id, task_id = seed(sessions, status='succeeded', attempt=True)
    snapshot = monitor.snapshot(experiment_id)
    attempt_id = snapshot['tasks'][0]['active_attempt_id']
    repository = TaskRepository(sessions)
    register_result_files(repository, UUID(task_id), UUID(attempt_id),
        (ResultFileInfo(None, 'plot', Path('enu.png'), 123),), analysis_payload={'artifact_count': 1})
    latest = monitor.snapshot(experiment_id)['tasks'][0]
    replay = monitor.events(experiment_id, snapshot['cursor'])
    assert [item['type'] for item in replay] == ['artifacts.changed', 'analysis.completed']
    assert latest['analysis_status'] == 'completed' and latest['artifact_version'] == 2
    assert replay[-1]['data']['task'] == latest
    assert len(repository.list_attempt_results(UUID(task_id), UUID(attempt_id))) == 1
