"""Isolated release tests: never touch checkout dist, config, or runtime DB."""
import importlib.util
import sqlite3
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def startup():
    path = Path(__file__).parents[1] / 'local_startup.py'
    assert path.exists(), 'startup needs an isolated, testable release helper'
    spec = importlib.util.spec_from_file_location('local_startup', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_paths(tmp_path):
    root = tmp_path / 'project with spaces'
    (root / 'frontend' / 'dist').mkdir(parents=True)
    (root / 'frontend' / 'dist' / 'index.html').write_text('old page')
    return root, root / 'frontend' / '.releases' / 'test'


def test_failed_build_keeps_old_dist_and_never_switches(tmp_path):
    app = startup()
    root, release = fixture_paths(tmp_path)
    def fail(command, **kwargs):
        raise RuntimeError('typecheck failed')
    with pytest.raises(RuntimeError, match='typecheck'):
        app.build_frontend(root, release, run=fail)
    assert (root / 'frontend/dist/index.html').read_text() == 'old page'
    assert not (release / 'previous-dist').exists()


def test_build_always_typechecks_and_stages_then_can_rollback(tmp_path):
    app = startup()
    root, release = fixture_paths(tmp_path)
    commands = []
    def build(command, **kwargs):
        commands.append(command)
        if '--outDir' in command:
            stage = Path(command[command.index('--outDir') + 1])
            stage.mkdir(parents=True)
            (stage / 'index.html').write_text('<script src="/assets/new.js"></script>')
            (stage / 'assets').mkdir()
            (stage / 'assets/new.js').write_text('new')
    stage = app.build_frontend(root, release, run=build)
    assert 'typescript' in str(commands[0]) and '-b' in commands[0]
    assert (root / 'frontend/dist/index.html').read_text() == 'old page'
    app.switch_frontend(root, release, stage)
    assert (root / 'frontend/dist/assets/new.js').exists()
    app.rollback_frontend(root, release)
    assert (root / 'frontend/dist/index.html').read_text() == 'old page'
    assert (release / 'failed-dist/assets/new.js').exists()


def test_invalid_staged_assets_never_replace_old_dist(tmp_path):
    app = startup()
    root, release = fixture_paths(tmp_path)
    def build(command, **kwargs):
        if '--outDir' in command:
            stage = Path(command[command.index('--outDir') + 1])
            stage.mkdir(parents=True)
            (stage / 'index.html').write_text('<script src="/assets/missing.js"></script>')
    with pytest.raises(RuntimeError, match='missing'):
        app.build_frontend(root, release, run=build)
    assert (root / 'frontend/dist/index.html').read_text() == 'old page'


def test_snapshot_keeps_database_runs_and_config_together(tmp_path):
    app = startup()
    root, release = fixture_paths(tmp_path)
    db = root / 'data/state.db'
    db.parent.mkdir()
    with sqlite3.connect(db) as connection:
        connection.execute('create table history (value text)')
        connection.execute("insert into history values ('preserve')")
    runs = root / 'runs'
    runs.mkdir()
    (runs / 'history.txt').write_text('artifact')
    config = root / 'custom config.toml'
    config.write_text('test config')
    settings = SimpleNamespace(database_path=db, run_root=runs)
    app.snapshot(settings, config, release)
    with sqlite3.connect(release / 'database.sqlite3') as connection:
        assert connection.execute('select value from history').fetchone() == ('preserve',)
    assert (release / 'runs/history.txt').read_text() == 'artifact'
    assert (release / 'config.toml').read_text() == 'test config'
    assert db.exists() and (runs / 'history.txt').exists()


def test_live_pid_refuses_without_signalling_or_overwriting(tmp_path, monkeypatch):
    app = startup()
    pid_root = tmp_path / 'pids'
    pid_root.mkdir()
    pid_file = pid_root / 'api.pid'
    pid_file.write_text('1234')
    monkeypatch.setattr(app.psutil, 'pid_exists', lambda pid: pid == 1234)
    with pytest.raises(RuntimeError, match='1234'):
        app.check_pid_files(pid_root)
    assert pid_file.read_text() == '1234'


def test_busy_worker_blocks_snapshot_before_copy(tmp_path):
    app = startup()
    from great_agent.runtime.worker_lock import WorkerLock, WorkerAlreadyRunning
    db = tmp_path / 'state.db'
    config = tmp_path / 'app.toml'
    config.write_text('config')
    destination = tmp_path / 'backup'
    with WorkerLock(db), pytest.raises(WorkerAlreadyRunning):
        app.snapshot(SimpleNamespace(database_path=db, run_root=tmp_path/'runs'), config, destination)
    assert not destination.exists()


def test_migration_snapshot_includes_knowledge_originals_and_review_cache(tmp_path):
    app = startup()
    root, release = fixture_paths(tmp_path)
    db = root / 'data/state.db'
    db.parent.mkdir()
    with sqlite3.connect(db) as connection:
        connection.execute('create table history (value text)')
    knowledge = root / 'data/knowledge'
    (knowledge / 'blobs').mkdir(parents=True)
    (knowledge / 'blobs/paper.pdf').write_bytes(b'%PDF-original')
    (knowledge / 'derived/formulas').mkdir(parents=True)
    (knowledge / 'derived/formulas/review.json').write_text('reviewed')
    config = root / 'config.toml'
    config.write_text('config')
    settings = SimpleNamespace(project_root=root, database_path=db, run_root=root/'runs',
                               knowledge=SimpleNamespace(root=knowledge))
    app.snapshot(settings, config, release)
    assert (release / 'knowledge/blobs/paper.pdf').read_bytes() == b'%PDF-original'
    assert (release / 'knowledge/derived/formulas/review.json').read_text() == 'reviewed'
    manifest = json.loads((release / 'manifest.json').read_text('utf-8'))
    assert 'knowledge/blobs/paper.pdf' in manifest['files']


def test_trial_migration_rejects_unknown_database_before_live_cli(tmp_path):
    app = startup()
    root, release = fixture_paths(tmp_path)
    db = root / 'unknown.db'
    with sqlite3.connect(db) as connection:
        connection.execute('create table unexpected (id integer)')
    before = db.read_bytes()
    config = root / 'app.toml'
    config.write_text('config')
    settings = SimpleNamespace(database_path=db, run_root=root/'runs')
    assert hasattr(app, 'prepare_database'), 'Validate a migrated copy before invoking live migration'
    commands = []
    with pytest.raises(RuntimeError, match='Unrecognized'):
        app.prepare_database(settings, config, release, run=lambda *args, **kw: commands.append(args))
    assert commands == []
    assert db.read_bytes() == before
    assert (release / 'runtime-backup/database.sqlite3').exists()


def test_current_schema_does_not_backup_or_migrate_again(tmp_path, monkeypatch):
    app = startup()
    assert hasattr(app, 'prepare_database'), 'Skip backup/migration when schema already valid'
    monkeypatch.setattr(app, 'migration_needed', lambda settings: False)
    def unexpected(*args, **kwargs):
        pytest.fail('Current-schema startup must not copy runs or invoke migration')
    monkeypatch.setattr(app, 'snapshot', unexpected)
    app.prepare_database(SimpleNamespace(), tmp_path/'app.toml', tmp_path/'release', run=unexpected)
    assert not (tmp_path/'release').exists()


def isolated_start(app, root, monkeypatch):
    settings = SimpleNamespace(project_root=root, host='127.0.0.1', port=8765,
                               research=SimpleNamespace(enabled=True))
    monkeypatch.setattr(app.AppSettings, 'load', lambda *args: settings)
    monkeypatch.setattr(app, 'assert_idle', lambda *args: None)
    def build(root, release):
        stage = release / 'staged-dist'
        stage.mkdir()
        (stage / 'index.html').write_text('new page')
        return stage
    monkeypatch.setattr(app, 'build_frontend', build)


def test_migration_failure_never_starts_children_or_replaces_dist(tmp_path, monkeypatch):
    app = startup()
    root, _ = fixture_paths(tmp_path)
    isolated_start(app, root, monkeypatch)
    def fail(*args):
        raise RuntimeError('migration exit 2')
    monkeypatch.setattr(app, 'prepare_database', fail)
    monkeypatch.setattr(app, 'launch', lambda *args: pytest.fail('Must not launch after failed migration'))
    with pytest.raises(RuntimeError, match='migration exit 2'):
        app.start(root, root/'app.toml', no_browser=True)
    assert (root/'frontend/dist/index.html').read_text() == 'old page'
    assert not (root/'data/pids/api.pid').exists()
    assert list((root/'frontend/.releases').glob('*/failure.txt'))


def test_health_failure_rolls_back_dist_and_stops_only_owned_children(tmp_path, monkeypatch):
    app = startup()
    root, _ = fixture_paths(tmp_path)
    isolated_start(app, root, monkeypatch)
    monkeypatch.setattr(app, 'prepare_database', lambda *args: None)
    children = []
    class Child:
        pid = 12345
        stopped = False
        def poll(self):
            return 0 if self.stopped else None
        def terminate(self):
            self.stopped = True
        def wait(self, timeout):
            assert self.stopped
    def launch(*args):
        child = Child()
        children.append(child)
        return child
    monkeypatch.setattr(app, 'launch', launch)
    monkeypatch.setattr(app, 'terminate_owned', lambda child: child.terminate())
    def fail(*args):
        raise RuntimeError('health failed')
    monkeypatch.setattr(app, 'await_health', fail)
    pid_root = root/'data/pids'
    pid_root.mkdir(parents=True)
    (pid_root/'api.pid').write_text('old stale pid')
    with pytest.raises(RuntimeError, match='health failed'):
        app.start(root, root/'app.toml', no_browser=True)
    assert len(children) == 3 and all(child.stopped for child in children)
    assert (root/'frontend/dist/index.html').read_text() == 'old page'
    assert (pid_root/'api.pid').read_text() == 'old stale pid'
    assert list((root/'frontend/.releases').glob('*/failed-dist/index.html'))


def test_real_child_pid_is_interpreter_with_venv_imports_and_cleanup(tmp_path):
    app = startup()
    result = tmp_path/'identity.json'
    command = app.cli_command('api', tmp_path/'unused.toml')[:1] + ['-c',
        "import os,sys,json,time,pdfplumber; from pathlib import Path; "
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),sys.prefix,pdfplumber.__version__])); time.sleep(30)", str(result)]
    child = app.launch(command, tmp_path, tmp_path/'child.log')
    try:
        deadline = time.monotonic() + 10
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert result.exists(), (tmp_path/'child.log').read_text()
        identity = json.loads(result.read_text())
        assert identity[0] == child.pid, 'Popen must identify actual interpreter, never a venv redirector'
        assert Path(identity[1]) == Path(sys.prefix)
        from importlib.metadata import version
        assert identity[2] == version('pdfplumber')
    finally:
        assert hasattr(app, 'terminate_owned'), 'Cleanup must verify actual process identity'
        app.terminate_owned(child)
    assert child.wait(timeout=5) is not None


