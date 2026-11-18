from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from great_agent.db.models import TaskAttemptModel
from great_agent.db.repositories import TaskRepository
from great_agent.errors import TaskStateError
from test_worker import repository_context, worker_factory  # noqa: F401


def test_new_claim_persists_session_and_reserved_phase(repository_context):
    sessions, _ = repository_context
    repository = TaskRepository(sessions)
    claim = repository.claim_next('worker-1', owner_session_id='old-session')
    with sessions() as session:
        attempt = session.get(TaskAttemptModel, str(claim.attempt_id))
        assert attempt.owner_session_id == 'old-session'
        assert attempt.launch_phase == 'reserved'


def make_orphan(repository_context, *, phase='started', identity=True):
    from great_agent.db.recovery_repository import RecoveryRepository
    sessions, _ = repository_context
    tasks = TaskRepository(sessions)
    claim = tasks.claim_next('worker-1', owner_session_id='old-session')
    with sessions.begin() as session:
        attempt = session.get(TaskAttemptModel, str(claim.attempt_id))
        attempt.launch_phase = phase
        if phase == 'started':
            attempt.pid = 4242
        if identity:
            attempt.process_created_at = 123456789
            attempt.process_executable = str(Path('solver.exe').resolve())
            attempt.command_sha256 = 'a' * 64
    return tasks, RecoveryRepository(sessions), claim


class Inspector:
    def __init__(self, state='running', reason=None):
        self.state, self.reason = state, reason
        self.calls = []

    def inspect(self, attempt, *, stop=False):
        from great_agent.runtime.process_identity import ProcessObservation
        self.calls.append((attempt.pid, stop))
        return ProcessObservation('exited' if stop and self.state == 'running' else self.state,
                                  self.reason, stopped=stop and self.state == 'running')


async def test_live_orphan_remains_occupied_then_exit_interrupts_once(repository_context):
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context)
    inspector = Inspector()
    service = RecoveryService(recovery, owner_session_id='new-session', inspector=inspector)
    await service.run_once()
    assert tasks.get_task(claim.task_id).status == 'running'
    assert recovery.get(claim.task_id)['recovery_state'] == 'observing'
    assert tasks.claim_next('worker-2', max_concurrency=1) is None
    inspector.state = 'exited'
    await service.run_once()
    await service.run_once()
    task = tasks.get_task(claim.task_id)
    assert task.status == 'interrupted'
    assert task.active_attempt.exit_code is None
    assert task.active_attempt.error_code == 'worker_lost'
    terminal = [e for e in tasks.list_events(claim.task_id)
                if e.kind == 'task_status' and e.payload.get('status') == 'interrupted']
    assert len(terminal) == 1


@pytest.mark.parametrize('reason', ['access_denied', 'identity_missing', 'candidate_conflict'])
async def test_unknown_orphan_keeps_quota(repository_context, reason):
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context)
    await RecoveryService(recovery, owner_session_id='new', inspector=Inspector('blocked', reason)).run_once()
    assert tasks.get_task(claim.task_id).status == 'running'
    assert recovery.get(claim.task_id)['recovery_reason'] == reason


async def test_reserved_is_safe_to_interrupt_but_spawning_requires_probe(repository_context):
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context, phase='reserved', identity=False)
    inspector = Inspector('blocked', 'launch_unknown')
    await RecoveryService(recovery, owner_session_id='new', inspector=inspector).run_once()
    assert tasks.get_task(claim.task_id).status == 'interrupted'
    assert not inspector.calls


async def test_spawning_unknown_keeps_occupied_and_probes_again(repository_context):
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context, phase='spawning', identity=False)
    inspector = Inspector('blocked', 'launch_unknown')
    service = RecoveryService(recovery, owner_session_id='new', inspector=inspector)
    await service.run_once()
    await service.run_once()
    assert tasks.get_task(claim.task_id).status == 'running'
    assert len(inspector.calls) == 2


