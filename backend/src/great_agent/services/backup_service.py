"""Complete, offline runtime snapshots. Never restore over an existing runtime.

Stop every API and Worker using these resources before invoking this module.
Backups contain the original configuration and must be kept private. Reports
contain only paths, counts and integrity results, never configuration values.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import sys
import tomllib

import psutil

from great_agent.runtime.worker_lock import WorkerAlreadyRunning, WorkerLock


FORMAT_VERSION = 1
INDEX_POLICY = {
    'strategy': 'rebuild',
    'originals': 'knowledge/blobs',
    'preserved': 'entire knowledge tree, including reviewed formula caches',
    'parsed_content': 'SQLite; no separate export required',
    'versions': 'preserve SQLite parser/index metadata; use the same installed application and embedding model versions',
}


def _path(value):
    """Check ancestors before resolving so links cannot disappear in resolve()."""
    absolute = Path(os.path.abspath(value))
    for candidate in (absolute, *absolute.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Symbolic link or reparse point path is forbidden')
    return absolute.resolve()


def _overlaps(first, second):
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def _resources(settings):
    database = _path(settings.database_path)
    project = _path(getattr(settings, 'project_root', database.parent.parent))
    knowledge = getattr(getattr(settings, 'knowledge', None), 'root', None)
    return {'database': database, 'runs': _path(settings.run_root),
            'knowledge': _path(knowledge or project / 'data/knowledge'), 'project': project}


def _validate_layout(resources, config, destination):
    sources = [resources[name] for name in ('database', 'runs', 'knowledge')] + [config]
    for index, first in enumerate(sources):
        for second in sources[index + 1:]:
            if _overlaps(first, second):
                raise RuntimeError('Source resource paths overlap')
    if any(_overlaps(destination, path) for path in sources):
        raise RuntimeError('Destination must be outside source resources; paths overlap')


def _empty_destination(destination):
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise RuntimeError('Destination must be a new or empty directory')


def _assert_offline(resources):
    """Read only resource fields from recognized app processes' configurations."""
    ours = [resources[key] for key in ('database', 'runs', 'knowledge')]
    for process in psutil.process_iter(['pid', 'cmdline']):
        if process.pid == os.getpid():
            continue
        try:
            args = process.info['cmdline'] or []
            recognized = ('great_agent.cli' in args or any(Path(arg).name.lower() in
                          ('great-agent', 'great-agent.exe', 'great_agent.exe') for arg in args[:2]))
            if not recognized or not any(arg in ('api', 'worker') for arg in args):
                continue
            value = next((arg.split('=', 1)[1] for arg in args if arg.startswith('--config=')), None)
            if '--config' in args:
                value = args[args.index('--config') + 1]
            cwd = Path(process.cwd())
            candidate = Path(value) if value else Path('config/local.toml')
            candidate = candidate if candidate.is_absolute() else cwd / candidate
            raw = tomllib.loads(candidate.read_text(encoding='utf-8'))
            project = (cwd / raw['project_root']).resolve()
            their = [(cwd / raw[key]).resolve() for key in ('database_path', 'run_root')]
            their.append((cwd / raw.get('knowledge', {}).get('root', project / 'data/knowledge')).resolve())
            if project == resources['project'] or any(_overlaps(a, b) for a in ours for b in their):
                raise RuntimeError('Active API/Worker shares snapshot resources; stop services first')
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError, ValueError, KeyError, IndexError):
            raise RuntimeError('Cannot safely inspect an application process; stop services first') from None


