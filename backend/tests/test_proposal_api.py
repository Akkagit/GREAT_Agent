from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from great_agent.db.models import DailyTaskModel, ExecutionPreviewModel
from great_agent.services.plan_service import PlanService
from test_api import api_context, valid_spec, valid_spec_payload  # noqa: F401
from test_execution_preview import preview_context  # noqa: F401


def create_with_base(api_context, spec):
    client, services, _ = api_context
    experiment_id = UUID(client.post("/api/v1/experiments", json=spec).json()["id"])
    base = services.plan_service.get_current_revision(experiment_id)
    return experiment_id, base


def test_http_manual_proposal_requires_an_explicit_base(api_context, valid_spec_payload):
    client, services, _ = api_context
    experiment_id, _ = create_with_base(api_context, valid_spec_payload)

    result = client.post(f"/api/v1/experiments/{experiment_id}/proposals", json={
        "source": "card", "candidate": valid_spec_payload,
    })

    assert result.status_code == 422
    assert services.plan_service.get_current_revision(experiment_id).number == 1


def test_http_proposal_and_rejection_survive_service_reconstruction(api_context, valid_spec_payload):
    client, services, sessions = api_context
    experiment_id, base = create_with_base(api_context, valid_spec_payload)
    created = client.post(f"/api/v1/experiments/{experiment_id}/proposals", json={
        "source": "card", "candidate": valid_spec_payload, "base_revision_id": str(base.id),
    })
    assert created.status_code == 200
    proposal_id = created.json()["id"]
    assert created.json()["base_revision_id"] == str(base.id)
    services.plan_service = PlanService(sessions)

    restored = client.get(f"/api/v1/experiments/{experiment_id}/proposals/{proposal_id}")
    assert restored.status_code == 200
    assert restored.json()["candidate"] == valid_spec_payload
    assert restored.json()["status"] == "pending"
    rejected = client.post(f"/api/v1/experiments/{experiment_id}/proposals/{proposal_id}/reject")
    assert rejected.status_code == 200
    services.plan_service = PlanService(sessions)
    pending = client.get(f"/api/v1/experiments/{experiment_id}/proposals", params={"status": "pending"})
    assert pending.status_code == 200
    assert pending.json() == []


def test_http_derived_stale_proposal_cannot_be_rebased(api_context, valid_spec_payload):
    client, _, _ = api_context
    experiment_id, base = create_with_base(api_context, valid_spec_payload)
    old = client.post(f"/api/v1/experiments/{experiment_id}/proposals", json={
        "source": "card", "candidate": valid_spec_payload, "base_revision_id": str(base.id),
    }).json()
    newer = {**valid_spec_payload, "concurrency": 2}
    current = client.post(f"/api/v1/experiments/{experiment_id}/proposals", json={
        "source": "card", "candidate": newer, "base_revision_id": str(base.id),
    }).json()
    accepted = client.post(f"/api/v1/experiments/{experiment_id}/revisions", json={
        "proposal_id": current["id"], "expected_revision": 1,
    })
    assert accepted.status_code == 201
    result = client.post(f"/api/v1/experiments/{experiment_id}/proposals", json={
        "source": "card", "candidate": valid_spec_payload,
        "source_proposal_id": old["id"], "base_revision_id": accepted.json()["id"],
    })
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "revision_conflict"
    assert client.get(f"/api/v1/experiments/{experiment_id}").json()["spec"]["concurrency"] == 2


def test_invalid_domain_dates_are_rejected_before_creating_an_experiment(api_context, valid_spec_payload):
    client, _, _ = api_context
    invalid = {**valid_spec_payload, "date_segments": [{"year": 2025, "start_doy": 366, "end_doy": 366}]}

    response = client.post("/api/v1/experiments", json=invalid)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "plan_validation_error"
    assert client.get("/api/v1/experiments").json() == []


def test_experiment_detail_exposes_revision_identity(api_context, valid_spec_payload):
    client, _, _ = api_context
    experiment_id, base = create_with_base(api_context, valid_spec_payload)

    detail = client.get(f"/api/v1/experiments/{experiment_id}")

    assert detail.status_code == 200
    assert detail.json()["current_revision_id"] == str(base.id)


def test_pending_proposal_blocks_execution_preview_without_freezing_or_queueing(preview_context):
    client, services, sessions, experiment = preview_context
    spec = services.plan_service.get_current_spec(experiment.id)
    services.plan_service.preview_revision(experiment.id, spec, source="card")

    response = client.post(f"/api/v1/experiments/{experiment.id}/execution-preview", json={
        "expected_revision": 1,
    })

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "pending_proposal"
    assert not services.plan_service.get_current_revision(experiment.id).is_frozen
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionPreviewModel)) == 0
        assert session.scalar(select(func.count()).select_from(DailyTaskModel)) == 0
    assert not list(services.run_storage.root.rglob("config.xml"))
