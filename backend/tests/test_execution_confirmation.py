from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select

from test_execution_preview import api_context, valid_spec, preview_context, prepare
from great_agent.db.models import DailyTaskModel, ExecutionPreviewModel, utc_now


def confirm(context, preview, **values):
    client, _, _, experiment = context
    return client.post(f'/api/v1/experiments/{experiment.id}/run', json={
        'preview_id': preview['id'], 'expected_revision': 1, 'confirmed': True, **values,
    })


def test_legacy_run_cannot_queue_without_confirmation(preview_context):
    client, services, _, experiment = preview_context
    services.plan_service.freeze_revision(experiment.id, expected_revision=1)
    response = client.post(f'/api/v1/experiments/{experiment.id}/run')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'execution_confirmation_required'
    assert not services.task_repository.list_tasks(experiment.id)


@pytest.mark.parametrize('confirmed', [False, 'true', 1])
def test_confirmation_requires_strict_true(preview_context, confirmed):
    preview = prepare(preview_context).json()
    response = confirm(preview_context, preview, confirmed=confirmed)
    assert response.status_code in (409, 422)
    assert not preview_context[1].task_repository.list_tasks(preview_context[3].id)


def test_confirm_is_idempotent_and_queues_only_once(preview_context):
    preview = prepare(preview_context).json()
    first = confirm(preview_context, preview)
    assert first.status_code == 200, first.text
    second = confirm(preview_context, preview)
    assert second.json() == first.json()
    assert first.json()['count'] == 1
    assert len(preview_context[1].task_repository.list_tasks(preview_context[3].id)) == 1


def test_two_previews_cannot_authorize_duplicate_tasks(preview_context):
    a, b = prepare(preview_context).json(), prepare(preview_context).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda p: confirm(preview_context, p), [a, b]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(preview_context[1].task_repository.list_tasks(preview_context[3].id)) == 1


@pytest.mark.parametrize('target', ['snapshot', 'executable', 'template', 'input'])
def test_changed_files_invalidate_confirmation(preview_context, target):
    preview = prepare(preview_context).json()
    job = preview['jobs'][0]
    path = {'snapshot': job['snapshot_path'], 'executable': preview['executable_path'],
            'template': preview['template_path'], 'input': job['inputs'][2]['path']}[target]
    Path(path).write_bytes(b'changed after review')
    response = confirm(preview_context, preview)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'execution_review_invalid'
    assert not preview_context[1].task_repository.list_tasks(preview_context[3].id)


def test_expired_preview_is_rejected(preview_context):
    preview = prepare(preview_context).json()
    with preview_context[2].begin() as session:
        row = session.get(ExecutionPreviewModel, preview['id'])
        from great_agent.domain.execution import ExecutionPreview
        payload = ExecutionPreview.model_validate_json(row.payload_json)
        row.payload_json = payload.model_copy(update={'expires_at': utc_now() - timedelta(seconds=1)}).model_dump_json()
    assert confirm(preview_context, preview).status_code == 409


def test_cross_experiment_preview_cannot_confirm(preview_context, valid_spec):
    preview = prepare(preview_context).json()
    client, services, sessions, _ = preview_context
    other = services.plan_service.create_experiment(valid_spec)
    response = confirm((client, services, sessions, other), preview)
    assert response.status_code == 409


async def test_worker_rejects_unapproved_legacy_pending_task(preview_context, monkeypatch):
    _, services, _, experiment = preview_context
    services.plan_service.freeze_revision(experiment.id, expected_revision=1)
    task = services.task_service.materialize(experiment.id)[0]
    async def forbidden(*args, **kwargs):
        pytest.fail('unapproved worker launched a process')
    monkeypatch.setattr(services.worker.process_runner, 'run', forbidden)
    await services.worker.run_once()
    stored = services.task_repository.get_task(task.id)
    assert stored.status == 'failed'
    assert stored.active_attempt.error_code == 'execution_confirmation_required'


