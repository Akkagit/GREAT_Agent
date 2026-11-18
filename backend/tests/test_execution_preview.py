from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select

from test_api import api_context, valid_spec  # shared isolated API fixtures
from great_agent.db.models import DailyTaskModel
from great_agent.domain.dates import DateSegment, expand_dates


def ready_inputs(services, spec):
    for date in expand_dates(spec):
        for item in services.task_service.adapter.preflight_task(spec, date, Path('unused')).missing:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_bytes(b'input')


@pytest.fixture
def preview_context(api_context, valid_spec):
    client, services, sessions = api_context
    experiment = services.plan_service.create_experiment(valid_spec)
    ready_inputs(services, valid_spec)
    return client, services, sessions, experiment


def prepare(context, **body):
    client, _, _, experiment = context
    return client.post(f'/api/v1/experiments/{experiment.id}/execution-preview', json={'expected_revision': 1, **body})


async def test_preview_generates_reviewable_files_without_queueing(preview_context):
    client, services, sessions, experiment = preview_context
    response = prepare(preview_context)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['experiment_id'] == str(experiment.id)
    assert data['revision'] == 1
    assert data['intent'] == 'initial'
    assert len(data['executable_sha256']) == 64
    assert len(data['jobs']) == 1
    job = data['jobs'][0]
    assert (job['year'], job['doy'], job['attempt_number']) == (2024, 350, 1)
    assert job['command'] == [data['executable_path'], '-x', job['xml_path']]
    assert job['output_dir'] in job['xml_content']
    assert Path(job['snapshot_path']).read_text(encoding='utf-8') == job['xml_content']
    assert Path(job['xml_path']).name == 'config.xml'
    assert not Path(job['xml_path']).exists()
    assert len(job['inputs']) == 25
    assert all(item['state'] == 'present' for item in job['inputs'])
    assert services.plan_service.get_current_revision(experiment.id).is_frozen
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0
    assert await services.worker.run_once() is False


def test_two_previews_are_separate_and_do_not_overwrite_xml(preview_context):
    first = prepare(preview_context)
    assert first.status_code == 200
    second = prepare(preview_context)
    assert second.status_code == 200
    a, b = first.json(), second.json()
    assert a['id'] != b['id']
    assert a['jobs'][0]['snapshot_path'] != b['jobs'][0]['snapshot_path']
    assert Path(a['jobs'][0]['snapshot_path']).read_text(encoding='utf-8') == a['jobs'][0]['xml_content']


def test_preview_rejects_stale_revision(preview_context):
    response = prepare(preview_context, expected_revision=9)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'revision_conflict'


def test_preview_reports_missing_inputs_without_freezing(preview_context):
    _, services, _, experiment = preview_context
    spec = services.plan_service.get_current_spec(experiment.id)
    item = services.task_service.adapter.preflight_task(spec, expand_dates(spec)[0], Path('unused')).items[2]
    item.path.unlink()
    response = prepare(preview_context)
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'preflight_error'
    assert not services.plan_service.get_current_revision(experiment.id).is_frozen


def test_preview_deduplicates_cross_year_dates(api_context, valid_spec):
    client, services, sessions = api_context
    spec = valid_spec.model_copy(update={'date_segments': (
        DateSegment(year=2024, start_doy=366, end_doy=366),
        DateSegment(year=2025, start_doy=1, end_doy=2),
        DateSegment(year=2025, start_doy=1, end_doy=1),
    )})
    experiment = services.plan_service.create_experiment(spec)
    ready_inputs(services, spec)
    response = prepare((client, services, sessions, experiment))
    assert response.status_code == 200
    assert [(j['year'], j['doy']) for j in response.json()['jobs']] == [(2024, 366), (2025, 1), (2025, 2)]


def test_preview_is_persisted_in_shared_database(preview_context):
    from great_agent.services.execution_service import ExecutionService
    response = prepare(preview_context)
    assert response.status_code == 200
    _, services, _, _ = preview_context
    reloaded = ExecutionService(services.task_service).get(UUID(response.json()['id']))
    assert reloaded.model_dump(mode='json') == response.json()


def test_preview_can_be_reopened_after_browser_refresh(preview_context):
    response = prepare(preview_context)
    assert response.status_code == 200
    client, _, _, experiment = preview_context
    reopened = client.get(f'/api/v1/experiments/{experiment.id}/execution-previews/{response.json()["id"]}')
    assert reopened.status_code == 200
    assert reopened.json() == response.json()


def test_history_lists_real_saved_experiments_without_queueing(preview_context):
    client, services, _, experiment = preview_context
    response = client.get('/api/v1/experiments')
    assert response.status_code == 200
    record = next(row for row in response.json() if row['id'] == str(experiment.id))
    assert record['spec']['date_segments'] == [{'year': 2024, 'start_doy': 350, 'end_doy': 350}]
    assert record['task_count'] == 0
    assert not services.task_repository.list_tasks(experiment.id)


def test_output_order_is_stable_across_plan_json_round_trips(valid_spec):
    from great_agent.domain.plans import ExperimentSpec
    assert valid_spec.model_dump(mode='json')['outputs'] == ['flt', 'log']
    restored = ExperimentSpec.model_validate_json(valid_spec.model_dump_json())
    assert restored.model_dump(mode='json')['outputs'] == ['flt', 'log']
