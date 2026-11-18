from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from great_agent.db.repositories import TaskRepository
from test_worker import repository_context, worker_factory  # noqa: F401


async def test_solver_success_is_committed_before_analysis_starts(worker_factory, monkeypatch):
    worker, task, _ = worker_factory('success')
    observed = []

    def analyze(*args, **kwargs):
        stored = worker.repository.get_task(task.id)
        observed.append((stored.status, stored.active_attempt.exit_code,
                         len(stored.result_files(kind='flt'))))
        return ()

    monkeypatch.setattr('great_agent.services.result_analysis_service.generate_analysis', analyze)
    await worker.run_once()
    assert observed == [('succeeded', 0, 15)]


def test_solver_completion_and_analysis_enqueue_are_one_transaction(repository_context, monkeypatch):
    from great_agent.db.analysis_repository import AnalysisJobRepository
    sessions, _ = repository_context
    tasks = TaskRepository(sessions)
    claim = tasks.claim_next('solver', owner_session_id='owner')

    def broken(*args, **kwargs):
        raise RuntimeError('enqueue failed')

    monkeypatch.setattr(AnalysisJobRepository, 'enqueue_in_session', broken)
    with pytest.raises(RuntimeError, match='enqueue failed'):
        tasks.complete_solver(claim, (), analysis_config={'hold_seconds': 600})
    stored = tasks.get_task(claim.task_id)
    assert stored.status == 'running'
    assert stored.active_attempt.exit_code is None


def test_analysis_recovery_preserves_solver_and_never_creates_solver_attempt(repository_context):
    from great_agent.db.analysis_repository import AnalysisJobRepository
    sessions, _ = repository_context
    tasks = TaskRepository(sessions)
    claim = tasks.claim_next('solver', owner_session_id='owner')
    tasks.complete_solver(claim, (), analysis_config={'hold_seconds': 600})
    jobs = AnalysisJobRepository(sessions)
    job = jobs.claim('old-worker')
    assert job is not None
    assert jobs.recover('new-worker') == 1
    recovered = jobs.claim('new-worker')
    assert recovered.id == job.id
    assert recovered.attempt_id == claim.attempt_id
    assert tasks.get_task(claim.task_id).status == 'succeeded'
    assert len(tasks.list_attempts(claim.task_id)) == 1
    assert tasks.claim_next('solver') is None


def test_analysis_failure_retries_same_attempt_and_rejects_stale_owner(repository_context):
    from great_agent.db.analysis_repository import AnalysisJobRepository
    from great_agent.errors import TaskStateError
    sessions, _ = repository_context
    tasks = TaskRepository(sessions)
    claim = tasks.claim_next('solver', owner_session_id='owner')
    tasks.complete_solver(claim, (), analysis_config={'hold_seconds': 600})
    jobs = AnalysisJobRepository(sessions)
    job = jobs.claim('old')
    jobs.recover('new')
    with pytest.raises(TaskStateError):
        jobs.finish(job, status='completed')
    job = jobs.claim('new')
    jobs.finish(job, status='failed', error_message='invalid result')
    assert jobs.retry(claim.task_id, job.id)['status'] == 'pending'
    assert jobs.retry(claim.task_id, job.id)['status'] == 'pending'
    retried = jobs.claim('new')
    assert retried.attempt_id == claim.attempt_id
    assert tasks.get_task(claim.task_id).status == 'succeeded'
    assert len(tasks.list_attempts(claim.task_id)) == 1


