from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sys
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
from great_agent.adapters.great_pppflt import GreatPppfltAdapter, RenderedTask
from great_agent.config import PppfltSettings
from great_agent.domain.dates import DateSegment, YearDoy
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.errors import TaskStateError
from great_agent.runtime.process_runner import ProcessRunner
from great_agent.runtime.result_inspector import PppfltResultInspector
from great_agent.runtime.run_storage import RunStorage
from great_agent.runtime.worker import Worker
from great_agent.services.plan_service import PlanService
from great_agent.services.task_service import TaskService


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


def make_worker_spec() -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(DateSegment(year=2024, start_doy=350, end_doy=350),),
        station_group_id="default_15",
        stations=DEFAULT_STATIONS,
    )


@pytest.fixture
def worker_factory(tmp_path: Path):
    def create(mode: str):
        root = tmp_path / mode
        engine, session_factory = create_engine_and_session_factory(root / "state.db")
        Base.metadata.create_all(engine)
        plan_service = PlanService(session_factory)
        template = root / "template.xml"
        template.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            Path(__file__).parent / "fixtures" / "minimal_pppflt.xml", template
        )
        executable = root / "GREAT_PVT.exe"
        executable.write_bytes(b"mock")
        adapter = GreatPppfltAdapter(
            PppfltSettings(
                executable=executable,
                template_path=template,
                observation_root=root / "observations",
                product_root=root / "products",
            )
        )
        storage = RunStorage(root / "runs")
        spec = make_worker_spec()
        experiment = plan_service.create_experiment(spec)
        plan_service.freeze_revision(experiment.id, expected_revision=1)
        for item in adapter.preflight_task(
            spec, YearDoy(year=2024, doy=350), Path("unused")
        ).missing:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_bytes(b"input")
        task_service = TaskService(
            session_factory=session_factory,
            plan_service=plan_service,
            adapter=adapter,
            run_storage=storage,
        )
        task = task_service.materialize(experiment.id)[0]
        selected_mode = {"value": mode}
        solver = Path(__file__).parent / "fixtures" / "mock_solver.py"

        def command_factory(rendered: RenderedTask, attempt_dir: Path) -> tuple[str, ...]:
            return (
                sys.executable,
                str(solver),
                "--mode",
                selected_mode["value"],
                "--output-dir",
                str(rendered.output_dir),
                "--stations",
                ",".join(DEFAULT_STATIONS),
            )

        repository = TaskRepository(session_factory)
        worker = Worker(
            repository=repository,
            plan_service=plan_service,
            adapter=adapter,
            run_storage=storage,
            process_runner=ProcessRunner(cancel_grace_seconds=0.2),
            result_inspector=PppfltResultInspector(),
            max_concurrency=1,
            command_factory=command_factory,
        )
        return worker, task, selected_mode

    return create


async def wait_until_running(repository: TaskRepository, task_id, timeout: float = 5) -> None:
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = repository.get_task(task_id)
        if task.status == "running" and task.active_attempt.pid is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("task did not start")


async def test_worker_records_success_and_files(worker_factory) -> None:
    worker, task, _ = worker_factory("success")

    await worker.run_once()

    stored = worker.repository.get_task(task.id)
    assert stored.status == "succeeded"
    assert stored.active_attempt.exit_code == 0
    assert len(stored.result_files(kind="flt")) == 15


async def test_worker_records_nonzero_exit(worker_factory) -> None:
    worker, task, _ = worker_factory("fail")

    await worker.run_once()

    attempt = worker.repository.get_task(task.id).active_attempt
    assert attempt.status == "failed"
    assert attempt.error_code == "solver_exit_error"


async def test_worker_records_missing_output_after_zero_exit(worker_factory) -> None:
    worker, task, _ = worker_factory("missing")

    await worker.run_once()

    attempt = worker.repository.get_task(task.id).active_attempt
    assert attempt.exit_code == 0
    assert attempt.status == "failed"
    assert attempt.error_code == "missing_output_error"


async def test_cancel_stops_process_and_preserves_log(worker_factory) -> None:
    import asyncio

    worker, task, _ = worker_factory("slow")
    run = asyncio.create_task(worker.run_once())
    await wait_until_running(worker.repository, task.id)

    await worker.cancel_experiment(task.experiment_id)
    await run

    stored = worker.repository.get_task(task.id)
    assert stored.status == "cancelled"
    assert Path(stored.active_attempt.log_path).is_file()


async def test_retry_creates_new_attempt_and_retains_old_log(worker_factory) -> None:
    worker, task, selected_mode = worker_factory("fail")
    await worker.run_once()
    first = worker.repository.get_task(task.id).active_attempt
    selected_mode["value"] = "success"

    worker.retry(task.id)
    await worker.run_once()

    second = worker.repository.get_task(task.id).active_attempt
    assert second.number == 2
    assert second.status == "succeeded"
    assert Path(first.log_path).is_file()
