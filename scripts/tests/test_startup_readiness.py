"""Startup must wait for the mode-appropriate readiness contract."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def startup():
    spec = importlib.util.spec_from_file_location('startup_readiness', Path(__file__).parents[1] / 'local_startup.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probes(monkeypatch, app, *, readiness):
    calls = []
    timer = SimpleNamespace(value=0.0)
    def sleep(seconds):
        timer.value += seconds
    monkeypatch.setattr(app, 'time', SimpleNamespace(monotonic=lambda: timer.value, sleep=sleep))
    class Response(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
    def request(url, *, timeout):
        calls.append(url)
        if '/readiness' in url:
            data = json.dumps({'ready': readiness(url)}).encode()
        elif url.endswith('/health'):
            data = b'{"status":"ok","mode":"local"}'
        else:
            data = b'published page'
        return Response(data)
    monkeypatch.setattr(app, 'build_opener', lambda *args: SimpleNamespace(open=request))
    child = SimpleNamespace(owned_processes=[], poll=lambda: None)
    return calls, child


def test_alive_worker_without_readiness_never_completes_startup(monkeypatch):
    app = startup()
    calls, child = probes(monkeypatch, app, readiness=lambda url: False)
    with pytest.raises(RuntimeError, match='timed out'):
        app.await_health({'api': child, 'worker': child}, 'http://127.0.0.1:9', b'published page', timeout=3)
    assert any(url.endswith('/readiness') for url in calls)


def test_api_only_uses_readiness_without_requiring_solver_worker(monkeypatch):
    app = startup()
    calls, child = probes(monkeypatch, app, readiness=lambda url: url.endswith('?require_worker=false'))
    app.await_health({'api': child}, 'http://127.0.0.1:9', b'published page', timeout=3)
    assert any(url.endswith('/readiness?require_worker=false') for url in calls)


def test_ready_worker_still_waits_for_stable_health_and_page(monkeypatch):
    app = startup()
    calls, child = probes(monkeypatch, app, readiness=lambda url: True)
    app.await_health({'api': child, 'worker': child}, 'http://127.0.0.1:9', b'published page', timeout=3)
    assert calls.count('http://127.0.0.1:9/api/v1/readiness') >= 2
    assert calls.count('http://127.0.0.1:9/api/v1/health') >= 2
    assert calls.count('http://127.0.0.1:9/') >= 2
