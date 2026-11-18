"""Offline backups exercise real temporary SQLite, originals and run artifacts."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import os
import shutil
from types import SimpleNamespace

import pytest


def service():
    name = 'great_agent.services.backup_service'
    assert importlib.util.find_spec(name) is not None, 'complete offline backup service is missing'
    return importlib.import_module(name)


def source(tmp_path):
    root = tmp_path / 'source project'
    runs = root / 'runs'
    knowledge = root / 'data/knowledge'
    runs.mkdir(parents=True)
    (knowledge / 'blobs/ab').mkdir(parents=True)
    (knowledge / 'derived/formulas').mkdir(parents=True)
    (runs / 'attempt-1.flt').write_text('solver original')
    (knowledge / 'blobs/ab/original.pdf').write_bytes(b'%PDF-1.7\nreal original')
    (knowledge / 'derived/formulas/review.json').write_text('{"status":"locally_reviewed"}')
    db = root / 'data/state.sqlite3'
    with sqlite3.connect(db) as connection:
        connection.execute('create table attempts(id integer primary key, artifact text)')
        connection.execute('create table documents(id integer primary key, original_relpath text, attempt_id integer references attempts(id))')
        connection.execute('insert into attempts values(1, ?)', (str(runs / 'attempt-1.flt'),))
        connection.execute("insert into documents values(1, 'blobs/ab/original.pdf', 1)")
    config = root / 'config.toml'
    config.write_text('\n'.join([
        f'project_root = {json.dumps(str(root))}',
        f'database_path = {json.dumps(str(db))}',
        f'run_root = {json.dumps(str(runs))}',
        '[knowledge]', f'root = {json.dumps(str(knowledge))}',
        '[model]', 'api_key = "private-fixture-secret"',
    ]), encoding='utf-8')
    settings = SimpleNamespace(project_root=root, database_path=db, run_root=runs,
                               knowledge=SimpleNamespace(root=knowledge))
    return settings, config


def test_roundtrip_preserves_pdf_review_cache_history_and_safe_report(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    report = app.backup(settings, config, backup)
    assert report['status'] == 'ok'
    manifest = json.loads((backup / 'manifest.json').read_text('utf-8'))
    assert manifest['index_policy']['strategy'] == 'rebuild'
    assert 'private-fixture-secret' not in json.dumps(report) + json.dumps(manifest)
    assert manifest['files']['knowledge/blobs/ab/original.pdf']['sha256'] == hashlib.sha256(b'%PDF-1.7\nreal original').hexdigest()
    assert app.verify(backup)['status'] == 'ok'
    target = tmp_path / 'restored project'
    assert app.restore(backup, target)['status'] == 'ok'
    with sqlite3.connect(target / 'database.sqlite3') as connection:
        assert connection.execute('select original_relpath from documents').fetchone() == ('blobs/ab/original.pdf',)
        assert connection.execute('select id from attempts').fetchall() == [(1,)]
    assert (target / 'knowledge/blobs/ab/original.pdf').read_bytes() == b'%PDF-1.7\nreal original'
    assert (target / 'knowledge/derived/formulas/review.json').read_text() == '{"status":"locally_reviewed"}'
    assert (target / 'runs/attempt-1.flt').read_text() == 'solver original'
    import tomllib
    restored = tomllib.loads((target / 'config.toml').read_text('utf-8'))
    assert Path(restored['project_root']) == target
    assert Path(restored['database_path']) == target / 'database.sqlite3'
    assert Path(restored['knowledge']['root']) == target / 'knowledge'
    assert (target / 'original-config.toml').read_bytes() == config.read_bytes()


def test_hash_tampering_refuses_verify_and_restore_without_creating_target(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    (backup / 'runs/attempt-1.flt').write_text('corrupt')
    with pytest.raises(RuntimeError, match='hash|size|checksum'):
        app.verify(backup)
    with pytest.raises(RuntimeError):
        app.restore(backup, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


@pytest.mark.parametrize('unsafe', ['../outside', '/absolute', 'C:/escape', 'runs/../escape', 'runs\\escape', 'runs/file:stream'])
def test_manifest_path_escape_rejected_before_writing(tmp_path, unsafe):
    app = service()
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    manifest = json.loads((backup / 'manifest.json').read_text())
    manifest['files'][unsafe] = {'size_bytes': 0, 'sha256': '0' * 64}
    (backup / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='path|manifest'):
        app.restore(backup, tmp_path / 'target')
    assert not (tmp_path / 'target').exists()


def test_nonempty_target_and_overlapping_backup_refuse_without_changes(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    target = tmp_path / 'target'
    target.mkdir()
    (target / 'research.txt').write_text('preserve')
    with pytest.raises(RuntimeError, match='empty'):
        app.restore(backup, target)
    assert (target / 'research.txt').read_text() == 'preserve'
    with pytest.raises(RuntimeError, match='overlap|outside'):
        app.backup(settings, config, settings.run_root / 'backup')
    assert not (settings.run_root / 'backup').exists()
    with pytest.raises(RuntimeError, match='overlap|outside'):
        app.restore(backup, backup / 'restored')


def test_active_worker_blocks_backup_and_restore_before_copy(tmp_path):
    app = service()
    from great_agent.runtime.worker_lock import WorkerAlreadyRunning, WorkerLock
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    with WorkerLock(settings.database_path):
        with pytest.raises(WorkerAlreadyRunning):
            app.backup(settings, config, tmp_path / 'blocked-backup')
        with pytest.raises(WorkerAlreadyRunning):
            app.restore(backup, tmp_path / 'blocked-restore', settings=settings)
    assert not (tmp_path / 'blocked-backup').exists()
    assert not (tmp_path / 'blocked-restore').exists()


def test_foreign_key_damage_fails_even_with_matching_file_hash(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute('insert into documents values(2, "missing.pdf", 99)')
    with pytest.raises(RuntimeError, match='foreign key'):
        app.backup(settings, config, tmp_path / 'backup')


def test_source_change_during_copy_refuses_complete_snapshot(tmp_path, monkeypatch):
    app = service()
    settings, config = source(tmp_path)
    original = app.shutil.copy2
    changed = False
    def changing(src, dest, *args, **kwargs):
        nonlocal changed
        result = original(src, dest, *args, **kwargs)
        if not changed and Path(src).name == 'attempt-1.flt':
            Path(src).write_text('changed concurrently')
            changed = True
        return result
    monkeypatch.setattr(app.shutil, 'copy2', changing)
    with pytest.raises(RuntimeError, match='changed|snapshot'):
        app.backup(settings, config, tmp_path / 'backup')
    assert not (tmp_path / 'backup/manifest.json').exists()


def test_symbolic_links_are_rejected_in_sources_and_backup(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    link = settings.run_root / 'external'
    try:
        link.symlink_to(config)
    except OSError as error:
        pytest.skip(f'Host does not permit symlink creation: {error}')
    with pytest.raises(RuntimeError, match='link|reparse'):
        app.backup(settings, config, tmp_path / 'backup')
    link.unlink()
    backup = tmp_path / 'valid-backup'
    app.backup(settings, config, backup)
    (backup / 'runs/attempt-1.flt').unlink()
    (backup / 'runs/attempt-1.flt').symlink_to(settings.run_root / 'attempt-1.flt')
    with pytest.raises(RuntimeError, match='link|reparse'):
        app.verify(backup)


def test_restore_relocates_historical_artifact_and_log_paths(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute('create table result_files(id text primary key, path text)')
        connection.execute('create table task_attempts(id text primary key, log_path text)')
        connection.execute('insert into result_files values("result-1", ?)', (str(settings.run_root/'attempt-1.flt'),))
        connection.execute('insert into task_attempts values("attempt-1", ?)', (str(settings.run_root/'attempt-1.log'),))
    (settings.run_root / 'attempt-1.log').write_text('stdout')
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    restored = tmp_path / 'restored'
    app.restore(backup, restored)
    with sqlite3.connect(restored / 'database.sqlite3') as connection:
        artifact = Path(connection.execute('select path from result_files').fetchone()[0])
        log = Path(connection.execute('select log_path from task_attempts').fetchone()[0])
    assert artifact == restored / 'runs/attempt-1.flt'
    assert artifact.read_text() == 'solver original'
    assert log == restored / 'runs/attempt-1.log'
    assert log.read_text() == 'stdout'


@pytest.mark.parametrize('broken_reference', ['blobs/missing.pdf', '../outside.pdf'])
def test_backup_refuses_unrestorable_knowledge_original_references(tmp_path, broken_reference):
    app = service()
    settings, config = source(tmp_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute('create table knowledge_blobs(original_relpath text, sha256 text, size_bytes integer)')
        connection.execute('insert into knowledge_blobs values(?, ?, 1)', (broken_reference, '0'*64))
    with pytest.raises(RuntimeError, match='original|path'):
        app.backup(settings, config, tmp_path / 'backup')


@pytest.mark.parametrize('operation', ['api', 'worker'])
def test_live_app_sharing_only_knowledge_blocks_snapshot(tmp_path, monkeypatch, operation):
    app = service()
    settings, config = source(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    other_config = other / 'config.toml'
    other_config.write_text('\n'.join([
        f'project_root = {json.dumps(str(other))}',
        f'database_path = {json.dumps(str(other / "state.db"))}',
        f'run_root = {json.dumps(str(other / "runs"))}',
        '[knowledge]', f'root = {json.dumps(str(settings.knowledge.root))}',
        '[model]', 'api_key = "private-other-secret"',
    ]))
    process = SimpleNamespace(pid=98765, info={'cmdline': ['python', '-m', 'great_agent.cli', operation, '--config', str(other_config)]}, cwd=lambda: str(other))
    monkeypatch.setattr(app.psutil, 'process_iter', lambda *args: [process])
    with pytest.raises(RuntimeError, match='Active|stop') as error:
        app.backup(settings, config, tmp_path / 'backup')
    assert 'private-other-secret' not in str(error.value)
    assert not (tmp_path / 'backup').exists()


def test_existing_empty_destination_and_wal_commits_are_supported(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    destination = tmp_path / 'backup'
    destination.mkdir()
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('insert into attempts values(2, "committed in WAL")')
        connection.commit()
        app.backup(settings, config, destination)
    with sqlite3.connect(destination / 'database.sqlite3') as connection:
        assert connection.execute('select artifact from attempts where id=2').fetchone() == ('committed in WAL',)
    restored = tmp_path / 'restored'
    restored.mkdir()
    app.restore(destination, restored)
    assert (restored / 'knowledge/blobs/ab/original.pdf').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction boundary')
def test_windows_junction_is_rejected_without_following_external_tree(tmp_path):
    app = service()
    settings, config = source(tmp_path)
    external = tmp_path / 'external originals'
    external.mkdir()
    (external / 'valuable.pdf').write_bytes(b'%PDF-untouched')
    junction = settings.knowledge.root / 'linked-originals'
    creation = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(external)],
                              capture_output=True, text=True, timeout=10)
    if creation.returncode != 0:
        pytest.skip('Host does not permit junction creation: ' + creation.stderr.strip())
    try:
        with pytest.raises(RuntimeError, match='link|reparse'):
            app.backup(settings, config, tmp_path / 'backup')
        assert not (tmp_path / 'backup').exists()
        assert (external / 'valuable.pdf').read_bytes() == b'%PDF-untouched'
    finally:
        # rmdir unlinks only this known junction; never recursively deletes it.
        assert junction.absolute().parent == settings.knowledge.root.absolute()
        junction.rmdir()


def test_powershell_wrapper_runs_roundtrip_and_propagates_corrupt_exit(tmp_path):
    host = shutil.which('pwsh') or shutil.which('powershell')
    if host is None:
        pytest.skip('PowerShell host not installed')
    settings, config = source(tmp_path)
    with config.open('a', encoding='utf-8') as stream:
        stream.write('\nbase_url = "http://127.0.0.1:9/v1"\nname = "offline-test"\n[pppflt]\n')
        for key in ('executable', 'template_path', 'observation_root', 'product_root'):
            stream.write(f'{key} = {json.dumps(str(tmp_path / key))}\n')
    project = Path(__file__).resolve().parents[2]
    script = project / 'scripts/backup.ps1'
    def run(*args):
        return subprocess.run([host, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), *args],
                              cwd=project, capture_output=True, text=True, timeout=30)
    backup = tmp_path / 'backup'
    result = run('-Operation', 'backup', '-Config', str(config), '-Destination', str(backup))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'ok'
    assert run('-Operation', 'verify', '-Backup', str(backup)).returncode == 0
    restored = tmp_path / 'restored'
    result = run('-Operation', 'restore', '-Backup', str(backup), '-Destination', str(restored))
    assert result.returncode == 0, result.stdout + result.stderr
    assert (restored / 'knowledge/blobs/ab/original.pdf').read_bytes() == b'%PDF-1.7\nreal original'
    (backup / 'config.toml').write_text('private-fixture-secret')
    result = run('-Operation', 'verify', '-Backup', str(backup))
    assert result.returncode != 0
    assert 'private-fixture-secret' not in result.stdout + result.stderr


def test_restore_refuses_file_created_concurrently_without_overwriting(tmp_path, monkeypatch):
    app = service()
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    target = tmp_path / 'target'
    resolver = app._path
    injected = False
    def racing(value):
        nonlocal injected
        result = resolver(value)
        if result == target / 'runs/attempt-1.flt' and not injected:
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text('new independent research')
            injected = True
        return result
    monkeypatch.setattr(app, '_path', racing)
    with pytest.raises((RuntimeError, FileExistsError)):
        app.restore(backup, target)
    assert (target / 'runs/attempt-1.flt').read_text() == 'new independent research'


@pytest.mark.parametrize('operation', ['backup', 'restore'])
def test_embedded_knowledge_executor_blocks_offline_operation(tmp_path, operation):
    app = service()
    from great_agent.runtime.worker_lock import WorkerAlreadyRunning, WorkerLock
    settings, config = source(tmp_path)
    backup = tmp_path / 'backup'
    app.backup(settings, config, backup)
    target = tmp_path / 'blocked'
    knowledge_owner = Path(str(settings.database_path.resolve()) + '.knowledge')
    with WorkerLock(knowledge_owner):
        with pytest.raises(WorkerAlreadyRunning):
            if operation == 'backup':
                app.backup(settings, config, target)
            else:
                app.restore(backup, target)
    assert not target.exists()
    # The failed attempt must also release its solver lock.
    with WorkerLock(settings.database_path):
        pass
