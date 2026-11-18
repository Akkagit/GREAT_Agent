from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select, text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import (
    Base,
    DailyTaskModel,
    ExperimentModel,
    PlanRevisionModel,
    TaskAttemptModel,
    TaskEventModel,
)
from great_agent.db.repositories import TaskRepository
from great_agent.runtime.worker import Worker


@pytest.fixture
def quota_context(tmp_path: Path):
    database_path = tmp_path / "quotas.db"
    engine, sessions = create_engine_and_session_factory(database_path)
    Base.metadata.create_all(engine)
    yield database_path, sessions, TaskRepository(sessions)
    engine.dispose()


def add_experiment(sessions, *, concurrency: int, days=(1, 2, 3)) -> UUID:
    experiment_id, revision_id = str(uuid4()), str(uuid4())
    with sessions.begin() as session:
        experiment = ExperimentModel(id=experiment_id, status="ready")
        session.add(experiment)
        session.flush()
        session.add(PlanRevisionModel(
            id=revision_id, experiment_id=experiment_id, number=1,
            source="initial", spec_json=json.dumps({"concurrency": concurrency}),
            is_frozen=True,
        ))
        session.flush()
        experiment.current_revision_id = revision_id
        session.add_all(DailyTaskModel(
            id=str(uuid4()), experiment_id=experiment_id, revision_id=revision_id,
            year=2024, doy=day, status="pending",
        ) for day in days)
    return UUID(experiment_id)


def test_direct_claims_respect_experiment_limit_without_global_limit(quota_context):
    _, sessions, repository = quota_context
    experiment_id = add_experiment(sessions, concurrency=1)

    first = repository.claim_next("first")

    assert first is not None and first.experiment_id == experiment_id
    assert first.doy == 1
    assert repository.claim_next("second") is None
    assert [task.status for task in repository.list_tasks(experiment_id)] == [
        "running", "pending", "pending",
    ]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(TaskAttemptModel)) == 1
        assert session.scalar(select(func.count()).select_from(TaskEventModel)) == 1


def test_saturated_experiment_does_not_block_other_experiment(quota_context):
    _, sessions, repository = quota_context
    first_experiment = add_experiment(sessions, concurrency=1, days=(1, 2))
    other_experiment = add_experiment(sessions, concurrency=1, days=(3, 4))

    first = repository.claim_next("first")
    second = repository.claim_next("second")

    assert first is not None and first.experiment_id == first_experiment
    assert second is not None and second.experiment_id == other_experiment
    assert repository.claim_next("third") is None


def test_global_limit_includes_claims_from_other_repositories(quota_context):
    _, sessions, repository = quota_context
    add_experiment(sessions, concurrency=3, days=(1, 2, 3))
    add_experiment(sessions, concurrency=3, days=(4, 5))

    assert repository.claim_next("existing") is not None
    other_repository = TaskRepository(sessions)
    assert other_repository.claim_next("remaining", max_concurrency=2) is not None
    assert repository.claim_next("full", max_concurrency=2) is None
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel).where(
            DailyTaskModel.status == "running",
        )) == 2


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_terminal_attempt_releases_global_and_experiment_slot(quota_context, terminal):
    _, sessions, repository = quota_context
    add_experiment(sessions, concurrency=1)
    first = repository.claim_next("first", max_concurrency=1)
    assert first is not None
    assert repository.claim_next("blocked", max_concurrency=1) is None

    if terminal == "failed":
        repository.mark_attempt_failed(
            first.task_id, first.attempt_id, exit_code=1, error_code="solver_exit_error",
        )
    else:
        getattr(repository, f"mark_attempt_{terminal}")(
            first.task_id, first.attempt_id, exit_code=0,
        )

    second = repository.claim_next("replacement", max_concurrency=1)
    assert second is not None and second.doy == 2
    assert repository.get_task(first.task_id).active_attempt.status == terminal
    assert repository.list_events(first.task_id)[-1].payload["status"] == terminal


