"""Runtime failures expose bounded metadata without exposing provider content."""
from pathlib import Path

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProviderError
from great_agent.config import ModelSettings
from great_agent.runtime.conversation_agent import AgentScopeConversationEngine


async def forbidden_proposal(*args):
    raise AssertionError("proposal callback forbidden")


@pytest.mark.parametrize("api_key", ["", " \t"])
async def test_missing_credential_has_safe_diagnostic_without_provider_request(api_key):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, text="PRIVATE PROVIDER RESPONSE")

    engine = AgentScopeConversationEngine(
        ModelSettings(base_url="https://fixture.invalid/v1", name="glm-4.7", api_key=api_key),
        resource_root=Path("config/agent"), transport=httpx.MockTransport(respond),
    )
    with pytest.raises(ModelProviderError) as error:
        await engine.reply("question", history=[], context=None, state=None, propose=forbidden_proposal)

    assert getattr(error.value, "diagnostic_code", None) == "missing_api_key"
    assert not requests
    assert "PRIVATE" not in str(error.value)


async def test_provider_http_status_survives_runtime_without_response_content():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "PRIVATE PROVIDER RESPONSE"}})

    engine = AgentScopeConversationEngine(
        ModelSettings(base_url="https://fixture.invalid/v1", name="glm-4.7", api_key="PRIVATE API KEY"),
        resource_root=Path("config/agent"), transport=httpx.MockTransport(respond),
    )
    with pytest.raises(ModelProviderError) as error:
        await engine.reply("question", history=[], context=None, state=None, propose=forbidden_proposal)

    assert getattr(error.value, "upstream_status", None) == 401
    assert len(requests) == 1
    assert "PRIVATE" not in str(error.value)
    assert "fixture.invalid" not in str(error.value)