async def test_recovery_takeover_rejects_old_owner_and_stale_cas(repository_context):
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context)
    stale = recovery.candidates('new')[0]
    await RecoveryService(recovery, owner_session_id='new', inspector=Inspector()).run_once()
    assert recovery.apply(stale, owner_session_id='old-session', state='interrupted', reason='worker_lost') is False
    with pytest.raises(TaskStateError):
        tasks.mark_attempt_succeeded(claim.task_id, claim.attempt_id, exit_code=0, owner_session_id='old-session')


async def test_stop_is_persistent_idempotent_and_rejects_wrong_attempt(repository_context):
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context)
    request_id = uuid4()
    recovery.request_action(claim.task_id, claim.attempt_id, request_id, 'stop')
    recovery.request_action(claim.task_id, claim.attempt_id, request_id, 'stop')
    with pytest.raises(TaskStateError):
        recovery.request_action(claim.task_id, uuid4(), uuid4(), 'stop')
    inspector = Inspector()
    await RecoveryService(recovery, owner_session_id='new', inspector=inspector).run_once()
    assert tasks.get_task(claim.task_id).status == 'cancelled'
    assert inspector.calls == [(4242, True)]
    assert len([e for e in tasks.list_events(claim.task_id) if e.kind == 'cancel_requested']) == 1


def test_worker_lock_excludes_second_instance_and_releases(tmp_path):
    from great_agent.runtime.worker_lock import WorkerAlreadyRunning, WorkerLock
    path = tmp_path / 'state.db'
    with WorkerLock(path):
        with pytest.raises(WorkerAlreadyRunning):
            with WorkerLock(path):
                pytest.fail('second worker acquired lock')
    with WorkerLock(path):
        pass


async def test_start_callback_failure_terminates_owned_process(tmp_path):
    import psutil
    import sys
    from great_agent.runtime.process_runner import ProcessRunner
    runner = ProcessRunner(cancel_grace_seconds=.2)
    seen = []
    def callback(pid):
        seen.append(pid)
        raise RuntimeError('database failure')
    with pytest.raises(RuntimeError, match='database failure'):
        executable = str(Path(sys.base_prefix) / ('python.exe' if __import__('os').name == 'nt' else 'bin/python3'))
        await runner.run(uuid4(), (executable, '-c', 'import time; time.sleep(60)'),
                         cwd=tmp_path, log_path=tmp_path / 'solver.log', on_started=callback)
    assert seen and not psutil.pid_exists(seen[0])
    assert not runner._active


def test_inspector_pid_reuse_never_stops_other_process():
    from great_agent.runtime.process_identity import ProcessIdentity, ProcessInspector
    from types import SimpleNamespace
    identity = ProcessIdentity(42, 222, 'solver', 'a' * 64)
    class Handle:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def identity(self): return identity
        def exited(self): return False
        def terminate_and_wait(self): pytest.fail('reused PID must never be terminated')
    expected = SimpleNamespace(pid=42, process_created_at=111, process_executable='solver',
        command_sha256='a' * 64, launch_phase='started')
    observed = ProcessInspector(open_process=lambda pid, stop: Handle()).inspect(expected, stop=True)
    assert observed.state == 'exited'
    assert not observed.stopped


def test_inspector_missing_identity_keeps_live_process_occupied():
    from great_agent.runtime.process_identity import ProcessIdentity, ProcessInspector
    from types import SimpleNamespace
    class Handle:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def identity(self): return ProcessIdentity(42, 222, 'solver', 'a' * 64)
        def exited(self): return False
        def terminate_and_wait(self): pytest.fail('missing identity must never be terminated')
    expected = SimpleNamespace(pid=42, process_created_at=None, process_executable=None,
        command_sha256=None, launch_phase='legacy_unknown')
    observed = ProcessInspector(open_process=lambda pid, stop: Handle()).inspect(expected, stop=True)
    assert observed.state == 'blocked'
    assert observed.reason == 'identity_missing'


def test_recovery_summary_hides_stop_until_identity_is_trusted(repository_context):
    tasks, recovery, claim = make_orphan(repository_context, identity=False)
    attempt = recovery.candidates('new')[0]
    recovery.apply(attempt, owner_session_id='new', state='blocked', reason='identity_missing')
    summary = recovery.get(claim.task_id)
    assert summary['allowed_actions'] == ['recheck']
    assert summary['recovery']['version'] > 0
    assert summary['recovery']['state'] == 'blocked'