def test_experiment_limit_uses_task_frozen_revision_not_current_revision(quota_context):
    _, sessions, repository = quota_context
    experiment_id = add_experiment(sessions, concurrency=1)
    with sessions.begin() as session:
        revision_id = str(uuid4())
        session.add(PlanRevisionModel(
            id=revision_id, experiment_id=str(experiment_id), number=2,
            source="edit", spec_json='{"concurrency": 8}', is_frozen=False,
        ))
        session.flush()
        session.get(ExperimentModel, str(experiment_id)).current_revision_id = revision_id

    assert repository.claim_next("first") is not None
    assert repository.claim_next("blocked") is None


@pytest.mark.parametrize("quota", ["experiment", "global"])
def test_independent_repositories_compete_for_last_slot(quota_context, quota):
    database_path, sessions, repository = quota_context
    add_experiment(sessions, concurrency=2 if quota == "experiment" else 3)
    assert repository.claim_next("existing") is not None
    barrier = Barrier(2)

    def compete(worker_id):
        # Each competitor owns an engine/connection pool to the same real SQLite file.
        engine, competing_sessions = create_engine_and_session_factory(database_path)
        try:
            competitor = TaskRepository(competing_sessions)
            barrier.wait(timeout=5)
            if quota == "global":
                return competitor.claim_next(worker_id, max_concurrency=2)
            return competitor.claim_next(worker_id)
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(compete, ["competitor-a", "competitor-b"]))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel).where(
            DailyTaskModel.status == "running",
        )) == 2
        assert session.scalar(select(func.count()).select_from(TaskAttemptModel)) == 2
        assert session.scalar(select(func.count()).select_from(TaskEventModel)) == 2


def test_worker_passes_its_configured_global_limit(quota_context, monkeypatch):
    _, _, repository = quota_context
    original_claim = repository.claim_next

    def check_limit(worker_id, *, max_concurrency=None, owner_session_id=None):
        assert max_concurrency == 3
        assert owner_session_id == worker.owner_session_id
        assert str(UUID(owner_session_id)) == owner_session_id
        return original_claim(worker_id, max_concurrency=max_concurrency,
                              owner_session_id=owner_session_id)

    monkeypatch.setattr(repository, "claim_next", check_limit)
    worker = Worker(
        repository=repository, plan_service=None, adapter=None, run_storage=None,
        process_runner=None, result_inspector=None, max_concurrency=3,
    )
    # No tasks: this coroutine returns before any event loop or solver is needed.
    run = worker.run_once()
    try:
        with pytest.raises(StopIteration) as result:
            run.send(None)
        assert result.value.value is False
    finally:
        run.close()


def test_claim_waits_for_sqlite_writer_and_creates_one_attempt(quota_context):
    database_path, sessions, repository = quota_context
    experiment_id = add_experiment(sessions, concurrency=1)
    engine, competing_sessions = create_engine_and_session_factory(database_path)
    entering_write = Event()

    @event.listens_for(engine, "before_cursor_execute")
    def observe_write(connection, cursor, statement, parameters, context, executemany):
        if statement == "BEGIN IMMEDIATE":
            entering_write.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with sessions.begin() as blocking_session:
                blocking_session.execute(text("BEGIN IMMEDIATE"))
                claim_future = pool.submit(
                    TaskRepository(competing_sessions).claim_next,
                    "waiting-worker", max_concurrency=1,
                )
                assert entering_write.wait(timeout=5)
                assert not claim_future.done()
            claim = claim_future.result(timeout=5)
        assert claim is not None and claim.experiment_id == experiment_id
        assert len(repository.list_attempts(claim.task_id)) == 1
        assert len(repository.list_events(claim.task_id)) == 1
        assert repository.claim_next("full", max_concurrency=1) is None
    finally:
        engine.dispose()


def test_claim_rolls_back_slot_and_attempt_when_event_write_fails(quota_context, monkeypatch):
    _, sessions, repository = quota_context
    experiment_id = add_experiment(sessions, concurrency=1)

    def reject_event(*args):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(repository, "_publish_event", reject_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        repository.claim_next("failed-transaction", max_concurrency=1)

    assert all(task.status == "pending" for task in repository.list_tasks(experiment_id))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(TaskAttemptModel)) == 0
        assert session.scalar(select(func.count()).select_from(TaskEventModel)) == 0
    assert TaskRepository(sessions).claim_next("replacement", max_concurrency=1) is not None