def _relative(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or c in '\\:<>"|?*' for c in value):
        raise RuntimeError('Invalid manifest path')
    parts = value.split('/')
    if any(part in ('', '.', '..') or part.endswith(('.', ' ')) or
           re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part) for part in parts):
        raise RuntimeError('Invalid manifest path')
    relative = PurePosixPath(value)
    if relative.is_absolute() or (len(parts) == 1 and value not in ('config.toml', 'database.sqlite3')) or (
            len(parts) > 1 and parts[0] not in ('runs', 'knowledge')):
        raise RuntimeError('Invalid manifest path')
    return relative


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return {'size_bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def _tree(root):
    if not root.exists():
        return {}
    if not root.is_dir():
        raise RuntimeError('Snapshot tree must be a directory')
    files = {}
    for directory, subdirectories, names in os.walk(root, followlinks=False):
        for name in subdirectories + names:
            candidate = _path(Path(directory) / name)
            if not candidate.is_relative_to(root):
                raise RuntimeError('Snapshot path escapes its root')
            if name in names:
                if not stat.S_ISREG(candidate.stat().st_mode):
                    raise RuntimeError('Snapshot contains a nonregular file')
                relative = candidate.relative_to(root).as_posix()
                files[relative] = _digest(candidate)
    return files


def _inventory(resources, config):
    result = {'config.toml': _digest(config)}
    for key in ('runs', 'knowledge'):
        result.update({f'{key}/{relative}': record for relative, record in _tree(resources[key]).items()})
    return result


def _database_checks(path):
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('SQLite integrity check failed')
            if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise RuntimeError('SQLite foreign key check failed')
    except sqlite3.Error:
        raise RuntimeError('SQLite integrity check failed') from None


def _run_relative(value, original_runs):
    try:
        relative = Path(value).relative_to(original_runs).as_posix()
    except (TypeError, ValueError):
        raise RuntimeError('Historical artifact path lies outside run_root') from None
    _relative('runs/' + relative)
    return relative


def _reference_checks(database, directory, original_runs):
    """A valid DB alone cannot prove that its original documents survived."""
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'knowledge_blobs' in tables:
            for relative, digest, size in connection.execute('SELECT original_relpath, sha256, size_bytes FROM knowledge_blobs'):
                _relative('knowledge/' + relative)
                original = _path(directory / 'knowledge' / relative)
                if not relative.startswith('blobs/') or not original.is_file() or _digest(original) != {'sha256': digest, 'size_bytes': size}:
                    raise RuntimeError('Knowledge original is missing or does not match its reference')
        for table, column in (('result_files', 'path'), ('task_attempts', 'log_path')):
            if table not in tables:
                continue
            for (value,) in connection.execute(f'SELECT {column} FROM {table} WHERE {column} IS NOT NULL'):
                relative = _run_relative(value, original_runs)
                if not _path(directory / 'runs' / relative).is_file():
                    raise RuntimeError('Historical artifact or log reference is missing')


def _runtime_versions():
    result = {'python': sys.version.split()[0]}
    for name in ('great-agent', 'qdrant-client', 'pdfplumber', 'onnxruntime', 'tokenizers'):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = 'not-installed'
    return result


def backup(settings, config, destination, *, allow_missing_database=False):
    resources = _resources(settings)
    config, destination = _path(config), _path(destination)
    _validate_layout(resources, config, destination)
    _empty_destination(destination)
    if not config.is_file():
        raise RuntimeError('Configuration file is missing')
    database_exists = resources['database'].is_file()
    if not database_exists and not allow_missing_database:
        raise RuntimeError('Database file is missing')
    _assert_offline(resources)
    knowledge_owner = Path(str(resources['database']) + '.knowledge')
    with WorkerLock(resources['database']), WorkerLock(knowledge_owner), ExitStack() as stack:
        _assert_offline(resources)
        if database_exists:
            # Reserve the writer slot while copying all files. A separate reader
            # makes the SQLite backup without backing up a write transaction.
            gate = stack.enter_context(sqlite3.connect(resources['database'], timeout=0))
            try:
                gate.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:
                raise RuntimeError('Database is busy; stop all writers before backup') from None
            _database_checks(resources['database'])
        before = _inventory(resources, config)
        for relative in before:
            _relative(relative)
        destination.mkdir(parents=True, exist_ok=True)
        for key in ('runs', 'knowledge'):
            (destination / key).mkdir()
        for relative in before:
            target = _path(destination / relative)
            if not target.is_relative_to(destination):
                raise RuntimeError('Backup target path escapes destination')
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == 'config.toml':
                source = config
            else:
                key, remainder = relative.split('/', 1)
                source = resources[key] / remainder
            shutil.copy2(source, target)
        if database_exists:
            with sqlite3.connect(resources['database'].as_uri() + '?mode=ro', uri=True) as source_db:
                with sqlite3.connect(destination / 'database.sqlite3') as target_db:
                    source_db.backup(target_db)
                    # A WAL-mode source transfers its journal mode. Make the
                    # snapshot self-contained and prevent verification readers
                    # from creating unmanifested -wal/-shm sidecars.
                    target_db.execute('PRAGMA journal_mode=DELETE')
            _database_checks(destination / 'database.sqlite3')
            _reference_checks(destination / 'database.sqlite3', destination, resources['runs'])
        if before != _inventory(resources, config):
            raise RuntimeError('Source files changed during snapshot; incomplete backup retained')
        files = dict(before)
        if database_exists:
            files['database.sqlite3'] = _digest(destination / 'database.sqlite3')
        for relative, expected in files.items():
            if _digest(_path(destination / relative)) != expected:
                raise RuntimeError('Copied snapshot checksum mismatch')
        manifest = {'format_version': FORMAT_VERSION,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'database_existed': database_exists,
                    'source_paths': {key: str(value) for key, value in resources.items()},
                    'index_policy': INDEX_POLICY, 'runtime_versions': _runtime_versions(), 'files': files}
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    return verify(destination)


def _strict_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeError('Duplicate manifest key')
        value[key] = item
    return value


def _manifest(directory):
    try:
        value = json.loads(_path(directory / 'manifest.json').read_text(encoding='utf-8'), object_pairs_hook=_strict_pairs)
    except (OSError, ValueError):
        raise RuntimeError('Invalid backup manifest') from None
    if (not isinstance(value, dict) or value.get('format_version') != FORMAT_VERSION or
            not isinstance(value.get('database_existed'), bool) or not isinstance(value.get('files'), dict) or
            not isinstance(value.get('source_paths'), dict) or set(value['source_paths']) != {'database', 'runs', 'knowledge', 'project'} or
            not all(isinstance(item, str) and Path(item).is_absolute() for item in value['source_paths'].values()) or
            value.get('index_policy') != INDEX_POLICY):
        raise RuntimeError('Invalid backup manifest structure')
    required = {'config.toml'} | ({'database.sqlite3'} if value['database_existed'] else set())
    if not required.issubset(value['files']) or ('database.sqlite3' in value['files']) != value['database_existed']:
        raise RuntimeError('Incomplete backup manifest')
    seen = set()
    for relative, record in value['files'].items():
        _relative(relative)
        normalized = relative.casefold()
        if normalized in seen:
            raise RuntimeError('Duplicate manifest path')
        seen.add(normalized)
        if (not isinstance(record, dict) or set(record) != {'size_bytes', 'sha256'} or
                type(record['size_bytes']) is not int or record['size_bytes'] < 0 or
                not isinstance(record['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise RuntimeError('Invalid manifest checksum record')
    return value


def verify(directory):
    directory = _path(directory)
    manifest = _manifest(directory)
    actual = _tree(directory)
    actual.pop('manifest.json', None)
    if set(actual) != set(manifest['files']):
        raise RuntimeError('Backup manifest file inventory mismatch')
    for relative, record in manifest['files'].items():
        if actual[relative] != record:
            raise RuntimeError('Backup file hash or size mismatch')
    if manifest['database_existed']:
        _database_checks(directory / 'database.sqlite3')
        _reference_checks(directory / 'database.sqlite3', directory, Path(manifest['source_paths']['runs']))
    return {'status': 'ok', 'file_count': len(actual), 'database_integrity': 'ok' if manifest['database_existed'] else 'absent',
            'index_strategy': 'rebuild'}


@contextmanager
def _existing_worker_lock(database):
    """Check the source lock without creating any original path during restore."""
    lock = _path(Path(str(database) + '.worker.lock'))
    if not lock.is_file():
        yield
        return
    with lock.open('r+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise WorkerAlreadyRunning('Active Worker prevents restore; stop services first') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _toml(raw):
    lines = []
    def emit(table, prefix=()):
        if prefix:
            lines.append('[' + '.'.join(json.dumps(key) for key in prefix) + ']')
        for key, value in table.items():
            if isinstance(value, dict):
                continue
            if not isinstance(value, (str, bool, int, float, list)):
                raise RuntimeError('Unsupported configuration value; restore requires manual config review')
            lines.append(json.dumps(key) + ' = ' + json.dumps(value, ensure_ascii=False))
        lines.append('')
        for key, value in table.items():
            if isinstance(value, dict):
                emit(value, (*prefix, key))
    emit(raw)
    return '\n'.join(lines)


def _relocate_history(database, original_runs, restored_runs):
    count = 0
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column in (('result_files', 'path'), ('task_attempts', 'log_path')):
            if table not in tables:
                continue
            for identifier, value in connection.execute(f'SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL').fetchall():
                target = str(restored_runs / _run_relative(value, original_runs))
                connection.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (target, identifier))
                count += 1
    return count


def restore(directory, destination, *, settings=None):
    directory, destination = _path(directory), _path(destination)
    if _overlaps(directory, destination):
        raise RuntimeError('Restore and backup paths overlap')
    _empty_destination(destination)
    verify(directory)
    manifest = _manifest(directory)
    if not manifest['database_existed']:
        raise RuntimeError('Backup has no database; use only for preinitialization inspection')
    resources = _resources(settings) if settings is not None else {key: _path(value) for key, value in manifest['source_paths'].items()}
    _validate_layout(resources, resources['project'] / 'config.toml', destination)
    if _overlaps(destination, resources['project']):
        raise RuntimeError('Restore destination overlaps the original project')
    _assert_offline(resources)
    try:
        raw = tomllib.loads((directory / 'config.toml').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise RuntimeError('Invalid saved configuration; review backup manually') from None
    raw.update(project_root=str(destination), database_path=str(destination / 'database.sqlite3'), run_root=str(destination / 'runs'))
    raw.setdefault('knowledge', {})['root'] = str(destination / 'knowledge')
    restored_config = _toml(raw)
    knowledge_owner = Path(str(resources['database']) + '.knowledge')
    with _existing_worker_lock(resources['database']), _existing_worker_lock(knowledge_owner):
        _assert_offline(resources)
        _empty_destination(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for name in ('runs', 'knowledge'):
            (destination / name).mkdir()
        for relative, expected in manifest['files'].items():
            source = _path(directory / relative)
            target = _path(destination / ('original-config.toml' if relative == 'config.toml' else relative))
            if not target.is_relative_to(destination):
                raise RuntimeError('Restore target path escapes destination')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as input_file, target.open('xb') as output_file:
                shutil.copyfileobj(input_file, output_file)
            if _digest(target) != expected:
                raise RuntimeError('Restored snapshot checksum mismatch')
        _database_checks(destination / 'database.sqlite3')
        relocated = _relocate_history(destination / 'database.sqlite3', Path(manifest['source_paths']['runs']), destination / 'runs')
        _database_checks(destination / 'database.sqlite3')
        _reference_checks(destination / 'database.sqlite3', destination, destination / 'runs')
        with (destination / 'config.toml').open('x', encoding='utf-8') as stream:
            stream.write(restored_config)
        report = {'status': 'ok', 'file_count': len(manifest['files']), 'database_integrity': 'ok',
                  'index_strategy': 'rebuild', 'destination': str(destination), 'relocated_history_paths': relocated,
                  'activation': 'manual; review restored configuration and confirm new execution previews'}
        with (destination / 'restore-report.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='operation', required=True)
    for name in ('backup', 'verify', 'restore'):
        command = subparsers.add_parser(name)
        if name != 'verify':
            command.add_argument('--destination', required=True, type=Path)
        if name != 'backup':
            command.add_argument('--backup', required=True, type=Path)
        command.add_argument('--config', type=Path, required=name == 'backup')
    args = parser.parse_args(argv)
    try:
        from great_agent.config import AppSettings
        settings = AppSettings.load(args.config, os.environ) if args.config else None
        if args.operation == 'backup':
            result = backup(settings, args.config, args.destination)
        elif args.operation == 'verify':
            result = verify(args.backup)
        else:
            result = restore(args.backup, args.destination, settings=settings)
    except Exception as error:
        # Pydantic/TOML errors can contain credentials; never print their text.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'message': 'Offline operation failed; check service state, paths and backup integrity.'}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
