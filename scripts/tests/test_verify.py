"""Release gate boundaries, without invoking the application or real build."""
import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def verifier():
    path = Path(__file__).parents[1] / 'verify.py'
    assert path.exists(), 'the release gate must have a testable executor'
    spec = importlib.util.spec_from_file_location('great_verify', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(tmp_path):
    root = tmp_path / 'project with spaces'
    dist = root / 'frontend' / 'dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('published page', encoding='utf-8')
    return root, root / 'reports' / 'verify.json'


def build_if_requested(command):
    if '--outDir' in command:
        output = Path(command[command.index('--outDir') + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / 'index.html').write_text('isolated build', encoding='utf-8')


@pytest.mark.parametrize('failed_step', range(5))
def test_failure_keeps_real_exit_code_and_skips_remaining_steps(tmp_path, failed_step):
    app = verifier()
    root, report_path = project(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        kwargs['stdout'].write('child output\n')
        build_if_requested(command)
        return SimpleNamespace(returncode=23 if len(calls) - 1 == failed_step else 0)

    assert app.verify(root, report_path, run=run) == 23
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['status'] == 'failed' and report['exit_code'] == 23
    assert len(calls) == failed_step + 1
    assert [step['status'] for step in report['steps']] == (
        ['passed'] * failed_step + ['failed'] + ['skipped'] * (4 - failed_step)
    )
    failed = report['steps'][failed_step]
    assert failed['exit_code'] == 23 and failed['duration_seconds'] >= 0
    assert failed['command'] == calls[-1] and Path(failed['cwd']).is_absolute()
    assert 'child output' in Path(failed['log']).read_text(encoding='utf-8')
    assert (root / 'frontend/dist/index.html').read_text() == 'published page'


def test_success_runs_all_checks_and_builds_only_in_unique_temp_directory(tmp_path):
    app = verifier()
    root, report_path = project(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        build_if_requested(command)
        return SimpleNamespace(returncode=0)

    assert app.verify(root, report_path, run=run) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['status'] == 'passed' and len(calls) == 5
    assert all(step['status'] == 'passed' for step in report['steps'])
    python = calls[0][0]
    assert 'backend/tests' in python and 'scripts/tests' in python
    assert 'pytest_asyncio.plugin' in python
    assert calls[0][1]['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
    assert calls[1][0][-3:] == ['test', '--', '--run']
    assert calls[2][0][-1] == 'typecheck'
    assert calls[3][0][-1] == 'lint'
    build = calls[4][0]
    assert 'build' in build and '--outDir' in build
    output = Path(build[build.index('--outDir') + 1]).resolve()
    assert output.is_absolute() and not output.is_relative_to(root.resolve())
    assert (root / 'frontend/dist/index.html').read_text() == 'published page'
    assert app.verify(root, report_path, run=run) == 0
    second = calls[-1][0]
    assert second[second.index('--outDir') + 1] != str(output)


@pytest.mark.parametrize('scratch_exists', [False, True], ids=['clean', 'existing'])
def test_python_gate_can_create_tmp_path_in_a_clean_checkout(tmp_path, scratch_exists):
    app = verifier()
    root, report_path = project(tmp_path)
    if scratch_exists:
        (root / '.tmp').mkdir()
    tests = root / 'backend' / 'tests'
    tests.mkdir(parents=True)
    (root / 'scripts' / 'tests').mkdir(parents=True)
    (tests / 'test_fixture.py').write_text(
        'def test_tmp_path(tmp_path):\n    assert tmp_path.is_dir()\n', encoding='utf-8',
    )

    def run(command, **kwargs):
        if '--pytest-offline' in command:
            return app.run_command(command, **kwargs)
        build_if_requested(command)
        return SimpleNamespace(returncode=0)

    assert app.verify(root, report_path, run=run) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert '1 passed' in Path(report['steps'][0]['log']).read_text(encoding='utf-8')


def test_report_fingerprints_working_sources_locks_and_isolated_build(tmp_path):
    import hashlib
    app = verifier()
    root, report_path = project(tmp_path)
    source = root / 'backend/src/great_agent'
    source.mkdir(parents=True)
    (source / 'sample.py').write_bytes(b'VALUE = 1\n')
    (root / 'uv.lock').write_bytes(b'locked fixture\n')
    (root / 'config').mkdir()
    (root / 'config/local.toml').write_text('api_key = "must-not-appear"')
    def run(command, **kwargs):
        build_if_requested(command)
        return SimpleNamespace(returncode=0)
    assert app.verify(root, report_path, run=run) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['source']['files']['uv.lock'] == hashlib.sha256(b'locked fixture\n').hexdigest()
    assert report['source']['files']['backend/src/great_agent/sample.py'] == hashlib.sha256(b'VALUE = 1\n').hexdigest()
    assert 'config/local.toml' not in report['source']['files']
    assert report['build']['index_sha256'] == hashlib.sha256(b'isolated build').hexdigest()
    assert 'must-not-appear' not in report_path.read_text(encoding='utf-8')


def test_success_exit_without_build_artifact_is_failure(tmp_path):
    app = verifier()
    root, report_path = project(tmp_path)
    assert app.verify(root, report_path, run=lambda *a, **k: SimpleNamespace(returncode=0)) == 1
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert report['steps'][-1]['child_exit_code'] == 0
    assert 'index.html' in report['steps'][-1]['error']


@pytest.mark.parametrize('error,status,exit_code', [
    (subprocess.TimeoutExpired(['child'], 0.1), 'timed_out', 124),
    (FileNotFoundError('missing executable'), 'error', 127),
])
def test_timeout_or_missing_tool_cannot_pass(tmp_path, error, status, exit_code):
    app = verifier()
    root, report_path = project(tmp_path)

    def fail(*args, **kwargs):
        raise error

    assert app.verify(root, report_path, run=fail, timeout_seconds=0.1) == exit_code
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['steps'][0]['status'] == status
    assert report['steps'][0]['child_exit_code'] is None
    assert report['status'] == 'failed'
    assert all(step['status'] == 'skipped' for step in report['steps'][1:])


def test_gate_removes_local_model_credentials_from_children(tmp_path, monkeypatch):
    app = verifier()
    root, report_path = project(tmp_path)
    for name in ('GREAT_AGENT_MODEL_API_KEY', 'OPENAI_API_KEY', 'DASHSCOPE_API_KEY'):
        monkeypatch.setenv(name, 'must-not-escape')

    def run(command, **kwargs):
        assert 'must-not-escape' not in kwargs['env'].values()
        build_if_requested(command)
        return SimpleNamespace(returncode=0)

    assert app.verify(root, report_path, run=run) == 0
    assert 'must-not-escape' not in report_path.read_text(encoding='utf-8')


def test_executor_preserves_real_child_failure_and_log(tmp_path):
    app = verifier()
    log_path = tmp_path / 'child.log'
    with log_path.open('w', encoding='utf-8') as log:
        result = app.run_command(
            [sys.executable, '-u', '-c', "print('real child'); raise SystemExit(19)"],
            cwd=tmp_path, env=None, stdout=log, stderr=subprocess.STDOUT, timeout=10,
        )
    assert result.returncode == 19
    assert 'real child' in log_path.read_text()


def test_executor_timeout_is_an_exception_and_reaps_child(tmp_path):
    app = verifier()
    with (tmp_path / 'timeout.log').open('w', encoding='utf-8') as log:
        with pytest.raises(subprocess.TimeoutExpired):
            app.run_command(
                [sys.executable, '-u', '-c', 'import time; time.sleep(30)'],
                cwd=tmp_path, env=None, stdout=log, stderr=subprocess.STDOUT, timeout=0.1,
            )


@pytest.mark.skipif(os.name != 'nt', reason='Windows process-tree cleanup')
def test_timeout_still_reaps_child_if_windows_tree_killer_is_unavailable(tmp_path, monkeypatch):
    app = verifier()
    real_popen = subprocess.Popen
    children = []

    def launch(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def unavailable(*args, **kwargs):
        raise FileNotFoundError('taskkill unavailable')

    monkeypatch.setattr(app.subprocess, 'Popen', launch)
    monkeypatch.setattr(app.subprocess, 'run', unavailable)
    try:
        with (tmp_path / 'timeout.log').open('w', encoding='utf-8') as log:
            with pytest.raises(subprocess.TimeoutExpired):
                app.run_command(
                    [sys.executable, '-c', 'import time; time.sleep(30)'],
                    cwd=tmp_path, env=None, stdout=log, stderr=subprocess.STDOUT, timeout=0.1,
                )
        assert children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)


@pytest.mark.parametrize('method,args', [
    (lambda: socket.getaddrinfo, ('model.example', 443)),
    (lambda: socket.create_connection, (('model.example', 443),)),
])
def test_python_test_network_guard_refuses_external_connections(method, args):
    app = verifier()
    with app.offline_network():
        with pytest.raises(RuntimeError, match='offline'):
            method()(*args)
