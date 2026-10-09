from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.config import PppfltSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base, DailyTaskModel
from great_agent.domain.dates import DateSegment, YearDoy, expand_dates
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.errors import PlanNotFrozenError, PreflightError, UnsafePathError
from great_agent.runtime.run_storage import RunStorage
from great_agent.services.plan_service import PlanService
from great_agent.services.task_service import TaskService


STATIONS = (
    "AHB0", "BJFS", "CHAN", "DAEJ", "GAMG", "GMSD", "HKSL", "JFNG",
    "KUNM", "LHAZ", "NTUS", "SHAO", "URUM", "WUHN", "YELL",
)


def cross_year_spec() -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(
            DateSegment(year=2024, start_doy=365, end_doy=366),
            DateSegment(year=2025, start_doy=1, end_doy=2),
            DateSegment(year=2025, start_doy=2, end_doy=3),
        ),
        excluded_dates=(YearDoy(year=2025, doy=2),),
        station_group_id="default_15",
        stations=STATIONS,
    )


@pytest.fixture
def run_storage(tmp_path: Path) -> RunStorage:
    return RunStorage(tmp_path / "runs")


@pytest.fixture
def materialization_context(tmp_path: Path, run_storage: RunStorage):
    engine, session_factory = create_engine_and_session_factory(
        tmp_path / "data" / "test.db"
    )
    Base.metadata.create_all(engine)
    plan_service = PlanService(session_factory)

    template = tmp_path / "template.xml"
    shutil.copyfile(
        Path(__file__).parent / "fixtures" / "minimal_pppflt.xml", template
    )
    executable = tmp_path / "bin" / "GREAT_PVT.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"mock executable")
    adapter = GreatPppfltAdapter(
        PppfltSettings(
            executable=executable,
            template_path=template,
            observation_root=tmp_path / "observations",
            product_root=tmp_path / "products",
        )
    )
    task_service = TaskService(
        session_factory=session_factory,
        plan_service=plan_service,
        adapter=adapter,
        run_storage=run_storage,
    )
    return task_service, plan_service, adapter, session_factory


def create_required_inputs(
    adapter: GreatPppfltAdapter, spec: ExperimentSpec
) -> None:
    for date in expand_dates(spec):
        report = adapter.preflight_task(spec, date, Path("unused"))
        for item in report.missing:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_bytes(b"input")


def test_materialize_creates_one_task_per_unique_date(
    materialization_context,
) -> None:
    task_service, plan_service, adapter, _ = materialization_context
    spec = cross_year_spec()
    experiment = plan_service.create_experiment(spec)
    plan_service.freeze_revision(experiment.id, expected_revision=1)
    create_required_inputs(adapter, spec)

    tasks = task_service.materialize(experiment.id)

    assert [(task.year, task.doy) for task in tasks] == [
        (2024, 365), (2024, 366), (2025, 1), (2025, 3)
    ]
    assert all(task.station_count == 15 for task in tasks)
    assert all(task.status == "pending" for task in tasks)


def test_materialize_is_idempotent(materialization_context) -> None:
    task_service, plan_service, adapter, _ = materialization_context
    spec = cross_year_spec()
    experiment = plan_service.create_experiment(spec)
    plan_service.freeze_revision(experiment.id, expected_revision=1)
    create_required_inputs(adapter, spec)

    first = task_service.materialize(experiment.id)
    second = task_service.materialize(experiment.id)

    assert [task.id for task in second] == [task.id for task in first]


def test_preflight_failure_creates_no_tasks_and_aggregates_dates(
    materialization_context,
) -> None:
    task_service, plan_service, _, session_factory = materialization_context
    spec = cross_year_spec()
    experiment = plan_service.create_experiment(spec)
    plan_service.freeze_revision(experiment.id, expected_revision=1)

    with pytest.raises(PreflightError) as captured:
        task_service.materialize(experiment.id)

    assert {item.date for item in captured.value.missing} == set(expand_dates(spec))
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0


def test_materialize_requires_frozen_revision(materialization_context) -> None:
    task_service, plan_service, _, _ = materialization_context
    experiment = plan_service.create_experiment(cross_year_spec())

    with pytest.raises(PlanNotFrozenError):
        task_service.materialize(experiment.id)


def test_materialize_writes_frozen_snapshot_manifest_and_task_directories(
    materialization_context, run_storage: RunStorage
) -> None:
    task_service, plan_service, adapter, _ = materialization_context
    spec = cross_year_spec()
    experiment = plan_service.create_experiment(spec)
    plan_service.freeze_revision(experiment.id, expected_revision=1)
    create_required_inputs(adapter, spec)

    tasks = task_service.materialize(experiment.id)
    frozen_path = run_storage.resolve(str(experiment.id), "plan", "frozen_plan.json")
    manifest_path = run_storage.resolve(str(experiment.id), "manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["plan_sha256"] == hashlib.sha256(frozen_path.read_bytes()).hexdigest()
    assert manifest["stations"] == list(STATIONS)
    assert manifest["template"]["path"] == str(adapter.settings.template_path.resolve())
    assert manifest["executable"] == str(adapter.settings.executable.resolve())
    assert len(manifest["inputs"]) > 15 * len(tasks)
    assert all(
        run_storage.resolve(
            str(experiment.id), "tasks", f"{task.year:04d}_{task.doy:03d}", "attempts"
        ).is_dir()
        for task in tasks
    )


def test_storage_rejects_escape(run_storage: RunStorage) -> None:
    with pytest.raises(UnsafePathError):
        run_storage.resolve("..", "outside")
