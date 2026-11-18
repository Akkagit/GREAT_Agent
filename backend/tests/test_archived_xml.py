from uuid import uuid4

from great_agent.db.models import TaskAttemptModel
from test_api import api_context, valid_spec, seed_task  # noqa: F401


def test_archived_xml_is_read_only_and_bound_to_attempt(api_context, valid_spec):
    client, services, sessions = api_context
    experiment, task_id, _ = seed_task(api_context, valid_spec, status="succeeded")
    experiment_id = experiment.id
    attempt_id = uuid4()
    with sessions.begin() as session:
        session.add(TaskAttemptModel(id=str(attempt_id), task_id=str(task_id), number=1, status="succeeded"))
    directory = services.run_storage.root / str(experiment_id) / 'tasks' / '2024_350' / 'attempts' / '0001'
    directory.mkdir(parents=True)
    xml = directory / 'config.xml'
    content = '<?xml version="1.0"?>\r\n<config>history</config>\r\n'
    xml.write_bytes(content.encode())
    before = xml.stat().st_mtime_ns
    response = client.get(f'/api/v1/tasks/{task_id}/attempts/{attempt_id}/xml')
    assert response.status_code == 200
    assert response.json()['xml_content'] == content
    assert response.json()['xml_path'] == str(xml.resolve())
    assert xml.stat().st_mtime_ns == before
    assert services.plan_service.get_current_revision(experiment_id).number == 1


def test_archived_xml_rejects_attempt_from_another_task(api_context, valid_spec):
    client, _, sessions = api_context
    _, first_task, _ = seed_task(api_context, valid_spec, status="succeeded")
    _, other_task, _ = seed_task(api_context, valid_spec, status="succeeded")
    attempt_id = uuid4()
    with sessions.begin() as session:
        session.add(TaskAttemptModel(id=str(attempt_id), task_id=str(other_task), number=1, status="succeeded"))
    response = client.get(f'/api/v1/tasks/{first_task}/attempts/{attempt_id}/xml')
    assert response.status_code == 404


def test_archived_xml_missing_file_returns_not_found(api_context, valid_spec):
    client, _, sessions = api_context
    _, task_id, _ = seed_task(api_context, valid_spec, status="succeeded")
    attempt_id = uuid4()
    with sessions.begin() as session:
        session.add(TaskAttemptModel(id=str(attempt_id), task_id=str(task_id), number=1, status="succeeded"))
    assert client.get(f'/api/v1/tasks/{task_id}/attempts/{attempt_id}/xml').status_code == 404
