"""Creation tools stage validated drafts; only the server commits a turn."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProviderError, ModelTimeout
from great_agent.config import ModelSettings
from great_agent.runtime.conversation_agent import AgentScopeConversationEngine


def completion(text=None, calls=None):
    return {
        "id": "creation-fixture", "object": "chat.completion", "created": 1,
        "model": "glm-4.7", "choices": [{"index": 0,
            "finish_reason": "tool_calls" if calls else "stop",
            "message": {"role": "assistant", "content": text,
                        **({"tool_calls": calls} if calls else {})}}],
    }


def call(name, args=None, id="c1"):
    return {"id": id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args or {})}}


def engine(handler, **kwargs):
    return AgentScopeConversationEngine(
        ModelSettings(base_url="https://fixture.invalid/v1", name="glm-4.7", api_key="SECRET"),
        resource_root=Path("config/agent"), transport=httpx.MockTransport(handler), **kwargs,
    )


async def forbidden(*args):
    raise AssertionError("Unexpected server mutation")


def tool_payload(body, name):
    # OpenAI tool replies identify their assistant call by ID, not by name.
    call_id = next(tool_call["id"] for message in reversed(body["messages"])
                   if message.get("role") == "assistant"
                   for tool_call in reversed(message.get("tool_calls", []))
                   if tool_call["function"]["name"] == name)
    message = next(message for message in reversed(body["messages"])
                   if message.get("role") == "tool" and message.get("tool_call_id") == call_id)
    content = message["content"]
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content)
    return json.loads(content)


@pytest.mark.parametrize("bundled", [False, True])
async def test_creation_tools_use_server_defaults_and_stage_one_validated_draft(tmp_path, bundled):
    defaults = {"schema_version": "1.0", "workflow": "pppflt",
                "scenario": {"mode": "static", "frequency": "DF", "ambiguity": "float", "reset_hours": 0},
                "date_segments": [{"year": 2024, "start_doy": 339, "end_doy": 339}],
                "excluded_dates": [], "station_group_id": "custom", "stations": ["ABCD"],
                "input_profile_id": "local_default", "solver_profile_id": "great_local",
                "template_profile_id": "pppflt_base", "concurrency": 1, "outputs": ["flt", "log"]}
    received = []
    requests = []

    async def create(candidate):
        received.append(candidate)
        return {"status": "validated", "pending": True, "spec": candidate}

    def respond(req):
        body = json.loads(req.content)
        requests.append(body)
        names = {item["function"]["name"] for item in body["tools"]}
        assert {"get_experiment_defaults", "create_experiment"} <= names
        assert "propose_plan_change" not in names
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[call("get_experiment_defaults", id="defaults")]))
        if len(requests) == 2:
            assert tool_payload(body, "get_experiment_defaults") == defaults
            return httpx.Response(200, json=completion(calls=[call("create_experiment", {"candidate": defaults}, id="create")]))
        staged = tool_payload(body, "create_experiment")
        assert staged == {"status": "validated", "pending": True, "spec": defaults}
        assert "id" not in staged
        return httpx.Response(200, json=completion("参数已校验，等待服务器保存实验草稿。"))

    instance = engine(respond)
    if bundled:
        instance.resource_root = tmp_path / "missing"
    result = await instance.reply("使用默认参数创建实验", history=[], context=None, state=None,
                                  propose=forbidden, create=create, creation_defaults=defaults)
    assert received == [defaults]
    assert result.proposal is None
    assert result.metrics["resource_source"] == ("bundled" if bundled else "configured")
    assert result.metrics["tool_calls"] == 2


@pytest.mark.parametrize("enabled", [False, True])
async def test_creation_capability_follows_server_callback_only(enabled):
    requests = []

    def respond(req):
        body = json.loads(req.content)
        requests.append(body)
        names = {item["function"]["name"] for item in body["tools"]}
        assert ("create_experiment" in names) is enabled
        assert ("get_experiment_defaults" in names) is enabled
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[call("get_capabilities")]))
        assert tool_payload(body, "get_capabilities")["experiment_creation"] is enabled
        return httpx.Response(200, json=completion("Capabilities explained"))

    kwargs = {"create": forbidden, "creation_defaults": {"stations": ["ABCD"]}} if enabled else {}
    result = await engine(respond).reply("能力", history=[], context=None, state=None,
                                       propose=forbidden, **kwargs)
    assert result.text == "Capabilities explained"


async def test_duplicate_creation_calls_in_one_turn_reach_server_only_once():
    received = []
    requests = []

    async def create(candidate):
        received.append(candidate)
        return {"status": "validated", "pending": True, "spec": candidate}

    def respond(req):
        body = json.loads(req.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[
                call("create_experiment", {"candidate": {"stations": ["ABCD"]}}, "first"),
                call("create_experiment", {"candidate": {"stations": ["EFGH"]}}, "second"),
            ]))
        tool_messages = [message for message in body["messages"] if message["role"] == "tool"]
        assert len(tool_messages) == 2
        assert "Only one" in str(tool_messages[1]["content"])
        return httpx.Response(200, json=completion("等待服务器保存草稿。"))

    result = await engine(respond).reply("创建", history=[], context=None, state=None,
                                       propose=forbidden, create=create, creation_defaults={})
    assert received == [{"stations": ["ABCD"]}]
    assert result.metrics["tool_calls"] == 2


async def test_creation_validation_error_fails_turn_without_followup_request():
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("create_experiment", {"candidate": {}})]))

    async def create(candidate):
        raise ValueError("SECRET server validation detail")

    with pytest.raises(ModelProviderError) as error:
        await engine(respond).reply("创建", history=[], context=None, state=None,
                                   propose=forbidden, create=create, creation_defaults={})
    assert "SECRET" not in str(error.value)
    assert len(requests) == 1


async def test_cancellation_during_creation_stops_followup_request():
    entered = asyncio.Event()
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("create_experiment", {"candidate": {}})]))

    async def create(candidate):
        entered.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(engine(respond).reply("创建", history=[], context=None, state=None,
                                                   propose=forbidden, create=create, creation_defaults={}))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1


async def test_creation_callback_cannot_suppress_late_cancellation():
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("create_experiment", {"candidate": {}})]))

    async def create(candidate):
        asyncio.current_task().cancel()
        return {"status": "validated", "pending": True, "spec": candidate}

    task = asyncio.create_task(engine(respond).reply("创建", history=[], context=None, state=None,
                                                   propose=forbidden, create=create, creation_defaults={}))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1


async def test_creation_callback_cannot_suppress_turn_deadline():
    requests = []

    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("create_experiment", {"candidate": {}})]))

    async def create(candidate):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            return {"status": "validated", "pending": True, "spec": candidate}

    with pytest.raises(ModelTimeout):
        await engine(respond, timeout_seconds=.2).reply("创建", history=[], context=None, state=None,
                                                      propose=forbidden, create=create, creation_defaults={})
    assert len(requests) == 1
