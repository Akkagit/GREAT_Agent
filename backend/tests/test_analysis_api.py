from uuid import uuid4

from great_agent.db.analysis_repository import AnalysisJobRepository
from test_api import api_context, valid_spec  # noqa: F401
from test_execution_preview import preview_context  # noqa: F401


def test_failed_analysis_can_be_retried_without_solver_attempt(preview_context):
    client, services, sessions, experiment = preview_context
    preview = services.task_service.execution_service.prepare(experiment.id, expected_revision=1)
    task = services.task_service.execution_service.confirm(experiment.id, preview_id=preview.id,
        expected_revision=1, confirmed=True)[0]
    claim = services.task_repository.claim_next('solver')
    services.task_repository.complete_solver(claim, (), analysis_config={'hold_seconds': 600})
    jobs = AnalysisJobRepository(sessions)
    job = jobs.claim('analysis')
    jobs.finish(job, status='failed', error_message='temporary analysis error')

    response = client.get(f'/api/v1/tasks/{task.id}/analysis-jobs')
    assert response.status_code == 200
    assert response.json()['items'][0]['status'] == 'failed'
    for _ in range(2):
        response = client.post(f'/api/v1/tasks/{task.id}/analysis-jobs/{job.id}/retry')
        assert response.status_code == 200
        assert response.json()['status'] == 'pending'
    assert services.task_repository.get_task(task.id).status == 'succeeded'
    assert len(services.task_repository.list_attempts(task.id)) == 1
    assert client.post(f'/api/v1/tasks/{uuid4()}/analysis-jobs/{job.id}/retry').status_code == 404
