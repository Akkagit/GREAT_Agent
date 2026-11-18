from uuid import uuid4

import httpx
import pytest

from great_agent.cli import build_app
from great_agent.config import AppSettings


@pytest.fixture
def research_app(valid_config):
    settings = AppSettings.load(valid_config, {'GREAT_AGENT_MODEL_API_KEY': 'fixture-not-a-live-key'})
    (settings.project_root / 'algorithm.py').write_text('def reset(gap):\n    return gap > 30\n', encoding='utf-8')
    app = build_app(settings)
    yield app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


async def test_third_workspace_api_catalog_and_durable_acceptance(research_app):
    async with client(research_app) as api:
        catalog = await api.get('/api/v1/research/catalog')
        assert catalog.status_code == 200
        assert catalog.json()['sources'][0]['id'] == 'project'
        assert catalog.json()['profiles'][0]['configured']
        created = await api.post('/api/v1/research/sessions', json={'title': '诊断', 'goal': '核查收敛',
            'materials': [{'kind': 'code', 'source_id': 'project', 'paths': ['algorithm.py']}]})
        assert created.status_code == 201
        session = created.json()
        body = {'request_id': str(uuid4()), 'expected_version': session['version'], 'message': '读取并检查实现'}
        accepted = await api.post(f'/api/v1/research/sessions/{session["id"]}/runs', json=body)
        assert accepted.status_code == 202 and accepted.json()['status'] == 'queued'
        replay = await api.post(f'/api/v1/research/sessions/{session["id"]}/runs', json=body)
        assert replay.json()['id'] == accepted.json()['id']
        changed = await api.post(f'/api/v1/research/sessions/{session["id"]}/runs', json={**body, 'message': 'different'})
        assert changed.status_code == 409 and changed.json()['error']['code'] == 'request_conflict'
        restored = (await api.get(f'/api/v1/research/sessions/{session["id"]}')).json()
        assert len(restored['messages']) == 1 and restored['active_run_id'] == accepted.json()['id']
        stopped = await api.post(f'/api/v1/research/runs/{accepted.json()["id"]}/cancel', json={})
        assert stopped.status_code == 200 and stopped.json()['status'] == 'cancelled'
        events = await api.get(f'/api/v1/research/sessions/{session["id"]}/events?once=true')
        assert events.status_code == 200 and 'cancelled' in events.text


async def test_material_and_settings_updates_are_versioned_and_no_code_is_exposed_in_list(research_app):
    async with client(research_app) as api:
        session = (await api.post('/api/v1/research/sessions', json={})).json()
        path = f'/api/v1/research/sessions/{session["id"]}'
        updated = await api.patch(path + '/materials', json={'expected_version': session['version'],
            'materials': [{'kind': 'code', 'source_id': 'project', 'paths': ['algorithm.py']}]})
        assert updated.status_code == 200 and updated.json()['material_revision'] == 2
        conflict = await api.patch(path + '/settings', json={'expected_version': session['version'],
            'model_profile_id': 'default', 'mode': 'quick'})
        assert conflict.status_code == 409
        listing = await api.get('/api/v1/research/sessions')
        assert 'return gap > 30' not in listing.text
        assert listing.json()['items'][0]['id'] == session['id']


async def test_only_registered_read_scopes_and_valid_shapes_are_accepted(research_app):
    async with client(research_app) as api:
        for selection in [
            [{'kind': 'code', 'source_id': 'project', 'paths': ['../secret.py']}],
            [{'kind': 'code', 'source_id': 'missing', 'paths': ['algorithm.py']}],
            [{'kind': 'execute', 'command': 'python'}],
        ]:
            response = await api.post('/api/v1/research/sessions', json={'materials': selection})
            assert response.status_code in (403, 404, 422)
            assert response.json().get('error') or response.json().get('detail')
        session = (await api.post('/api/v1/research/sessions', json={})).json()
        response = await api.post(f'/api/v1/research/sessions/{session["id"]}/runs', json={
            'request_id': str(uuid4()), 'expected_version': session['version'], 'message': ' ', 'execute': True})
        assert response.status_code == 422


async def test_research_feature_is_wired_without_auto_starting_worker_or_model(research_app):
    service = getattr(research_app.state.services, 'research_service', None)
    assert service is not None
    assert not hasattr(service, '_active')
    assert research_app.state.services.worker is not None
    assert getattr(research_app.state, 'research_worker', None) is not None
