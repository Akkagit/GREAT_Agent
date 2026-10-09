from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.domain.dates import DateSegment
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.errors import PlanFrozenError, RevisionConflictError
from great_agent.services.plan_service import PlanService, diff_specs


STATIONS = tuple(f"S{i:03d}" for i in range(15))


@pytest.fixture
def base_spec() -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(DateSegment(year=2024, start_doy=350, end_doy=350),),
        station_group_id="default_15",
        stations=STATIONS,
    )


@pytest.fixture
def plan_service(tmp_path: Path) -> PlanService:
    engine, session_factory = create_engine_and_session_factory(
        tmp_path / "data" / "test.db"
    )
    Base.metadata.create_all(engine)
    return PlanService(session_factory)


def test_preview_does_not_mutate_current_revision(
    plan_service: PlanService, base_spec: ExperimentSpec
) -> None:
    experiment = plan_service.create_experiment(base_spec)
    candidate = base_spec.model_copy(update={"concurrency": 2})

    preview = plan_service.preview_revision(experiment.id, candidate, source="card")

    assert preview.diff == [{"path": "/concurrency", "before": 1, "after": 2}]
    assert plan_service.get_current_spec(experiment.id).concurrency == 1


def test_recursive_diff_reports_nested_field(base_spec: ExperimentSpec) -> None:
    candidate = base_spec.model_copy(
        update={
            "scenario": base_spec.scenario.model_copy(update={"frequency": "TF"})
        }
    )

    assert [item.as_dict() for item in diff_specs(base_spec, candidate)] == [
        {"path": "/scenario/frequency", "before": "DF", "after": "TF"}
    ]


def test_accept_revision_rejects_stale_expected_revision(
    plan_service: PlanService, base_spec: ExperimentSpec
) -> None:
    experiment = plan_service.create_experiment(base_spec)
    accepted = plan_service.accept_revision(
        experiment.id,
        base_spec.model_copy(update={"concurrency": 2}),
        expected_revision=1,
    )

    with pytest.raises(RevisionConflictError, match="expected revision 1"):
        plan_service.accept_revision(
            experiment.id, base_spec, expected_revision=1
        )

    assert accepted.number == 2
    assert plan_service.get_current_spec(experiment.id).concurrency == 2


def test_frozen_revision_is_immutable(
    plan_service: PlanService, base_spec: ExperimentSpec
) -> None:
    experiment = plan_service.create_experiment(base_spec)
    frozen = plan_service.freeze_revision(experiment.id, expected_revision=1)

    with pytest.raises(PlanFrozenError):
        plan_service.accept_revision(
            experiment.id, base_spec, expected_revision=frozen.number
        )

    assert frozen.is_frozen is True
    assert plan_service.get_experiment(experiment.id).status == "ready"


def test_create_experiment_stores_an_independent_snapshot(
    plan_service: PlanService, base_spec: ExperimentSpec
) -> None:
    experiment = plan_service.create_experiment(base_spec)
    changed = base_spec.model_copy(update={"concurrency": 2})

    assert changed.concurrency == 2
    assert plan_service.get_current_spec(experiment.id).concurrency == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("output_path", "F:\\outside"), ("command", "del /s D:\\Data")],
)
def test_candidate_rejects_output_path_and_command_fields(
    base_spec: ExperimentSpec, field: str, value: str
) -> None:
    payload = base_spec.model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(payload)


def test_sqlite_enables_foreign_keys_and_wal(tmp_path: Path) -> None:
    engine, _ = create_engine_and_session_factory(tmp_path / "db" / "state.db")

    with engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()

    assert foreign_keys == 1
    assert journal_mode.lower() == "wal"