async def test_recovery_loop_retries_transient_database_failure():
    from sqlalchemy.exc import OperationalError
    from great_agent.runtime.recovery_service import RecoveryService
    finished = asyncio.Event()
    class Repository:
        calls = 0
        def candidates(self, owner):
            self.calls += 1
            if self.calls == 1:
                raise OperationalError('select', {}, RuntimeError('database is locked'))
            asyncio_loop.call_soon_threadsafe(finished.set)
            return ()
    asyncio_loop = asyncio.get_running_loop()
    repository = Repository()
    service = RecoveryService(repository, owner_session_id='new', interval_seconds=.001)
    job = asyncio.create_task(service.run_forever())
    try:
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert repository.calls >= 2
    finally:
        job.cancel()
        with __import__('contextlib').suppress(asyncio.CancelledError):
            await job


def test_malformed_old_approval_cannot_crash_candidate_scan(repository_context):
    from great_agent.db.models import ExecutionApprovalModel, ExecutionPreviewModel, utc_now
    tasks, recovery, claim = make_orphan(repository_context, phase='spawning')
    sessions, _ = repository_context
    with sessions.begin() as session:
        preview_id = str(uuid4())
        session.add(ExecutionPreviewModel(id=preview_id, experiment_id=str(claim.experiment_id),
            revision_id=str(claim.revision_id), payload_json='{broken', confirmed_at=utc_now()))
        session.flush()
        session.add(ExecutionApprovalModel(task_id=str(claim.task_id), attempt_number=1, preview_id=preview_id, job_index=0))
    assert recovery.candidates('new')[0].command is None


def test_old_pid_snapshot_recovery_cannot_mutate_session_owned_tasks(repository_context):
    tasks, recovery, claim = make_orphan(repository_context)
    tasks.recover_stale_tasks(set())
    assert tasks.get_task(claim.task_id).status == 'running'


def use_native_python(worker):
    import os
    import sys
    original = worker._command_factory
    executable = str(Path(sys.base_prefix) / ('python.exe' if os.name == 'nt' else 'bin/python3'))
    worker._command_factory = lambda rendered, directory: (executable, *original(rendered, directory)[1:])


async def test_start_persistence_database_failure_isolated_to_attempt(worker_factory, monkeypatch):
    import psutil
    from sqlalchemy.exc import OperationalError
    worker, task, _ = worker_factory('slow')
    use_native_python(worker)
    seen = []
    def failed_record(task_id, attempt_id, pid, **kwargs):
        seen.append(pid)
        raise OperationalError('update', {}, RuntimeError('database is locked'))
    monkeypatch.setattr(worker.repository, 'record_pid', failed_record)
    await worker.run_once()
    attempt = worker.repository.get_task(task.id).active_attempt
    assert attempt.status == 'failed'
    assert attempt.error_code == 'launch_error'
    assert seen and not psutil.pid_exists(seen[0])


async def test_cancellation_watcher_retries_database_read_failure(worker_factory, monkeypatch):
    from sqlalchemy.exc import OperationalError
    worker, task, _ = worker_factory('slow')
    use_native_python(worker)
    started = asyncio.Event()
    original_run = worker.process_runner.run
    async def observe_start(*args, on_started, **kwargs):
        def after_started(pid):
            on_started(pid)
            started.set()
        return await original_run(*args, on_started=after_started, **kwargs)
    monkeypatch.setattr(worker.process_runner, 'run', observe_start)
    calls = []
    def requested(task_id, attempt_id):
        if not started.is_set():
            return False
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError('select', {}, RuntimeError('database is locked'))
        return True
    monkeypatch.setattr(worker.repository, 'cancellation_requested', requested)
    running = asyncio.create_task(worker.run_once())
    try:
        # The five-second cancellation contract starts after the child and its
        # identity have been persisted, not before XML/DB/cold Windows startup.
        await asyncio.wait_for(started.wait(), timeout=30)
        await asyncio.wait_for(running, timeout=5)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    assert worker.repository.get_task(task.id).status == 'cancelled'
    assert len(calls) >= 2


