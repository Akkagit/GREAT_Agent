from __future__ import annotations

import copy
import json
import logging
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from great_agent.adapters.model_provider import (
    InvalidModelJson, InvalidModelSchema, ModelProposalError, ModelProviderError, ModelTimeout,
    canonicalize_station_selection,
)
from great_agent.adapters.openai_compatible import OpenAICompatibleProvider
from great_agent.app import create_app
from great_agent.config import AppSettings, ModelSettings
from great_agent.domain.plans import ExperimentSpec
from great_agent.services.chat_service import ChatService
from test_chat import base_spec, experiment, model_completion, plan_service, provider  # noqa: F401


def sdk_provider(payload):
    return OpenAICompatibleProvider(
        ModelSettings(base_url="https://fixture.invalid/v1", name="fixture", api_key="fixture",
                      response_format="json_object"),
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=model_completion(json.dumps(payload)))),
    )


def removed_first(base_spec):
    candidate = base_spec.model_dump(mode="json")
    candidate["stations"] = candidate["stations"][1:]
    return {"candidate": candidate, "explanation": "移除首站"}


async def test_sdk_removal_becomes_custom_pending_preview_without_mutating_revision(
    base_spec, plan_service, experiment,
):
    payload = removed_first(base_spec)
    original = copy.deepcopy(payload)
    service = ChatService(plan_service=plan_service, provider=sdk_provider(payload))
    proposal = await service.propose(experiment.id, "我想要去掉第一个测站")
    expected = copy.deepcopy(payload["candidate"])
    expected["station_group_id"] = "custom"
    assert proposal.candidate.model_dump(mode="json") == expected
    assert proposal.candidate.stations == base_spec.stations[1:]
    assert payload == original
    preview = plan_service.preview_revision(
        experiment.id, proposal.candidate, source="chat",
        base_revision_id=proposal.base_revision_id, explanation=proposal.explanation,
    )
    assert preview.candidate == proposal.candidate
    assert plan_service.get_current_revision(experiment.id).number == 1
    assert plan_service.get_current_spec(experiment.id) == base_spec


@pytest.mark.parametrize("group,changed", [("default_15", False), ("custom", True)])
async def test_sdk_preserves_unchanged_preset_and_existing_custom(base_spec, group, changed):
    current = ExperimentSpec.model_validate(dict(base_spec.model_dump(), station_group_id=group))
    payload = {"candidate": current.model_dump(mode="json"), "explanation": "保持分组"}
    if changed:
        payload["candidate"]["stations"] = payload["candidate"]["stations"][1:]
    result = await sdk_provider(payload).propose_plan("更新", current)
    assert result.candidate.station_group_id == group


@pytest.mark.parametrize("bad", ["empty", "duplicate", "blank", "nonstring", "notarray",
                                     "missing_stations", "missing_group", "extra", "invalid_enum"])
async def test_sdk_station_edits_do_not_repair_invalid_candidates(base_spec, bad):
    payload = removed_first(base_spec)
    candidate = payload["candidate"]
    if bad == "empty": candidate["stations"] = []
    elif bad == "duplicate": candidate["stations"][1] = candidate["stations"][0].lower()
    elif bad == "blank": candidate["stations"][0] = " "
    elif bad == "nonstring": candidate["stations"][0] = 123
    elif bad == "notarray": candidate["stations"] = "S001"
    elif bad == "missing_stations": del candidate["stations"]
    elif bad == "missing_group": del candidate["station_group_id"]
    elif bad == "extra": candidate["command"] = "private-command"
    elif bad == "invalid_enum": candidate["scenario"]["mode"] = "private-mode"
    with pytest.raises(InvalidModelSchema):
        await sdk_provider(payload).propose_plan("更新", base_spec)


def test_domain_preset_constraint_remains_strict(base_spec):
    with pytest.raises(ValidationError, match="exactly 15"):
        ExperimentSpec.model_validate(removed_first(base_spec)["candidate"])


def test_canonicalization_copies_payload_without_reordering_or_rewriting_fields(base_spec):
    payload = removed_first(base_spec)
    payload["candidate"]["stations"] = [f" {s.lower()} " for s in payload["candidate"]["stations"]]
    original = copy.deepcopy(payload)
    result = canonicalize_station_selection(payload, base_spec)
    assert payload == original
    assert result is not payload
    assert result["candidate"]["station_group_id"] == "custom"
    expected = copy.deepcopy(original)
    expected["candidate"]["station_group_id"] = "custom"
    assert result == expected


def test_normalization_only_change_keeps_preset(base_spec):
    candidate = base_spec.model_dump(mode="json")
    candidate["stations"] = [f" {s.lower()} " for s in base_spec.stations]
    payload = {"candidate": candidate, "explanation": "x"}
    assert canonicalize_station_selection(payload, base_spec) is payload


@pytest.mark.parametrize("failure,category", [
    (InvalidModelSchema("private-body"), "schema"),
    (InvalidModelJson("private-body"), "invalid_json"),
    (ModelTimeout("private-body"), "timeout"),
    (ModelProviderError("private-body"), "provider"),
    (None, "schema"),
])
def test_api_failure_is_classified_correlated_and_safe(
    valid_config, plan_service, provider, experiment, caplog, failure, category,
):
    provider.raise_on_call = failure
    if failure is None:
        provider.response = {"candidate": {"command": "private-body"}, "explanation": "x"}
    services = SimpleNamespace(plan_service=plan_service,
        chat_service=ChatService(plan_service=plan_service, provider=provider))
    caplog.set_level(logging.INFO)
    with TestClient(create_app(AppSettings.load(valid_config, {}), services=services)) as client:
        response = client.post("/api/v1/chat/proposals", json={
            "experiment_id": str(experiment.id), "message": "private-user-content"})
    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "model_proposal_error"
    assert error["category"] == category
    UUID(error["request_id"])
    assert error["request_id"] in caplog.text
    assert any("\u4e00" <= c <= "\u9fff" for c in error["message"])
    assert "private-body" not in response.text + caplog.text
    assert "private-user-content" not in response.text + caplog.text
    assert plan_service.get_current_revision(experiment.id).number == 1
    assert not plan_service.list_proposals(experiment.id)


def test_direct_model_proposal_error_cannot_expose_arbitrary_text(valid_config, plan_service, experiment):
    class FailingService:
        async def propose(self, *args, **kwargs):
            raise ModelProposalError("private-direct-body")
    services = SimpleNamespace(plan_service=plan_service, chat_service=FailingService())
    with TestClient(create_app(AppSettings.load(valid_config, {}), services=services)) as client:
        response = client.post("/api/v1/chat/proposals", json={
            "experiment_id": str(experiment.id), "message": "private-user-content"})
    assert response.status_code == 502
    assert response.json()["error"]["category"] == "provider"
    UUID(response.json()["error"]["request_id"])
    assert "private-direct-body" not in response.text
