from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from great_agent.adapters.great_pppflt import GreatPppfltAdapter
from great_agent.api.dependencies import AppServices
from great_agent.app import create_app
from great_agent.config import AppSettings, ModelSettings, PppfltSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import (
    Base,
    DailyTaskModel,
    ResultFileModel,
    TaskAttemptModel,
)
from great_agent.db.repositories import TaskRepository
from great_agent.domain.dates import DateSegment
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.runtime.process_runner import ProcessRunner
from great_agent.runtime.result_inspector import PppfltResultInspector
from great_agent.runtime.run_storage import RunStorage
from great_agent.runtime.worker import Worker
from great_agent.services.plan_service import PlanService
from great_agent.services.task_service import TaskService


STATIONS = tuple(f"S{i:03d}" for i in range(15))


@pytest.fixture
def valid_spec() -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(DateSegment(year=2024, start_doy=350, end_doy=350),),
        station_group_id="default_15",
        stations=STATIONS,
    )


@pytest.fixture
def valid_spec_payload(valid_spec: ExperimentSpec) -> dict:
    return valid_spec.model_dump(mode="json")


@pytest.fixture
def api_context(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    template = project / "template.xml"
    shutil.copyfile(
        Path(__file__).parent / "fixtures" / "minimal_pppflt.xml", template
    )
    executable = project / "GREAT_PVT.exe"
    executable.write_bytes(b"mock")
    pppflt = PppfltSettings(
        executable=executable,
        template_path=template,
        observation_root=project / "observations",
        product_root=project / "products",
    )
    settings = AppSettings(
        project_root=project,
        run_root=project / "runs",
        database_path=project / "data" / "state.db",
        model=ModelSettings(
            base_url="https://example.invalid/v1", name="test", api_key=""
        ),
        pppflt=pppflt,
    )
    settings.assert_safe_paths()
    engine, session_factory = create_engine_and_session_factory(
        settings.database_path
    )
    Base.metadata.create_all(engine)
    plan_service = PlanService(session_factory)
    adapter = GreatPppfltAdapter(pppflt)
    storage = RunStorage(settings.run_root)
    repository = TaskRepository(session_factory)
    task_service = TaskService(
        session_factory=session_factory,
        plan_service=plan_service,
        adapter=adapter,
        run_storage=storage,
    )
    worker = Worker(
        repository=repository,
        plan_service=plan_service,
        adapter=adapter,
        run_storage=storage,
        process_runner=ProcessRunner(),
        result_inspector=PppfltResultInspector(),
        max_concurrency=1,
    )
    services = AppServices(
        plan_service=plan_service,
        task_service=task_service,
        task_repository=repository,
        worker=worker,
        run_storage=storage,
    )
    app = create_app(settings, services=services)
    return TestClient(app), services, session_factory


@pytest.fixture
def client(api_context) -> TestClient:
    return api_context[0]


def test_full_manual_plan_flow(client: TestClient, valid_spec_payload: dict) -> None:
    created_response = client.post("/api/v1/experiments", json=valid_spec_payload)
    assert created_response.status_code == 201
    created = created_response.json()
    preview = client.post(
        f"/api/v1/experiments/{created['id']}/proposals",
        json={
            "source": "card",
            "candidate": {**valid_spec_payload, "concurrency": 2},
        },
    ).json()
    assert preview["diff"] == [
        {"path": "/concurrency", "before": 1, "after": 2}
    ]
    accepted = client.post(
        f"/api/v1/experiments/{created['id']}/revisions",
        json={"proposal_id": preview["id"], "expected_revision": 1},
    )
    assert accepted.status_code == 201
    assert client.post(
        f"/api/v1/experiments/{created['id']}/freeze"
    ).status_code == 200


def test_get_experiment_returns_current_revision_for_workspace(
    api_context, valid_spec: ExperimentSpec
) -> None:
    client, services, _ = api_context
    experiment = services.plan_service.create_experiment(valid_spec)

    response = client.get(f"/api/v1/experiments/{experiment.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(experiment.id),
        "status": "draft",
        "current_revision": 1,
        "spec": valid_spec.model_dump(mode="json"),
    }


def test_run_is_blocked_when_preflight_has_missing_inputs(
    api_context, valid_spec: ExperimentSpec
) -> None:
    client, services, _ = api_context
    experiment = services.plan_service.create_experiment(valid_spec)
    services.plan_service.freeze_revision(experiment.id, expected_revision=1)

    response = client.post(f"/api/v1/experiments/{experiment.id}/run")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "preflight_error"
    assert any(item["station"] == "S000" for item in body["error"]["details"])


def test_stale_expected_revision_returns_409(
    client: TestClient, valid_spec_payload: dict
) -> None:
    created = client.post("/api/v1/experiments", json=valid_spec_payload).json()
    preview = client.post(
        f"/api/v1/experiments/{created['id']}/proposals",
        json={"source": "card", "candidate": valid_spec_payload},
    ).json()

    response = client.post(
        f"/api/v1/experiments/{created['id']}/revisions",
        json={"proposal_id": preview["id"], "expected_revision": 0},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revision_conflict"


def test_editing_frozen_plan_returns_409(api_context, valid_spec: ExperimentSpec) -> None:
    client, services, _ = api_context
    experiment = services.plan_service.create_experiment(valid_spec)
    services.plan_service.freeze_revision(experiment.id, expected_revision=1)

    response = client.post(
        f"/api/v1/experiments/{experiment.id}/proposals",
        json={"source": "card", "candidate": valid_spec.model_dump(mode="json")},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "plan_frozen"


def seed_task(api_context, valid_spec: ExperimentSpec, *, status: str = "succeeded"):
    _, services, session_factory = api_context
    experiment = services.plan_service.create_experiment(valid_spec)
    revision = services.plan_service.freeze_revision(experiment.id, expected_revision=1)
    task_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            DailyTaskModel(
                id=str(task_id), experiment_id=str(experiment.id),
                revision_id=str(revision.id), year=2024, doy=350,
                status=status, worker_id=None, active_attempt_id=None,
            )
        )
    return experiment, task_id, revision


def test_retrying_succeeded_task_returns_409(api_context, valid_spec: ExperimentSpec) -> None:
    client, _, _ = api_context
    _, task_id, _ = seed_task(api_context, valid_spec)

    response = client.post(f"/api/v1/tasks/{task_id}/retry")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_state_error"


def test_stopping_unknown_experiment_returns_404(client: TestClient) -> None:
    response = client.post(f"/api/v1/experiments/{uuid4()}/stop")

    assert response.status_code == 404


def test_result_path_escape_returns_500_and_security_event(
    api_context, valid_spec: ExperimentSpec, tmp_path: Path
) -> None:
    client, services, session_factory = api_context
    _, task_id, _ = seed_task(api_context, valid_spec)
    attempt_id = uuid4()
    outside = tmp_path / "outside.flt"
    outside.write_text("data", encoding="utf-8")
    with session_factory.begin() as session:
        task = session.get(DailyTaskModel, str(task_id))
        attempt = TaskAttemptModel(
            id=str(attempt_id), task_id=str(task_id), number=1,
            status="succeeded", exit_code=0,
        )
        session.add(attempt)
        session.flush()
        task.active_attempt_id = str(attempt_id)
        session.add(
            ResultFileModel(
                id=str(uuid4()), task_id=str(task_id), attempt_id=str(attempt_id),
                station="S000", kind="flt", path=str(outside),
                size_bytes=outside.stat().st_size,
            )
        )

    response = client.get(f"/api/v1/tasks/{task_id}/results")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "unsafe_result_path"
    assert services.task_repository.list_events(task_id)[-1].kind == "security_error"


def test_sse_replays_only_events_after_last_event_id(
    api_context, valid_spec: ExperimentSpec
) -> None:
    client, services, _ = api_context
    _, task_id, _ = seed_task(api_context, valid_spec, status="pending")
    services.task_repository.publish_event(task_id, "task_status", {"status": "pending"})
    services.task_repository.publish_event(task_id, "task_status", {"status": "running"})

    response = client.get(
        f"/api/v1/tasks/{task_id}/events?once=true",
        headers={"Last-Event-ID": "1"},
    )

    assert response.status_code == 200
    assert "id: 1" not in response.text
    assert "id: 2" in response.text
    assert 'data: {"status":"running"}' in response.text