@pytest.mark.parametrize('terminal', ['interrupted', 'cancelled'])
def test_recovery_terminal_closes_running_analysis_in_same_event_transaction(repository_context, terminal):
    from great_agent.db.monitor_repository import MonitorRepository
    from great_agent.runtime.result_inspector import ResultFileInfo
    tasks, recovery, claim = make_orphan(repository_context)
    sessions, _ = repository_context
    tasks.add_result_files(claim.task_id, claim.attempt_id, (ResultFileInfo(None, 'analysis', Path('kept.json'), 4),))
    with sessions.begin() as session:
        session.get(TaskAttemptModel, str(claim.attempt_id)).analysis_status = 'running'
    attempt = recovery.candidates('new')[0]
    assert recovery.apply(attempt, owner_session_id='new', state=terminal, reason='worker_lost')
    snapshot = MonitorRepository(sessions).snapshot(claim.experiment_id)
    assert snapshot['tasks'][0]['analysis_status'] == 'failed'
    assert len(tasks.list_attempt_results(claim.task_id, claim.attempt_id)) == 1
    failure = [event for event in tasks.list_events(claim.task_id) if event.kind == 'analysis_failed']
    assert len(failure) == 1
    assert failure[0].payload['reason'] == 'analysis_interrupted'
    assert failure[0].payload['attempt_id'] == str(claim.attempt_id)


@pytest.mark.parametrize('analysis_status', ['completed', 'failed', 'skipped'])
def test_recovery_preserves_completed_analysis_status(repository_context, analysis_status):
    tasks, recovery, claim = make_orphan(repository_context)
    sessions, _ = repository_context
    with sessions.begin() as session:
        session.get(TaskAttemptModel, str(claim.attempt_id)).analysis_status = analysis_status
    recovery.apply(recovery.candidates('new')[0], owner_session_id='new', state='interrupted', reason='worker_lost')
    with sessions() as session:
        assert session.get(TaskAttemptModel, str(claim.attempt_id)).analysis_status == analysis_status
    assert not [event for event in tasks.list_events(claim.task_id) if event.kind == 'analysis_failed']


async def test_same_result_recheck_acknowledged_once_after_probe(repository_context):
    from great_agent.db.monitor_repository import MonitorRepository
    from great_agent.runtime.recovery_service import RecoveryService
    tasks, recovery, claim = make_orphan(repository_context)
    inspector = Inspector('blocked', 'access_denied')
    service = RecoveryService(recovery, owner_session_id='new', inspector=inspector)
    await service.run_once()
    request_id = uuid4()
    recovery.request_action(claim.task_id, claim.attempt_id, request_id, 'recheck')
    assert not [event for event in tasks.list_events(claim.task_id) if event.kind == 'recovery_checked']
    sessions, _ = repository_context
    monitor = MonitorRepository(sessions)
    cursor = monitor.snapshot(claim.experiment_id)['cursor']
    await service.run_once()
    checks = [event for event in tasks.list_events(claim.task_id) if event.kind == 'recovery_checked']
    assert len(checks) == 1
    assert checks[0].payload['request_id'] == str(request_id)
    assert checks[0].payload['last_checked_at']
    assert checks[0].payload['version'] > 0
    replay = monitor.events(claim.experiment_id, cursor)
    checked = [event for event in replay if event['type'] == 'recovery.changed']
    assert len(checked) == 1
    assert checked[0]['data']['task']['recovery']['last_checked_at'] == checks[0].payload['last_checked_at']
    recovery.request_action(claim.task_id, claim.attempt_id, request_id, 'recheck')
    await service.run_once()
    assert len(inspector.calls) == 3
    assert len([event for event in tasks.list_events(claim.task_id) if event.kind == 'recovery_checked']) == 1