async def test_analysis_after_process_loss_is_reclaimed_without_running_solver(worker_factory, monkeypatch, tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    from great_agent.db.analysis_repository import AnalysisJobRepository

    worker, task, _ = worker_factory('success')
    # Complete a real mock solver, deferring its analysis consumer.
    claim = worker.repository.claim_next('solver', owner_session_id=worker.owner_session_id)
    await worker._run_claimed(claim)
    marker = tmp_path / 'analysis-started'
    database = worker.repository._session_factory.kw['bind'].url.database
    script = ('import sys,time; from pathlib import Path; '
        'from great_agent.db.engine import create_engine_and_session_factory; '
        'from great_agent.db.analysis_repository import AnalysisJobRepository; '
        'engine,sessions=create_engine_and_session_factory(Path(sys.argv[1])); '
        'job=AnalysisJobRepository(sessions).claim("lost-process"); '
        'Path(sys.argv[2]).write_text(str(job.id)); time.sleep(60)')
    child = subprocess.Popen([sys.executable, '-c', script, database, str(marker)],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(.02)
        assert marker.exists(), 'analysis child never claimed its job'
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)
    jobs = AnalysisJobRepository(worker.repository._session_factory)
    assert jobs.recover(worker.owner_session_id) == 1
    await worker.analysis_worker.run_once()
    assert jobs.list_for_task(task.id)[0]['status'] == 'completed'
    assert worker.repository.get_task(task.id).status == 'succeeded'
    assert len(worker.repository.list_attempts(task.id)) == 1


async def test_analysis_completion_retries_persistence_without_recomputing(worker_factory, monkeypatch):
    from sqlalchemy.exc import OperationalError
    worker, task, _ = worker_factory('success')
    claim = worker.repository.claim_next('solver', owner_session_id=worker.owner_session_id)
    await worker._run_claimed(claim)
    completed = []
    original = worker.analysis_worker.repository.finish
    failures = [True, True]
    def compute(job):
        completed.append(job.id)
        return ()
    def finish(*args, **kwargs):
        if failures:
            failures.pop()
            raise OperationalError('finish', {}, RuntimeError('locked'))
        return original(*args, **kwargs)
    monkeypatch.setattr(worker.analysis_worker, '_analyze', compute)
    monkeypatch.setattr(worker.analysis_worker.repository, 'finish', finish)
    for _ in range(2):
        try:
            await worker.analysis_worker.run_once()
        except OperationalError:
            pass
    await worker.analysis_worker.run_once()
    assert worker.analysis_worker.repository.list_for_task(task.id)[0]['status'] == 'completed'
    assert len(completed) == 1
    assert len(worker.repository.list_attempts(task.id)) == 1


async def test_repeated_shutdown_cancellation_waits_for_analysis_thread(worker_factory, monkeypatch):
    import threading
    from types import SimpleNamespace
    worker, _, _ = worker_factory('success')
    started, release, exited = threading.Event(), threading.Event(), threading.Event()
    finished = []
    def compute(job):
        started.set()
        release.wait(3)
        exited.set()
        return ()
    monkeypatch.setattr(worker.analysis_worker.repository, 'claim', lambda owner: SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(worker.analysis_worker.repository, 'finish', lambda *args, **kwargs: finished.append(kwargs['status']))
    monkeypatch.setattr(worker.analysis_worker, '_analyze', compute)
    operation = asyncio.create_task(worker.analysis_worker.run_once())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        operation.cancel()
        await asyncio.sleep(.01)
        operation.cancel()
        await asyncio.sleep(.01)
        assert not operation.done(), 'cannot release WorkerLock while its analysis thread still writes'
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
    assert exited.is_set()
    assert finished == ['completed']


async def test_continuous_scheduler_fills_short_slot_before_long_task_finishes(worker_factory, monkeypatch):
    from dataclasses import replace
    worker, _, _ = worker_factory('success')
    claim = worker.repository.claim_next('seed', owner_session_id=worker.owner_session_id)
    worker.max_concurrency = 2
    claims = [replace(claim, task_id=uuid4()) for _ in range(3)]
    waiting = list(claims)
    started = []
    long_done = asyncio.Event()
    third_started = asyncio.Event()

    def next_claim(*args, **kwargs):
        return waiting.pop(0) if waiting else None

    async def run(claimed):
        started.append(claimed.task_id)
        if claimed.task_id == claims[1].task_id:
            await long_done.wait()
        elif claimed.task_id == claims[2].task_id:
            third_started.set()
        else:
            await asyncio.sleep(.01)

    monkeypatch.setattr(worker.repository, 'claim_next', next_claim)
    monkeypatch.setattr(worker, '_run_claimed', run)
    loop = asyncio.create_task(worker._run_claim_loop(.005))
    try:
        await asyncio.wait_for(third_started.wait(), timeout=.5)
        assert not long_done.is_set()
        assert started == [c.task_id for c in claims]
    finally:
        long_done.set()
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