async def test_worker_rechecks_files_after_confirmation(preview_context, monkeypatch):
    preview = prepare(preview_context).json()
    result = confirm(preview_context, preview)
    assert result.status_code == 200
    Path(preview['jobs'][0]['snapshot_path']).write_bytes(b'changed in queue')
    _, services, _, _ = preview_context
    async def forbidden(*args, **kwargs):
        pytest.fail('modified approved XML launched a process')
    monkeypatch.setattr(services.worker.process_runner, 'run', forbidden)
    await services.worker.run_once()
    task = services.task_repository.get_task(UUID(result.json()['tasks'][0]))
    assert task.active_attempt.error_code == 'execution_review_invalid'


def test_legacy_retry_cannot_bypass_review(preview_context):
    preview = prepare(preview_context).json()
    result = confirm(preview_context, preview)
    assert result.status_code == 200
    task_id = result.json()['tasks'][0]
    with preview_context[2].begin() as session:
        session.get(DailyTaskModel, task_id).status = 'failed'
    response = preview_context[0].post(f'/api/v1/tasks/{task_id}/retry')
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'execution_confirmation_required'


async def test_worker_uses_reviewed_xml_and_command_without_rendering(preview_context, monkeypatch):
    from great_agent.runtime.process_runner import ProcessOutcome
    preview = prepare(preview_context).json()
    result = confirm(preview_context, preview)
    assert result.status_code == 200
    _, services, _, _ = preview_context
    def forbidden(*args, **kwargs):
        pytest.fail('worker re-rendered the reviewed XML')
    async def inspect(task_id, command, *, cwd, log_path, on_started):
        assert tuple(command) == tuple(preview['jobs'][0]['command'])
        assert Path(command[2]).read_bytes() == preview['jobs'][0]['xml_content'].encode('utf-8')
        assert str(cwd) == preview['jobs'][0]['cwd']
        return ProcessOutcome(pid=1234, exit_code=7, cancelled=False)
    monkeypatch.setattr(services.task_service.adapter, 'render_task', forbidden)
    monkeypatch.setattr(services.worker.process_runner, 'run', inspect)
    await services.worker.run_once()
    assert services.task_repository.get_task(UUID(result.json()['tasks'][0])).active_attempt.exit_code == 7


@pytest.mark.parametrize('state', ['running', 'succeeded', 'pending', 'cancelled'])
def test_retry_preview_rejects_ineligible_states(preview_context, state):
    preview = prepare(preview_context).json()
    task_id = confirm(preview_context, preview).json()['tasks'][0]
    with preview_context[2].begin() as session:
        session.get(DailyTaskModel, task_id).status = state
    assert prepare(preview_context, task_id=task_id).status_code == 409


def test_preview_cannot_retry_a_task_from_another_experiment(preview_context, valid_spec):
    preview = prepare(preview_context).json()
    task_id = confirm(preview_context, preview).json()['tasks'][0]
    client, services, sessions, _ = preview_context
    with sessions.begin() as session:
        session.get(DailyTaskModel, task_id).status = 'failed'
    other = services.plan_service.create_experiment(valid_spec)
    assert prepare((client, services, sessions, other), task_id=task_id).status_code == 409


async def test_stopping_experiment_cancels_pending_jobs_without_launching(preview_context):
    preview = prepare(preview_context).json()
    task_id = confirm(preview_context, preview).json()['tasks'][0]
    client, services, _, experiment = preview_context
    assert client.post(f'/api/v1/experiments/{experiment.id}/stop').status_code == 200
    assert services.task_repository.get_task(UUID(task_id)).status == 'cancelled'
    assert await services.worker.run_once() is False


def test_confirmed_authorization_does_not_expire_while_queued(preview_context):
    from great_agent.domain.execution import ExecutionPreview
    preview = prepare(preview_context).json()
    task_id = confirm(preview_context, preview).json()['tasks'][0]
    with preview_context[2].begin() as session:
        row = session.get(ExecutionPreviewModel, preview['id'])
        payload = ExecutionPreview.model_validate_json(row.payload_json)
        row.payload_json = payload.model_copy(update={'expires_at': utc_now() - timedelta(hours=1)}).model_dump_json()
    job = preview_context[1].task_service.execution_service.approved_job(UUID(task_id), 1)
    assert job.xml_sha256 == preview['jobs'][0]['xml_sha256']
