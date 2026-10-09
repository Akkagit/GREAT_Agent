from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import (
    Base,
    DailyTaskModel,
    ExperimentModel,
    PlanRevisionModel,
)
from great_agent.db.repositories import TaskRepository
from great_agent.errors import TaskStateError
from great_agent.runtime.result_inspector import PppfltResultInspector


DEFAULT_STATIONS = (
    "AHB0", "BJFS", "CHAN", "DAEJ", "GAMG", "GMSD", "HKSL", "JFNG",
    "KUNM", "LHAZ", "NTUS", "SHAO", "URUM", "WUHN", "YELL",
)


@pytest.fixture
def repository_context(tmp_path: Path):
    engine, session_factory = create_engine_and_session_factory(tmp_path / "tasks.db")
    Base.metadata.create_all(engine)
    experiment_id = str(uuid4())
    revision_id = str(uuid4())
    task_id = str(uuid4())
    with session_factory.begin() as session:
        experiment = ExperimentModel(
            id=experiment_id, status="ready", current_revision_id=None
        )
        session.add(experiment)
        session.flush()
        revision = PlanRevisionModel(
            id=revision_id,
            experiment_id=experiment_id,
            number=1,
            source="initial",
            spec_json="{}",
            is_frozen=True,
        )
        session.add(revision)
        session.flush()
        experiment.current_revision_id = revision_id
        session.add(
            DailyTaskModel(
                id=task_id,
                experiment_id=experiment_id,
                revision_id=revision_id,
                year=2024,
                doy=350,
                status="pending",
                worker_id=None,
                active_attempt_id=None,
            )
        )
    return session_factory, task_id


def test_two_workers_cannot_claim_same_task(repository_context) -> None:
    session_factory, task_id = repository_context

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda worker: TaskRepository(session_factory).claim_next(worker),
                ["w1", "w2"],
            )
        )

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert str(claimed[0].task_id) == task_id
    assert claimed[0].attempt_number == 1
    assert TaskRepository(session_factory).get_task(claimed[0].task_id).status == "running"


def test_stale_running_task_becomes_interrupted(repository_context) -> None:
    session_factory, _ = repository_context
    repository = TaskRepository(session_factory)
    claim = repository.claim_next("worker-1")
    assert claim is not None
    repository.record_pid(claim.task_id, claim.attempt_id, 4242)

    repository.recover_stale_tasks(active_pids=set())

    task = repository.get_task(claim.task_id)
    assert task.status == "interrupted"
    assert task.active_attempt.status == "interrupted"


def test_stale_recovery_keeps_live_pid_and_emits_action_event(
    repository_context,
) -> None:
    session_factory, _ = repository_context
    repository = TaskRepository(session_factory)
    claim = repository.claim_next("worker-1")
    assert claim is not None
    repository.record_pid(claim.task_id, claim.attempt_id, 4242)

    repository.recover_stale_tasks(active_pids={4242})

    assert repository.get_task(claim.task_id).status == "running"
    events = repository.list_events(claim.task_id)
    assert events[-1].kind == "recovery_blocked"
    assert events[-1].payload["pid"] == 4242


def test_claim_transition_rejects_wrong_previous_state(repository_context) -> None:
    session_factory, _ = repository_context
    repository = TaskRepository(session_factory)
    claim = repository.claim_next("worker-1")
    assert claim is not None
    repository.record_pid(claim.task_id, claim.attempt_id, 4242)
    repository.recover_stale_tasks(active_pids=set())

    with pytest.raises(TaskStateError):
        repository.mark_attempt_succeeded(claim.task_id, claim.attempt_id, exit_code=0)


@pytest.fixture
def result_inspector() -> PppfltResultInspector:
    return PppfltResultInspector()


@pytest.fixture
def attempt_dir(tmp_path: Path) -> Path:
    attempt = tmp_path / "attempt-1"
    (attempt / "results").mkdir(parents=True)
    (attempt / "solver.log").write_text("solver output", encoding="utf-8")
    return attempt


def write_outputs(attempt_dir: Path, stations: tuple[str, ...]) -> None:
    for station in stations:
        (attempt_dir / "results" / f"{station}.flt").write_text(
            "solution", encoding="utf-8"
        )


def test_zero_exit_with_one_missing_station_is_failure(
    result_inspector: PppfltResultInspector, attempt_dir: Path
) -> None:
    write_outputs(attempt_dir, DEFAULT_STATIONS[:-1])

    report = result_inspector.inspect(
        attempt_dir, expected_stations=DEFAULT_STATIONS
    )

    assert report.ok is False
    assert report.error_code == "missing_output_error"
    assert report.missing_stations == (DEFAULT_STATIONS[-1],)


def test_missing_station_check_is_case_insensitive(
    result_inspector: PppfltResultInspector, attempt_dir: Path
) -> None:
    write_outputs(attempt_dir, tuple(station.lower() for station in DEFAULT_STATIONS))

    report = result_inspector.inspect(
        attempt_dir, expected_stations=DEFAULT_STATIONS
    )

    assert report.ok is True
    assert len(report.files) == 16


def test_missing_station_check_rejects_empty_flt(
    result_inspector: PppfltResultInspector, attempt_dir: Path
) -> None:
    write_outputs(attempt_dir, DEFAULT_STATIONS)
    (attempt_dir / "results" / f"{DEFAULT_STATIONS[-1]}.flt").write_bytes(b"")

    report = result_inspector.inspect(
        attempt_dir, expected_stations=DEFAULT_STATIONS
    )

    assert report.missing_stations == (DEFAULT_STATIONS[-1],)
