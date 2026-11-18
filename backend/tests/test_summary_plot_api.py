"""Only the three canonical attempt summaries belong in image preview."""
from uuid import uuid4

from great_agent.db.models import TaskAttemptModel
from great_agent.runtime.result_inspector import ResultFileInfo
from test_api import api_context, valid_spec, seed_task  # noqa: F401


def test_summary_plot_types_exclude_same_named_cdfs_and_other_attempts(api_context, valid_spec):
    client, services, sessions = api_context
    experiment, task_id, _ = seed_task(api_context, valid_spec, status='succeeded')
    attempt_id = uuid4()
    with sessions.begin() as session:
        session.add(TaskAttemptModel(id=str(attempt_id), task_id=str(task_id), number=1, status='succeeded'))
    attempt = services.run_storage.root / str(experiment.id) / 'tasks' / '2024_350' / 'attempts' / '0001'
    paths = [
        ('analysis/enu.png', None, 'plot', 'enu'),
        ('analysis/convergence.png', None, 'plot', 'convergence'),
        ('analysis/fix_rate.png', None, 'plot', 'fix_rate'),
        ('analysis/cdf/convergence.png', None, 'plot', None),
        ('analysis/cdf/fix_rate.png', None, 'plot', None),
        ('analysis/AHB0-enu.png', 'AHB0', 'plot', None),
        ('results/enu.png', None, 'plot', None),
        ('../0002/analysis/enu.png', None, 'plot', None),
    ]
    files = []
    expected = {}
    for relative, station, kind, plot_type in paths:
        path = (attempt / relative).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'isolated image fixture')
        files.append(ResultFileInfo(station, kind, path, path.stat().st_size))
        expected[str(path)] = plot_type
    services.task_repository.add_result_files(task_id, attempt_id, tuple(files))
    stored = services.task_repository.list_attempt_results(task_id, attempt_id)
    expected_by_id = {str(item.id): expected[item.path] for item in stored}
    response = client.get(f'/api/v1/tasks/{task_id}/attempts/{attempt_id}/results')
    assert response.status_code == 200
    data = response.json()
    for item in data:
        file_id = item['url'].rsplit('/', 1)[-1]
        assert item.get('plot_type') == expected_by_id[file_id]
    assert {item['plot_type'] for item in data if item.get('plot_type')} == {'enu', 'convergence', 'fix_rate'}
    assert len([item for item in data if item.get('plot_type')]) == 3
