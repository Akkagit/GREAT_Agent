"""Sequential release gate. No installs, live configuration, or publishing.

Run via verify.ps1, or `python scripts/verify.py --report <path>`.
Exit codes: first child failure, 124 timeout, 127 missing tool, 1 invalid build.
The JSON report and per-command logs are retained; the isolated build is removed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import uuid


def _local_host(host):
    if host is None or host in ('localhost', b'localhost'):
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except ValueError:
        return False


@contextmanager
def offline_network():
    """Keep mocked HTTP and local test sockets usable; refuse external networking."""
    def local_connect(original):
        def checked(sock, address, *args, **kwargs):
            # Unix-domain socket addresses are local filesystem paths.
            if isinstance(address, tuple) and not _local_host(address[0]):
                raise RuntimeError('offline verification forbids external connections')
            return original(sock, address, *args, **kwargs)
        return checked

    original_getaddrinfo = socket.getaddrinfo

    def local_getaddrinfo(host, *args, **kwargs):
        if not _local_host(host):
            raise RuntimeError('offline verification forbids external DNS lookups')
        return original_getaddrinfo(host, *args, **kwargs)

    with (
        patch.object(socket.socket, 'connect', local_connect(socket.socket.connect)),
        patch.object(socket.socket, 'connect_ex', local_connect(socket.socket.connect_ex)),
        patch.object(socket, 'getaddrinfo', local_getaddrinfo),
    ):
        yield


def run_command(command, *, cwd, env, stdout, stderr, timeout):
    """Run one command and reap its process tree if its deadline expires."""
    options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, **options)
    try:
        return subprocess.CompletedProcess(command, process.wait(timeout=timeout))
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            if os.name == 'nt':
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    stdout=stdout, stderr=stderr, check=False, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            print(f'Process-tree cleanup failed: {type(cleanup_error).__name__}; killing child', file=stdout, flush=True)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)
        raise


def _steps(root, python, npm, output):
    frontend = root / 'frontend'
    return [
        {'name': 'python-tests', 'cwd': root, 'command': [
            str(python), '-u', str(Path(__file__).resolve()), '--pytest-offline',
            'backend/tests', 'scripts/tests', '-q', '-p', 'pytest_asyncio.plugin',
            '-o', 'faulthandler_timeout=120',
            f'--basetemp={root / ".tmp" / ("v-" + uuid.uuid4().hex[:8])}',
        ]},
        {'name': 'frontend-tests', 'cwd': frontend, 'command': [npm, 'run', 'test', '--', '--run']},
        {'name': 'frontend-typecheck', 'cwd': frontend, 'command': [npm, 'run', 'typecheck']},
        {'name': 'frontend-lint', 'cwd': frontend, 'command': [npm, 'run', 'lint']},
        {'name': 'frontend-build', 'cwd': frontend, 'command': [
            npm, 'run', 'build', '--', '--outDir', str(output), '--emptyOutDir',
        ]},
    ]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_report(path, report):
    temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def _source_fingerprint(root):
    """Identify the tested working tree, including uncommitted code, not secrets."""
    paths = set()
    for directory in ('backend/src', 'backend/tests', 'frontend/src', 'scripts', 'config/agent', '.github/workflows'):
        base = root / directory
        if base.is_dir():
            paths.update(path for path in base.rglob('*') if path.is_file()
                and path.suffix in ('.py', '.ps1', '.ts', '.tsx', '.css', '.json', '.xml', '.md', '.yml', '.yaml')
                and '__pycache__' not in path.parts)
    for name in ('pyproject.toml', 'uv.lock', 'frontend/package.json', 'frontend/package-lock.json',
                 'frontend/vite.config.ts', 'frontend/tsconfig.json', 'frontend/tsconfig.app.json'):
        if (root / name).is_file():
            paths.add(root / name)
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    git_head, dirty = None, None
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, timeout=3, **options)
        if head.returncode == 0:
            git_head = head.stdout.strip()
            state = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                   cwd=root, capture_output=True, text=True, timeout=3, **options)
            if state.returncode == 0:
                dirty = bool(state.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {'git_head': git_head, 'dirty': dirty, 'sha256': digest, 'files': files}


def _build_fingerprint(output):
    files = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(output.rglob('*')) if path.is_file()}
    return {'index_sha256': files['index.html'], 'files': files,
            'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def verify(root, report_path=None, *, python=None, npm=None, timeout_seconds=900, run=run_command):
    """Stop at the first failed check and leave a machine-readable audit trail."""
    if timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be positive')
    root = Path(root).resolve(strict=True)
    if report_path is None:
        report_path = Path(tempfile.mkdtemp(prefix='great-agent-verify-report-')) / 'report.json'
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = report_path.parent / f'logs-{uuid.uuid4().hex}'
    log_dir.mkdir()
    python = python or sys.executable
    npm = str(npm or shutil.which('npm.cmd' if os.name == 'nt' else 'npm') or 'npm')
    env = dict(os.environ)
    for name in list(env):
        if name.upper().startswith('GREAT_AGENT_') or name.upper() in (
            'OPENAI_API_KEY', 'DASHSCOPE_API_KEY', 'ANTHROPIC_API_KEY',
        ):
            del env[name]
    env.update({'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONUNBUFFERED': '1', 'CI': 'true'})
    started = time.monotonic()
    exit_code = 0
    with tempfile.TemporaryDirectory(prefix='great-agent-verify-build-') as scratch:
        output = Path(scratch).resolve() / 'dist'
        # TMPDIR can be customized: never trust it to point away from live assets.
        if output.is_relative_to((root / 'frontend').resolve()):
            raise ValueError('temporary build output must be outside frontend')
        # Pytest creates --basetemp itself, but its parent must exist on a clean checkout.
        (root / '.tmp').mkdir(exist_ok=True)
        steps = _steps(root, python, npm, output)
        report = {
            'schema_version': 1, 'status': 'running', 'exit_code': None,
            'started_at': _now(), 'finished_at': None, 'duration_seconds': 0,
            'root': str(root), 'build_output': str(output), 'build_output_removed': False,
            'timeout_seconds': timeout_seconds,
            'source': _source_fingerprint(root), 'source_unchanged': None, 'build': None,
            'steps': [dict(step, cwd=str(step['cwd']), status='skipped', exit_code=None,
                           child_exit_code=None, duration_seconds=0, log=None) for step in steps],
        }
        _write_report(report_path, report)
        print(f'Verification report: {report_path}', flush=True)
        for index, step in enumerate(steps):
            result = report['steps'][index]
            log_path = log_dir / f'{index + 1:02}-{step["name"]}.log'
            result.update(status='running', log=str(log_path), started_at=_now())
            _write_report(report_path, report)
            print(f'[{index + 1}/{len(steps)}] {step["name"]}; log: {log_path}', flush=True)
            command_started = time.monotonic()
            try:
                with log_path.open('w', encoding='utf-8') as log:
                    completed = run(step['command'], cwd=step['cwd'], env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout_seconds)
                result['child_exit_code'] = completed.returncode
                exit_code = completed.returncode
                result['status'] = 'passed' if exit_code == 0 else 'failed'
                if not exit_code and step['name'] == 'frontend-build' and not (output / 'index.html').is_file():
                    exit_code = 1
                    result.update(status='failed', error='build exited successfully without index.html')
                elif not exit_code and step['name'] == 'frontend-build':
                    report['build'] = _build_fingerprint(output)
            except subprocess.TimeoutExpired:
                exit_code = 124
                result.update(status='timed_out', error=f'command exceeded {timeout_seconds} seconds')
            except OSError as error:
                exit_code = 127
                result.update(status='error', error=f'{type(error).__name__}: {error}')
            except KeyboardInterrupt:
                exit_code = 130
                result.update(status='interrupted', error='verification interrupted')
            result.update(exit_code=exit_code, finished_at=_now(),
                          duration_seconds=round(time.monotonic() - command_started, 3))
            _write_report(report_path, report)
            print(f'  {result["status"]}: exit={exit_code}, {result["duration_seconds"]}s', flush=True)
            if exit_code:
                for remaining in report['steps'][index + 1:]:
                    remaining['reason'] = f'blocked by {step["name"]}'
                break
        report['source_unchanged'] = _source_fingerprint(root)['sha256'] == report['source']['sha256']
        if not report['source_unchanged']:
            if not exit_code:
                exit_code = 1
            report['error'] = 'Working sources changed during verification; run the gate again.'
        report.update(status='passed' if exit_code == 0 else 'failed', exit_code=exit_code,
                      finished_at=_now(), duration_seconds=round(time.monotonic() - started, 3))
    report['build_output_removed'] = True
    _write_report(report_path, report)
    print(f'Verification {report["status"]}; exit={exit_code}; report: {report_path}', flush=True)
    return exit_code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['--pytest-offline']:
        import pytest
        with offline_network():
            return int(pytest.main(argv[1:]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, help='JSON report (default: a new OS temp directory)')
    parser.add_argument('--timeout-seconds', type=float, default=900, help='deadline for each command')
    args = parser.parse_args(argv)
    return verify(Path(__file__).resolve().parents[1], args.report, timeout_seconds=args.timeout_seconds)


if __name__ == '__main__':
    raise SystemExit(main())
