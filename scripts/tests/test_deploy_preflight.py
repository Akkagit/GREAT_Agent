"""Deployment checks must be read-only, local, and honest about readiness."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest


def preflight():
    spec = importlib.util.spec_from_file_location(
        'deploy_preflight', Path(__file__).parents[1] / 'deploy_preflight.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def trial(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    for relative in ('local/solver.exe', 'local/template.xml',
                     'frontend/dist/assets/app.js'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    (root / 'frontend/dist/index.html').write_text(
        '<script type="module" src="/assets/app.js"></script>', encoding='utf-8')
    for relative in ('observations', 'products'):
        (root / relative).mkdir()
    values = dict(project_root=str(root), run_root=str(root / 'runs/trial'),
                  database_path=str(root / 'data/trial/great_agent.db'),
                  host='127.0.0.1', port=8766, max_concurrency=1,
                  knowledge={'enabled': True, 'root': str(root / 'data/trial/knowledge')},
                  model={'base_url': 'https://model.example.invalid', 'name': 'test'},
                  pppflt=dict(executable=str(root / 'local/solver.exe'),
                              template_path=str(root / 'local/template.xml'),
                              observation_root=str(root / 'observations'),
                              product_root=str(root / 'products')))
    config = root / 'trial.toml'

    def save():
        lines = []
        for key, value in values.items():
            if not isinstance(value, dict):
                lines.append(f'{key} = {json.dumps(value)}')
        for section, fields in values.items():
            if isinstance(fields, dict):
                lines.append(f'[{section}]')
                lines.extend(f'{key} = {json.dumps(value)}' for key, value in fields.items())
        config.write_text('\n'.join(lines), encoding='utf-8')
    save()
    return SimpleNamespace(root=root, config=config, values=values, save=save)


def test_static_mode_is_read_only_and_never_opens_network(monkeypatch, trial):
    app = preflight()
    monkeypatch.setattr(app, 'build_opener', lambda *args: pytest.fail('network opened'))
    before = {p.relative_to(trial.root) for p in trial.root.rglob('*')}
    checks = app.collect_checks(trial.config, environ={})
    assert not any(c['status'] == 'fail' for c in checks)
    assert any(c['code'] == 'live_probe' and c['status'] == 'warn' for c in checks)
    assert before == {p.relative_to(trial.root) for p in trial.root.rglob('*')}


def test_invalid_config_does_not_echo_embedded_secrets(trial):
    app = preflight()
    secret = 'do-not-print-this-private-value'
    trial.config.write_text(f'project_root = "{secret}"\nport = "{secret}"', encoding='utf-8')
    checks = app.collect_checks(trial.config, environ={'GREAT_AGENT_MODEL_API_KEY': secret})
    assert checks[0]['status'] == 'fail'
    assert secret not in json.dumps(checks)


@pytest.mark.parametrize('field,value', [
    ('port', 8765), ('database_path', 'data/great_agent.db'),
    ('run_root', 'runs'), ('knowledge', 'data/knowledge'),
])
def test_trial_cannot_reuse_production_resources(trial, field, value):
    app = preflight()
    if field == 'knowledge':
        trial.values['knowledge']['root'] = str(trial.root / value)
    else:
        trial.values[field] = value if field == 'port' else str(trial.root / value)
    trial.save()
    checks = app.collect_checks(trial.config, environ={})
    assert any(c['code'] == 'trial_isolation' and c['status'] == 'fail' for c in checks)


def test_non_loopback_config_never_probes(monkeypatch, trial):
    app = preflight()
    trial.values['host'] = '0.0.0.0'
    trial.save()
    monkeypatch.setattr(app, 'build_opener', lambda *args: pytest.fail('network opened'))
    assert any(c['status'] == 'fail' for c in app.collect_checks(trial.config, probe=True, environ={}))


def fake_probe(monkeypatch, app, *, ready=True, knowledge=True, configured=True):
    calls = []
    def request(url, *, timeout):
        calls.append(url)
        if url.endswith('/health'):
            payload = {'status': 'ok'}
        elif url.endswith('/readiness'):
            payload = {'ready': ready}
        else:
            payload = {'knowledge': {'configured': configured, 'executor_running': knowledge}}
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(app, 'build_opener', lambda *handlers: SimpleNamespace(open=request))
    return calls


@pytest.mark.parametrize('ready,knowledge', [(False, True), (True, False)])
def test_live_probe_requires_worker_and_knowledge(monkeypatch, trial, ready, knowledge):
    app = preflight()
    calls = fake_probe(monkeypatch, app, ready=ready, knowledge=knowledge)
    checks = app.collect_checks(trial.config, probe=True, environ={})
    assert any(c['status'] == 'fail' for c in checks)
    assert len(calls) == 3
    assert all(url.startswith('http://127.0.0.1:8766/') for url in calls)


def test_missing_knowledge_service_is_not_ready(monkeypatch, trial):
    app = preflight()
    fake_probe(monkeypatch, app, configured=False)
    checks = app.collect_checks(trial.config, probe=True, environ={})
    assert any(c['code'] == 'knowledge_executor' and c['status'] == 'fail' for c in checks)


def test_successful_live_probe_is_explicitly_local_only(monkeypatch, trial):
    app = preflight()
    fake_probe(monkeypatch, app)
    checks = app.collect_checks(trial.config, probe=True, environ={})
    assert not any(c['status'] == 'fail' for c in checks)
    assert any(c['code'] == 'public_access' and c['status'] == 'warn' for c in checks)


def test_redirect_is_refused():
    app = preflight()
    handler = app.NoRedirect()
    assert handler.redirect_request(Request('http://127.0.0.1:8766/'), None, 302,
                                    'Found', {}, 'https://outside.example/') is None


def test_probe_ignores_environment_proxy_and_redacts_http_error(monkeypatch, trial):
    app = preflight()
    secret = 'private-server-response'
    seen = []
    def opener(*handlers):
        seen.extend(handlers)
        def request(url, *, timeout):
            raise HTTPError(url, 503, secret, {}, io.BytesIO(secret.encode()))
        return SimpleNamespace(open=request)
    monkeypatch.setattr(app, 'build_opener', opener)
    checks = app.collect_checks(trial.config, probe=True, environ={})
    assert any(getattr(handler, 'proxies', None) == {} for handler in seen)
    assert any(isinstance(handler, app.NoRedirect) for handler in seen)
    assert any(c['status'] == 'fail' for c in checks)
    assert secret not in json.dumps(checks)


def test_missing_frontend_asset_is_a_failure(trial):
    app = preflight()
    (trial.root / 'frontend/dist/assets/app.js').unlink()
    checks = app.collect_checks(trial.config, environ={})
    assert any(c['code'] == 'frontend' and c['status'] == 'fail' for c in checks)


def test_report_refuses_overwriting_runtime_config(trial):
    app = preflight()
    original = trial.config.read_bytes()
    assert app.main(['--config', str(trial.config), '--report', str(trial.config)]) == 1
    assert trial.config.read_bytes() == original
