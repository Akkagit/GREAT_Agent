"""Local release/start orchestration; no implicit dependency installation.

Only processes created here are terminated on failure. Runtime recovery is
manual: verify the complete recorded backup and restore to a new empty directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
from urllib.parse import urlsplit, unquote
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4
import webbrowser

import psutil
from great_agent.config import AppSettings
from great_agent.runtime.worker_lock import WorkerLock


def run_checked(command, *, cwd, log):
    with log.open('ab') as stream:
        result = subprocess.run(command, cwd=cwd, env=runtime_environment(), stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}); see {log}')


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and ((tag == 'script' and key == 'src') or (tag == 'link' and key == 'href')):
                self.paths.append(value)


def validate_dist(stage):
    index = stage / 'index.html'
    if not index.is_file() or not index.stat().st_size:
        raise RuntimeError(f'Build missing nonempty index.html: {stage}')
    parser = Assets()
    parser.feed(index.read_text(encoding='utf-8'))
    if not parser.paths:
        raise RuntimeError(f'Build missing frontend assets: {stage}')
    for value in parser.paths:
        url = urlsplit(value)
        if url.scheme or url.netloc:
            continue
        target = (stage / unquote(url.path).lstrip('/')).resolve()
        if not target.is_relative_to(stage.resolve()) or not target.is_file():
            raise RuntimeError(f'Build missing or unsafe asset: {value}')


def build_frontend(root, release, *, run=run_checked):
    release.mkdir(parents=True, exist_ok=True)
    frontend = root / 'frontend'
    stage = release / 'staged-dist'
    node = shutil.which('node') or 'node'
    # Equivalent to package.json's tsc -b && vite build; no shell interpolation.
    run([node, str(frontend / 'node_modules/typescript/bin/tsc'), '-b'],
        cwd=frontend, log=release / 'build.log')
    run([node, str(frontend / 'node_modules/vite/bin/vite.js'), 'build',
         '--outDir', str(stage), '--emptyOutDir'], cwd=frontend, log=release / 'build.log')
    validate_dist(stage)
    return stage


def switch_frontend(root, release, stage):
    dist = root / 'frontend/dist'
    previous = release / 'previous-dist'
    if dist.exists():
        dist.rename(previous)
    try:
        stage.rename(dist)
    except BaseException:
        if previous.exists():
            previous.rename(dist)
        raise


def rollback_frontend(root, release):
    dist = root / 'frontend/dist'
    if dist.exists():
        dist.rename(release / 'failed-dist')
    if (release / 'previous-dist').exists():
        (release / 'previous-dist').rename(dist)


def check_pid_files(pid_root):
    for path in pid_root.glob('*.pid'):
        try:
            pid = int(path.read_text().strip())
        except ValueError as error:
            raise RuntimeError(f'Invalid PID file; inspect manually: {path}') from error
        if psutil.pid_exists(pid):
            raise RuntimeError(f'PID {pid} in {path} is live; inspect/stop the known service before retrying.')


def assert_idle(settings, config, pid_root):
    check_pid_files(pid_root)
    with socket.socket() as probe:
        if probe.connect_ex((settings.host, settings.port)) == 0:
            raise RuntimeError(f'Port {settings.port} is occupied; no existing process was stopped.')
    for process in psutil.process_iter(['pid', 'cmdline']):
        if process.pid == os.getpid():
            continue
        args = process.info['cmdline'] or []
        app_process = ('great_agent.cli' in args or any(Path(arg).name.lower() in
                       ('great-agent.exe', 'great-agent', 'great_agent.exe') for arg in args[:2]))
        if app_process and any(arg in ('api', 'worker', 'research-worker') for arg in args):
            value = next((arg.partition('=')[2] for arg in args if arg.startswith('--config=')), None)
            if '--config' in args and args.index('--config') + 1 < len(args):
                value = args[args.index('--config') + 1]
            if value is None:
                raise RuntimeError(f'Cannot inspect app PID {process.pid} configuration; stop it first.')
            try:
                cwd = Path(process.cwd())
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = cwd / candidate
                if candidate.resolve() == config:
                    raise RuntimeError(f'Existing API/Worker PID {process.pid} uses {config}; stop it first.')
                # Only inspect resource paths; never copy/log another config's keys.
                raw = tomllib.loads(candidate.read_text(encoding='utf-8'))
                resources = [(cwd / Path(raw[key])).resolve() for key in
                             ('project_root', 'database_path', 'run_root')]
                ours = [settings.project_root.resolve(), settings.database_path.resolve(), settings.run_root.resolve()]
                if resources[0] == ours[0] or any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
                        for a in resources[1:] for b in ours[1:]):
                    raise RuntimeError(f'Existing API/Worker PID {process.pid} shares application resources; stop it first.')
            except psutil.NoSuchProcess:
                continue
            except (psutil.AccessDenied, OSError, ValueError, KeyError) as error:
                raise RuntimeError(f'Cannot safely inspect app PID {process.pid} resources; stop it first.') from None
    with WorkerLock(settings.database_path):
        pass


def snapshot(settings, config, destination):
    from great_agent.services.backup_service import backup
    return backup(settings, config, destination, allow_missing_database=True)


def migration_needed(settings):
    from sqlalchemy import create_engine
    from great_agent.db.schema import require_runtime_schema
    if not settings.database_path.exists():
        return True
    # The normal factory enables WAL, which would mutate history before backup.
    engine = create_engine('sqlite://', creator=lambda: sqlite3.connect(
        settings.database_path.resolve().as_uri() + '?mode=ro', uri=True))
    try:
        require_runtime_schema(engine)
        return False
    except RuntimeError:
        return True
    finally:
        engine.dispose()


def cli_command(command, config):
    # Windows venv python.exe is a redirector. Launch the real interpreter while
    # retaining venv prefix/site-packages via CPython's launcher environment.
    return [getattr(sys, '_base_executable', sys.executable), '-m', 'great_agent.cli', command, '--config', str(config)]


def runtime_environment():
    environ = os.environ.copy()
    if os.name == 'nt':
        environ['__PYVENV_LAUNCHER__'] = sys.executable
    return environ


def prepare_database(settings, config, release, *, run=run_checked):
    if not migration_needed(settings):
        return
    from great_agent.db.schema import upgrade_runtime_database
    print('Schema upgrade required; backing up DB, runs, knowledge and config before trial migration.', flush=True)
    backup = release / 'runtime-backup'
    snapshot(settings, config, backup)
    # Keep the pristine snapshot; trial migrations only ever modify a second copy.
    trial = release / 'trial.sqlite3'
    if (backup / 'database.sqlite3').exists():
        shutil.copy2(backup / 'database.sqlite3', trial)
    with WorkerLock(trial):
        upgrade_runtime_database(trial)
    print('Trial schema verified; invoking explicit CLI migrate.', flush=True)
    run(cli_command('migrate', config), cwd=settings.project_root, log=release / 'migration.log')
    if migration_needed(settings):
        raise RuntimeError('Migration returned without a valid runtime schema.')


def launch(command, root, log):
    with log.open('ab') as stream:
        child = subprocess.Popen(command, cwd=root, env=runtime_environment(), stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    child.owned_processes = []
    try:
        child.owned_processes.append(psutil.Process(child.pid))
        child.owned_processes[0].create_time()  # Cache identity before PID reuse.
    except psutil.NoSuchProcess:
        pass
    return child


def remember_descendants(child):
    if not child.owned_processes:
        return
    root = child.owned_processes[0]
    if root.is_running():  # psutil compares PID + cached creation time.
        try:
            for process in root.children(recursive=True):
                process.create_time()
                if process not in child.owned_processes:
                    child.owned_processes.append(process)
        except psutil.NoSuchProcess:
            pass


def terminate_owned(child):
    remember_descendants(child)
    processes = [process for process in reversed(child.owned_processes) if process.is_running()]
    for process in processes:
        try:
            process.terminate()  # psutil rechecks cached PID/creation-time identity.
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=10)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=5)
    child.wait(timeout=5)
    if alive:
        raise RuntimeError('Owned service processes did not stop; frontend rollback withheld.')


def await_health(children, base_url, expected_index, timeout):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    ready_since = None
    while time.monotonic() < deadline:
        for name, child in children.items():
            remember_descendants(child)
            if child.poll() is not None:
                raise RuntimeError(f'{name} exited with {child.returncode}; inspect its release log.')
        try:
            with opener.open(base_url + '/api/v1/health', timeout=2) as response:
                data = json.load(response)
                healthy = data.get('status') == 'ok' and data.get('mode') == 'local'
            readiness_url = base_url + '/api/v1/readiness'
            if 'worker' not in children:
                readiness_url += '?require_worker=false'
            with opener.open(readiness_url, timeout=2) as response:
                healthy = healthy and json.load(response).get('ready') is True
            with opener.open(base_url + '/', timeout=2) as response:
                healthy = healthy and response.read() == expected_index
            if healthy:
                ready_since = ready_since or time.monotonic()
                if time.monotonic() - ready_since >= 2:
                    return
            else:
                ready_since = None
        except (OSError, ValueError):
            ready_since = None
        time.sleep(0.25)
    raise RuntimeError(f'Health/page verification timed out after {timeout}s.')


def start(root, config, *, no_build=False, no_browser=False, api_only=False, health_timeout=60):
    settings = AppSettings.load(config, os.environ)
    if settings.project_root != root:
        raise RuntimeError('Config project_root must match this checkout (frontend served from that root).')
    pid_root = root / 'data/pids'
    # Separate launcher mutex; migrate itself acquires the existing WorkerLock.
    with WorkerLock(root / 'data/startup'):
        assert_idle(settings, config, pid_root)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
        release = root / 'frontend/.releases' / stamp
        release.mkdir(parents=True)
        print(f'Release/logs: {release}', flush=True)
        children = {}
        switched = False
        try:
            stage = None
            if not no_build:
                stage = build_frontend(root, release)
            else:
                validate_dist(root / 'frontend/dist')
            assert_idle(settings, config, pid_root)
            prepare_database(settings, config, release)
            if stage is not None:
                switch_frontend(root, release, stage)
                switched = True
            names = ('api',) if api_only else ('api', 'worker')
            if not api_only and settings.research.enabled:
                names += ('research-worker',)
            for name in names:
                children[name] = launch(cli_command(name, config), root, release / f'{name}.log')
                print(f'{name} starting PID={children[name].pid}', flush=True)
            base_url = f'http://{settings.host}:{settings.port}'
            await_health(children, base_url, (root / 'frontend/dist/index.html').read_bytes(), health_timeout)
            pid_root.mkdir(parents=True, exist_ok=True)
            for name, child in children.items():
                (pid_root / f'{name}.pid').write_text(str(child.pid), encoding='ascii')
            (release / 'processes.json').write_text(json.dumps({name: child.pid for name, child in children.items()}))
            print(f'GREAT Agent healthy: {base_url}/; logs: {release}', flush=True)
        except BaseException as error:
            for child in children.values():
                terminate_owned(child)
            if switched:
                rollback_frontend(root, release)
            (release / 'failure.txt').write_text(str(error), encoding='utf-8')
            print(f'Startup failed; logs/backups retained: {release}', file=sys.stderr, flush=True)
            raise
    if not no_browser:
        webbrowser.open(base_url + '/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config/local.toml')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-build', action='store_true', help='Reuse an already validated frontend dist.')
    parser.add_argument('--api-only', action='store_true', help='Do not start the solver worker.')
    parser.add_argument('--health-timeout', type=float, default=60)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = Path(args.config)
    config = (root / config).resolve() if not config.is_absolute() else config.resolve()
    try:
        start(root, config, no_build=args.no_build, no_browser=args.no_browser,
              api_only=args.api_only, health_timeout=args.health_timeout)
    except Exception as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