@pytest.mark.parametrize('equals', [False, True])
def test_other_config_same_database_refuses_before_worker_lock(tmp_path, monkeypatch, equals):
    app = startup()
    current = tmp_path/'one.toml'
    other = tmp_path/'other.toml'
    other.write_text(json.dumps({}))
    settings = SimpleNamespace(project_root=tmp_path, database_path=tmp_path/'state.db', run_root=tmp_path/'runs', host='127.0.0.1', port=8765)
    other.write_text('\n'.join(f'{key} = {json.dumps(str(value))}' for key, value in
        [('project_root', tmp_path), ('database_path', settings.database_path), ('run_root', settings.run_root)]))
    args = ['python', '-m', 'great_agent.cli', 'api'] + ([f'--config={other}'] if equals else ['--config', str(other)])
    process = SimpleNamespace(pid=98765, info={'cmdline': args}, cwd=lambda: str(tmp_path))
    monkeypatch.setattr(app.psutil, 'process_iter', lambda *args: [process])
    class Probe:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def connect_ex(self, address): return 1
    monkeypatch.setattr(app.socket, 'socket', Probe)
    with pytest.raises(RuntimeError, match='98765'):
        app.assert_idle(settings, current, tmp_path/'pids')
    assert not (tmp_path/'state.db.worker.lock').exists()


@pytest.mark.parametrize('root_identity_matches', [True, False])
def test_owned_tree_cleanup_preserves_unknown_and_reused_pids(monkeypatch, root_identity_matches):
    app = startup()
    stopped = []
    class Process:
        def __init__(self, pid, matches=True):
            self.pid, self.matches = pid, matches
        def is_running(self): return self.matches
        def create_time(self): return self.pid
        def children(self, recursive): return [descendant]
        def terminate(self): stopped.append(self.pid)
    root = Process(10, root_identity_matches)
    descendant = Process(11)
    unknown = Process(12)
    child = SimpleNamespace(owned_processes=[root], wait=lambda timeout: 0)
    monkeypatch.setattr(app.psutil, 'wait_procs', lambda processes, timeout: (processes, []))
    app.terminate_owned(child)
    assert stopped == ([11, 10] if root_identity_matches else [])
    assert unknown.pid not in stopped