class CleanupBlockedProcess:
    pid = 999999999
    returncode = None
    allow_cleanup = False
    def terminate(self):
        if not self.allow_cleanup:
            raise PermissionError('cannot terminate owned child')
        self.returncode = 1
    def kill(self):
        self.terminate()
    async def wait(self):
        if self.returncode is None:
            raise OSError('cannot confirm child exit')
        return self.returncode


async def test_cleanup_uncertain_retains_owned_process_and_error_evidence(tmp_path, monkeypatch):
    from great_agent.runtime.process_runner import ProcessRunner
    process = CleanupBlockedProcess()
    async def create(*args, **kwargs): return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    runner = ProcessRunner()
    key = uuid4()
    def started(pid): raise RuntimeError('identity persistence failed')
    with pytest.raises(Exception) as error:
        await runner.run(key, ('mock-solver',), cwd=tmp_path, log_path=tmp_path / 'solver.log', on_started=started)
    assert type(error.value).__name__ == 'ProcessCleanupError'
    assert error.value.process is process
    assert runner._active[key] is process


async def test_uncertain_cleanup_keeps_quota_until_owned_handle_retry_succeeds(worker_factory, monkeypatch):
    from great_agent.runtime.process_identity import ProcessIdentity
    worker, task, _ = worker_factory('slow')
    process = CleanupBlockedProcess()
    async def create(*args, **kwargs): return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', create)
    monkeypatch.setattr('great_agent.runtime.worker.capture_identity',
                        lambda pid: ProcessIdentity(pid, 123, 'mock-solver', 'a' * 64))
    def failed_record(*args, **kwargs): raise RuntimeError('identity persistence failed')
    monkeypatch.setattr(worker.repository, 'record_pid', failed_record)
    await worker.run_once()
    assert worker.repository.get_task(task.id).status == 'running'
    assert worker.repository.claim_next('other', max_concurrency=1) is None
    assert task.id in worker.process_runner._active
    await worker.recovery_service.run_once()
    assert worker.repository.get_task(task.id).status == 'running'
    process.allow_cleanup = True
    await worker.recovery_service.run_once()
    assert worker.repository.get_task(task.id).status == 'failed'
    assert task.id not in worker.process_runner._active


async def test_worker_startup_retries_initial_recovery_database_error(worker_factory, monkeypatch):
    from sqlalchemy.exc import OperationalError
    worker, task, _ = worker_factory('success')
    worker.recovery_service.interval_seconds = .001
    claims_started = asyncio.Event()
    calls = []
    def candidates(owner):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError('select', {}, RuntimeError('database is locked'))
        return ()
    def claim_next(*args, **kwargs):
        claims_started.set()
        return None
    monkeypatch.setattr(worker.recovery_service.repository, 'candidates', candidates)
    monkeypatch.setattr(worker.repository, 'claim_next', claim_next)
    job = asyncio.create_task(worker.run_forever(idle_seconds=.001))
    try:
        await asyncio.wait_for(claims_started.wait(), timeout=3)
        assert len(calls) >= 2
    finally:
        job.cancel()
        with __import__('contextlib').suppress(asyncio.CancelledError):
            await job


def test_inspector_checks_actual_executable_against_approved_executable():
    from great_agent.runtime.process_identity import ProcessIdentity, ProcessInspector, command_sha256
    from types import SimpleNamespace
    approved = (str(Path('approved-solver.exe').resolve()), '-x', 'attempt.xml')
    observed_exe = str(Path('other-solver.exe').resolve())
    digest = command_sha256(approved)
    stopped = []
    class Handle:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def identity(self): return ProcessIdentity(42, 222, observed_exe, digest)
        def exited(self): return False
        def terminate_and_wait(self): stopped.append(True); return True
    expected = SimpleNamespace(pid=42, process_created_at=222, process_executable=observed_exe,
        command_sha256=digest, launch_phase='started', command=approved)
    observed = ProcessInspector(open_process=lambda pid, stop: Handle()).inspect(expected, stop=True)
    assert observed.state == 'blocked'
    assert observed.reason == 'approval_identity_mismatch'
    assert not stopped
