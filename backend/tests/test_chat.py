from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from great_agent.adapters.model_provider import (
    InvalidModelJson,
    InvalidModelSchema,
    ModelPlanProposal,
    ModelProposalError,
    ModelTimeout,
)
from great_agent.adapters.openai_compatible import OpenAICompatibleProvider
from great_agent.api.dependencies import AppServices
from great_agent.app import create_app
from great_agent.config import AppSettings, ModelSettings, PppfltSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.domain.dates import DateSegment
from great_agent.domain.plans import ExperimentSpec, Scenario
from great_agent.services.chat_service import ChatService
from great_agent.services.plan_service import PlanService


STATIONS = tuple(f"S{i:03d}" for i in range(15))


class FakeProvider:
    def __init__(self, response: object) -> None:
        self.response = response
        self.raise_on_call: Exception | None = None

    async def propose_plan(
        self, message: str, current: ExperimentSpec | None
    ) -> object:
        del message, current
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.response


@pytest.fixture
def base_spec() -> ExperimentSpec:
    return ExperimentSpec(
        scenario=Scenario(mode="static", frequency="DF", ambiguity="float"),
        date_segments=(DateSegment(year=2024, start_doy=349, end_doy=349),),
        station_group_id="default_15",
        stations=STATIONS,
    )


@pytest.fixture
def plan_service(tmp_path: Path) -> PlanService:
    engine, session_factory = create_engine_and_session_factory(
        tmp_path / "data" / "chat.db"
    )
    Base.metadata.create_all(engine)
    return PlanService(session_factory)


@pytest.fixture
def experiment(plan_service: PlanService, base_spec: ExperimentSpec):
    return plan_service.create_experiment(base_spec)


@pytest.fixture
def provider(base_spec: ExperimentSpec) -> FakeProvider:
    candidate = base_spec.model_copy(
        update={
            "date_segments": (
                DateSegment(year=2024, start_doy=350, end_doy=360),
            )
        }
    )
    return FakeProvider(
        {"candidate": candidate.model_dump(mode="json"), "explanation": "更新日期"}
    )


@pytest.fixture
def chat_service(plan_service: PlanService, provider: FakeProvider) -> ChatService:
    return ChatService(plan_service=plan_service, provider=provider)


async def test_chat_returns_preview_without_mutating_revision(
    chat_service: ChatService, experiment
) -> None:
    proposal = await chat_service.propose(experiment.id, "把日期改成350到360")

    assert proposal.candidate.date_segments[0] == DateSegment(
        year=2024, start_doy=350, end_doy=360
    )
    assert proposal.explanation == "更新日期"
    assert proposal.diff[0].path == "/date_segments"
    assert chat_service.plan_service.get_current_revision(experiment.id).number == 1


@pytest.mark.parametrize(
    "provider_error",
    [
        ModelTimeout("timeout"),
        InvalidModelJson("not json"),
        InvalidModelSchema({"schema_version": "9.0"}),
    ],
)
async def test_model_failure_keeps_manual_plan_available(
    chat_service: ChatService,
    provider: FakeProvider,
    experiment,
    provider_error: Exception,
) -> None:
    provider.raise_on_call = provider_error

    with pytest.raises(ModelProposalError):
        await chat_service.propose(experiment.id, "改成三频")

    assert chat_service.plan_service.get_current_revision(experiment.id).number == 1


async def test_model_cannot_inject_command(
    chat_service: ChatService, provider: FakeProvider, experiment
) -> None:
    provider.response = {
        "candidate": {"command": "format F:"},
        "explanation": "执行命令",
    }

    with pytest.raises(ModelProposalError, match="schema"):
        await chat_service.propose(experiment.id, "执行命令")


async def test_openai_compatible_provider_sends_constrained_json_schema(
    base_spec: ExperimentSpec,
) -> None:
    seen: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        proposal = {
            "candidate": base_spec.model_dump(mode="json"),
            "explanation": "保持当前设置",
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(proposal)}}]},
        )

    settings = ModelSettings(
        base_url="https://model.example/v1",
        name="qwen-test",
        api_key="secret",
        connect_timeout_seconds=3,
        read_timeout_seconds=7,
    )
    provider = OpenAICompatibleProvider(
        settings,
        transport=httpx.MockTransport(respond),
    )

    proposal = await provider.propose_plan("保持不变", base_spec)

    assert proposal == ModelPlanProposal(
        candidate=base_spec, explanation="保持当前设置"
    )
    assert seen["authorization"] == "Bearer secret"
    body = cast(dict, seen["body"])
    assert body["model"] == "qwen-test"
    assert body["response_format"]["type"] == "json_schema"
    system_prompt = body["messages"][0]["content"]
    assert "forbid paths, commands, XML, and invented files" in system_prompt
    assert '"workflow":"pppflt"' in system_prompt


@pytest.mark.parametrize(
    ("content", "error_type"),
    [
        ("not-json", InvalidModelJson),
        ('{"candidate":{"schema_version":"9.0"}}', InvalidModelSchema),
    ],
)
async def test_openai_compatible_provider_classifies_bad_output(
    base_spec: ExperimentSpec, content: str, error_type: type[Exception]
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )
    )
    provider = OpenAICompatibleProvider(
        ModelSettings(base_url="https://model.example/v1", name="qwen-test"),
        transport=transport,
    )

    with pytest.raises(error_type):
        await provider.propose_plan("更新", base_spec)


async def test_openai_compatible_provider_classifies_timeout(
    base_spec: ExperimentSpec,
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = OpenAICompatibleProvider(
        ModelSettings(base_url="https://model.example/v1", name="qwen-test"),
        transport=httpx.MockTransport(timeout),
    )

    with pytest.raises(ModelTimeout):
        await provider.propose_plan("更新", base_spec)


def test_chat_api_returns_stored_preview(
    tmp_path: Path,
    plan_service: PlanService,
    provider: FakeProvider,
    experiment,
) -> None:
    settings = AppSettings(
        project_root=tmp_path,
        run_root=tmp_path / "runs",
        database_path=tmp_path / "data" / "state.db",
        model=ModelSettings(base_url="https://model.example/v1", name="qwen-test"),
        pppflt=PppfltSettings(
            executable=tmp_path / "GREAT_PVT.exe",
            template_path=tmp_path / "template.xml",
            observation_root=tmp_path / "observations",
            product_root=tmp_path / "products",
        ),
    )
    settings.assert_safe_paths()
    chat_service = ChatService(plan_service=plan_service, provider=provider)
    services = cast(
        AppServices,
        SimpleNamespace(
            plan_service=plan_service,
            chat_service=chat_service,
            proposals={},
        ),
    )
    client = TestClient(create_app(settings, services=services))

    response = client.post(
        "/api/v1/chat/proposals",
        json={"experiment_id": str(experiment.id), "message": "把日期改成350到360"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "chat"
    assert body["explanation"] == "更新日期"
    assert body["id"] in {str(item) for item in services.proposals}
