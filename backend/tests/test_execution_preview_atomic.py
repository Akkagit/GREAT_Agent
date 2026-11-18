"""Preview publication must never freeze a plan before durable review material exists."""

from pathlib import Path
import pytest
from sqlalchemy import event, func, select

from test_execution_preview import api_context, valid_spec, preview_context, ready_inputs
from great_agent.db.models import DailyTaskModel, ExecutionPreviewModel
from great_agent.domain.dates import DateSegment
from great_agent.errors import PendingProposalError, RevisionConflictError, TaskStateError, XmlValidationError


def assert_no_published_preview(services, sessions, experiment_id):
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionPreviewModel)) == 0
    assert not list(services.run_storage.experiment_dir(experiment_id).rglob('config.xml'))


def test_second_day_render_failure_keeps_plan_editable_and_removes_own_xml(api_context, valid_spec, monkeypatch):
    _, services, sessions = api_context
    spec = valid_spec.model_copy(update={'date_segments': (DateSegment(year=2024, start_doy=350, end_doy=351),)})
    experiment = services.plan_service.create_experiment(spec)
    ready_inputs(services, spec)
    adapter = services.task_service.adapter
    render = adapter.render_task

    def fail_second_day(spec, date, task_dir, **kwargs):
        if date.doy == 351:
            raise XmlValidationError('second day rendering failed')
        return render(spec, date, task_dir, **kwargs)

    monkeypatch.setattr(adapter, 'render_task', fail_second_day)
    with pytest.raises(XmlValidationError, match='second day'):
        services.task_service.execution_service.prepare(experiment.id, expected_revision=1)

    assert not services.plan_service.get_current_revision(experiment.id).is_frozen
    assert services.plan_service.get_experiment(experiment.id).status == 'draft'
    assert_no_published_preview(services, sessions, experiment.id)
    changed = services.plan_service.accept_revision(experiment.id, spec.model_copy(update={'concurrency': 2}), expected_revision=1)
    assert changed.number == 2


@pytest.mark.parametrize('change', ['revision', 'pending_proposal'])
def test_plan_change_during_render_rejects_stale_publication(preview_context, monkeypatch, change):
    _, services, sessions, experiment = preview_context
    adapter = services.task_service.adapter
    render = adapter.render_task
    candidate = services.plan_service.get_current_spec(experiment.id).model_copy(update={'concurrency': 2})

    def change_plan(spec, date, task_dir, **kwargs):
        result = render(spec, date, task_dir, **kwargs)
        if change == 'revision':
            services.plan_service.accept_revision(experiment.id, candidate, expected_revision=1)
        else:
            services.plan_service.preview_revision(experiment.id, candidate, source='card')
        return result

    monkeypatch.setattr(adapter, 'render_task', change_plan)
    error = RevisionConflictError if change == 'revision' else PendingProposalError
    with pytest.raises(error):
        services.task_service.execution_service.prepare(experiment.id, expected_revision=1)

    assert not services.plan_service.get_current_revision(experiment.id).is_frozen
    assert_no_published_preview(services, sessions, experiment.id)


def test_preview_insert_failure_rolls_back_freeze_and_removes_own_xml(preview_context):
    _, services, sessions, experiment = preview_context
    engine = sessions.kw['bind']

    def fail_preview_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('INSERT INTO EXECUTION_PREVIEWS'):
            raise OSError('preview persistence failed')

    event.listen(engine, 'before_cursor_execute', fail_preview_insert)
    try:
        with pytest.raises(OSError, match='persistence failed'):
            services.task_service.execution_service.prepare(experiment.id, expected_revision=1)
    finally:
        event.remove(engine, 'before_cursor_execute', fail_preview_insert)

    assert not services.plan_service.get_current_revision(experiment.id).is_frozen
    assert services.plan_service.get_experiment(experiment.id).status == 'draft'
    assert_no_published_preview(services, sessions, experiment.id)


def test_failed_retry_preview_keeps_existing_freeze_and_published_xml(preview_context, monkeypatch):
    _, services, sessions, experiment = preview_context
    execution = services.task_service.execution_service
    initial = execution.prepare(experiment.id, expected_revision=1)
    task = execution.confirm(experiment.id, preview_id=initial.id, expected_revision=1, confirmed=True)[0]
    with sessions.begin() as session:
        session.get(DailyTaskModel, str(task.id)).status = 'failed'
    saved_xml = Path(initial.jobs[0].snapshot_path).read_bytes()
    render = services.task_service.adapter.render_task

    def fail_after_render(*args, **kwargs):
        render(*args, **kwargs)
        raise XmlValidationError('retry rendering failed')

    monkeypatch.setattr(services.task_service.adapter, 'render_task', fail_after_render)
    with pytest.raises(XmlValidationError, match='retry rendering'):
        execution.prepare(experiment.id, expected_revision=1, task_id=task.id)

    assert services.plan_service.get_current_revision(experiment.id).is_frozen
    assert services.plan_service.get_experiment(experiment.id).status == 'ready'
    assert execution.get(initial.id) == initial
    assert Path(initial.jobs[0].snapshot_path).read_bytes() == saved_xml
    assert list(services.run_storage.experiment_dir(experiment.id).rglob('config.xml')) == [Path(initial.jobs[0].snapshot_path)]
    assert services.task_repository.get_task(task.id).status == 'failed'


def test_retry_state_change_during_render_prevents_publication(preview_context, monkeypatch):
    _, services, sessions, experiment = preview_context
    execution = services.task_service.execution_service
    initial = execution.prepare(experiment.id, expected_revision=1)
    task = execution.confirm(experiment.id, preview_id=initial.id, expected_revision=1, confirmed=True)[0]
    with sessions.begin() as session:
        session.get(DailyTaskModel, str(task.id)).status = 'failed'
    render = services.task_service.adapter.render_task

    def queue_elsewhere(*args, **kwargs):
        result = render(*args, **kwargs)
        with sessions.begin() as session:
            session.get(DailyTaskModel, str(task.id)).status = 'pending'
        return result

    monkeypatch.setattr(services.task_service.adapter, 'render_task', queue_elsewhere)
    with pytest.raises(TaskStateError):
        execution.prepare(experiment.id, expected_revision=1, task_id=task.id)

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionPreviewModel)) == 1
    assert services.plan_service.get_current_revision(experiment.id).is_frozen
    assert list(services.run_storage.experiment_dir(experiment.id).rglob('config.xml')) == [Path(initial.jobs[0].snapshot_path)]


def test_failed_prepare_does_not_remove_another_requests_committed_preview(preview_context, monkeypatch):
    _, services, _, experiment = preview_context
    execution = services.task_service.execution_service
    render = services.task_service.adapter.render_task
    other_previews = []

    def publish_elsewhere_then_fail(*args, **kwargs):
        render(*args, **kwargs)
        monkeypatch.setattr(services.task_service.adapter, 'render_task', render)
        other_previews.append(execution.prepare(experiment.id, expected_revision=1))
        raise XmlValidationError('first request failed')

    monkeypatch.setattr(services.task_service.adapter, 'render_task', publish_elsewhere_then_fail)
    with pytest.raises(XmlValidationError, match='first request'):
        execution.prepare(experiment.id, expected_revision=1)

    other = other_previews[0]
    assert services.plan_service.get_current_revision(experiment.id).is_frozen
    assert execution.get(other.id) == other
    assert list(services.run_storage.experiment_dir(experiment.id).rglob('config.xml')) == [Path(other.jobs[0].snapshot_path)]
