from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from great_agent.db.models import Base
from great_agent.domain.dates import DateSegment, expand_dates
from test_api import api_context, valid_spec  # noqa: F401


def snapshot_database(session_factory):
    with session_factory() as session:
        return {
            table.name: list(session.execute(select(table)))
            for table in Base.metadata.tables.values()
        }


def bound_body(revision):
    return {"expected_revision_id": str(revision.id), "expected_revision": revision.number}


@pytest.mark.parametrize("with_body", [False, True])
@pytest.mark.parametrize("inputs_present", [False, True])
def test_validation_reports_checked_revision_without_side_effects(
    api_context, valid_spec, with_body, inputs_present,
):
    client, services, session_factory = api_context
    experiment = services.plan_service.create_experiment(valid_spec)
    # Check an accepted proposal rather than only the initial revision.
    proposal = services.plan_service.preview_revision(
        experiment.id, valid_spec.model_copy(update={"concurrency": 2}),
        source="card", base_revision_id=experiment.current_revision_id,
    )
    revision = services.plan_service.accept_proposal(
        experiment.id, proposal.id, expected_revision=1,
    )
    date = expand_dates(revision.spec)[0]
    task_dir = services.run_storage.task_dir(experiment.id, date)
    required = services.task_service.adapter.preflight_task(revision.spec, date, task_dir)
    if inputs_present:
        for item in required.missing:
            item.path.parent.mkdir(parents=True, exist_ok=True)
            item.path.write_bytes(b"synthetic input")
    before_db = snapshot_database(session_factory)
    before_files = set(services.run_storage.root.rglob("*"))
    request = {"json": bound_body(revision)} if with_body else {}

    first = client.post(f"/api/v1/experiments/{experiment.id}/validate", **request)
    second = client.post(f"/api/v1/experiments/{experiment.id}/validate", **request)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["revision_id"] == str(revision.id)
    assert body["revision"] == 2
    assert body["ok"] is inputs_present
    if inputs_present:
        assert body["missing"] == []
    else:
        observation = next(item for item in body["missing"] if item["station"] == "S000")
        assert observation["kind"] == "observation"
        assert observation["year"] == 2024
        assert observation["doy"] == 350
        assert observation["state"] == "missing"
        assert observation["path"]
    assert snapshot_database(session_factory) == before_db
    assert set(services.run_storage.root.rglob("*")) == before_files


@pytest.mark.parametrize("stale_field", ["expected_revision_id", "expected_revision"])
def test_validation_rejects_stale_identity_before_inspecting_inputs(
    api_context, valid_spec, monkeypatch, stale_field,
):
    client, services, _ = api_context
    experiment = services.plan_service.create_experiment(valid_spec)
    revision = services.plan_service.get_current_revision(experiment.id)
    body = bound_body(revision)
    body[stale_field] = str(uuid4()) if stale_field.endswith("_id") else 0

    def unexpected_preflight(*args):
        raise AssertionError("stale revision must be rejected before any input inspection")

    monkeypatch.setattr(services.task_service.adapter, "preflight_task", unexpected_preflight)
    response = client.post(f"/api/v1/experiments/{experiment.id}/validate", json=body)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revision_conflict"


@pytest.mark.parametrize("with_body", [False, True])
def test_validation_rejects_current_revision_changed_during_preflight(
    api_context, valid_spec, monkeypatch, with_body,
):
    client, services, _ = api_context
    spec = valid_spec.model_copy(update={
        "date_segments": (DateSegment(year=2024, start_doy=350, end_doy=351),),
    })
    experiment = services.plan_service.create_experiment(spec)
    revision = services.plan_service.get_current_revision(experiment.id)
    original = services.task_service.adapter.preflight_task
    checked = []

    def concurrent_preflight(checked_spec, date, task_dir):
        checked.append((checked_spec, date.doy))
        if len(checked) == 1:
            services.plan_service.accept_revision(
                experiment.id, valid_spec, expected_revision=1,
            )
        return original(checked_spec, date, task_dir)

    monkeypatch.setattr(services.task_service.adapter, "preflight_task", concurrent_preflight)
    request = {"json": bound_body(revision)} if with_body else {}
    response = client.post(f"/api/v1/experiments/{experiment.id}/validate", **request)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "revision_conflict"
    assert checked == [(revision.spec, 350), (revision.spec, 351)]


@pytest.mark.parametrize("body", [
    {},
    {"expected_revision_id": "not-a-uuid", "expected_revision": 1},
    {"expected_revision_id": str(uuid4())},
    {"expected_revision": 1},
    {"expected_revision_id": str(uuid4()), "expected_revision": "invalid"},
    {"expected_revision_id": str(uuid4()), "expected_revision": True},
    {"expected_revision_id": str(uuid4()), "expected_revision": 1, "extra": True},
])
def test_validation_rejects_malformed_revision_binding(api_context, valid_spec, body):
    client, services, _ = api_context
    experiment = services.plan_service.create_experiment(valid_spec)

    response = client.post(f"/api/v1/experiments/{experiment.id}/validate", json=body)

    assert response.status_code == 422
