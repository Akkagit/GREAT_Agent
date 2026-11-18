import asyncio
import json
from pathlib import Path

import httpx
import pytest

from great_agent.config import ModelSettings
from great_agent.adapters.model_provider import ModelProviderError, ModelTimeout


def completion(text=None, calls=None):
    return {"id": "fixture", "object": "chat.completion", "created": 1,
            "model": "glm-4.7", "choices": [{"index": 0,
            "finish_reason": "tool_calls" if calls else "stop",
            "message": {"role": "assistant", "content": text, **({"tool_calls": calls} if calls else {})}}]}


def call(name, args=None, id="c1"):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


def engine(handler, **kw):
    from great_agent.runtime.conversation_agent import AgentScopeConversationEngine
    return AgentScopeConversationEngine(ModelSettings(base_url="https://fixture.invalid/v1", name="glm-4.7", api_key="SECRET"),
        resource_root=Path("config/agent"), transport=httpx.MockTransport(handler), **kw)


async def forbidden(*args):
    raise AssertionError("proposal callback forbidden")


async def test_theory_text_native_state_and_wire_flags():
    requests = []
    def respond(req):
        body = json.loads(req.content); requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        assert "response_format" not in body
        assert "propose_plan_change" not in str(body.get("tools"))
        return httpx.Response(200, json=completion("PPP uses precise products."))
    result = await engine(respond).reply("Explain PPP", history=[], context=None, state=None, propose=forbidden)
    assert result.text == "PPP uses precise products."
    assert result.proposal is None
    assert result.state["version"] == 1
    assert result.metrics["model_requests"] == 1
    assert len(result.metrics["skill_versions"]) == 5


@pytest.mark.parametrize("no_change", [True, False])
async def test_mixed_proposal_tracked_independently(no_change):
    n = 0; received = []
    def respond(req):
        nonlocal n
        n += 1
        return httpx.Response(200, json=completion(calls=[call("propose_plan_change", {"candidate": {"stations": ["ABCD"]}, "explanation": "change"})]) if n == 1 else completion("Explanation after tool."))
    async def propose(candidate, explanation):
        received.append(candidate)
        return {"proposal": None if no_change else {"id": "p1"}, "no_change": no_change}
    result = await engine(respond).reply("change", history=[], context={"spec": {}, "read_only": False, "tasks": []}, state=None, propose=propose)
    assert result.text == "Explanation after tool."
    assert result.proposal == (None if no_change else {"id": "p1"})
    assert len(received) == 1


async def test_readonly_tools_and_invalid_state_fallback():
    n = 0
    def respond(req):
        nonlocal n
        n += 1; body = json.loads(req.content)
        assert "propose_plan_change" not in str(body.get("tools"))
        if n == 1:
            assert "old question" in str(body["messages"])
            return httpx.Response(200, json=completion(calls=[call("get_current_plan")]))
        assert "BOUND" in str(body["messages"])
        return httpx.Response(200, json=completion("Frozen explanation"))
    result = await engine(respond).reply("question", history=[{"role":"user", "text":"old question"}], context={"spec": {"label":"BOUND"}, "read_only": True}, state={"garbage":1}, propose=forbidden)
    assert result.proposal is None
    assert result.metrics["tool_calls"] == 1


async def test_request_budget_and_no_retry():
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("get_capabilities", id=str(len(requests)))]))
    with pytest.raises(ModelProviderError):
        await engine(respond, max_model_requests=2).reply("loop", history=[], context=None, state=None, propose=forbidden)
    assert len(requests) == 2


async def test_provider_errors_safe_without_retry():
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(500, json={"error":{"message":"SECRET BODY"}})
    with pytest.raises(ModelProviderError) as error:
        await engine(respond).reply("q", history=[], context=None, state=None, propose=forbidden)
    assert "SECRET" not in str(error.value)
    assert len(requests) == 1


async def test_deadline_and_external_cancel():
    entered = asyncio.Event()
    async def respond(req):
        entered.set(); await asyncio.sleep(10)
        return httpx.Response(200, json=completion("late"))
    with pytest.raises(ModelTimeout):
        await engine(respond, timeout_seconds=.1).reply("q", history=[], context=None, state=None, propose=forbidden)
    task = asyncio.create_task(engine(respond).reply("q", history=[], context=None, state=None, propose=forbidden))
    await asyncio.sleep(.1); task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_tool_budget_and_duplicate_proposal_guard():
    received = []
    async def propose(candidate, explanation):
        received.append(candidate)
        return {"proposal": {"id": "p"}, "no_change": False}
    n = 0
    def respond(req):
        nonlocal n
        n += 1
        calls = [call("propose_plan_change", {"candidate": {}, "explanation": "x"}, str(i)) for i in range(2)]
        return httpx.Response(200, json=completion(calls=calls) if n == 1 else completion("Review the proposal"))
    result = await engine(respond).reply("edit", history=[], context={"read_only":False}, state=None, propose=propose)
    assert len(received) == 1
    assert result.proposal == {"id":"p"}
    n = 0
    with pytest.raises(ModelProviderError):
        await engine(respond, max_tool_calls=1).reply("edit", history=[], context={"read_only":False}, state=None, propose=propose)


async def test_state_restore_does_not_duplicate_history_or_restore_permissions(tmp_path):
    first = await engine(lambda req: httpx.Response(200, json=completion("first answer"))).reply("first question", history=[], context={"read_only":False}, state=None, propose=forbidden)
    first.state["agent"]["tool_context"] = {"activated_groups":["evil"]}
    def respond(req):
        body = json.loads(req.content)
        assert "propose_plan_change" not in str(body.get("tools"))
        assert str(body["messages"]).count("first question") == 1
        assert "evil" not in str(body)
        return httpx.Response(200, json=completion("second answer"))
    instance = engine(respond)
    instance.resource_root = tmp_path / "missing"
    second = await instance.reply("second", history=[{"role":"user","text":"first question"}], context=None, state=first.state, propose=forbidden)
    assert second.metrics["state_restored"] is True
    assert second.metrics["resource_source"] == "bundled"


async def test_cancel_during_callback_stops_followup_model_request():
    entered = asyncio.Event(); requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("propose_plan_change", {"candidate":{},"explanation":"x"})]))
    async def propose(*args):
        entered.set(); await asyncio.sleep(10)
    task = asyncio.create_task(engine(respond).reply("edit", history=[], context={"read_only":False}, state=None, propose=propose))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1


async def test_14_station_candidate_reaches_bound_callback_for_canonicalization():
    n = 0; received = []
    stations = [f"S{i:03}" for i in range(14)]
    def respond(req):
        nonlocal n
        n += 1
        return httpx.Response(200, json=completion(calls=[call("propose_plan_change", {"candidate":{"stations":stations,"station_group_id":"default_15"},"explanation":"remove station"})]) if n == 1 else completion("Changed selection for review"))
    async def propose(candidate, explanation):
        received.append(candidate)
        # Canonicalization/domain schema validation belongs to the bound service.
        assert candidate["stations"] == stations
        return {"proposal":{"id":"p14","candidate":{**candidate,"station_group_id":"custom"}},"no_change":False}
    result = await engine(respond).reply("remove", history=[], context={"read_only":False,"experiment_id":"server-only"}, state=None, propose=propose)
    assert result.proposal["candidate"]["station_group_id"] == "custom"
    assert len(received) == 1


async def test_native_skill_discovery_is_on_demand_and_budgeted():
    n = 0
    def respond(req):
        nonlocal n
        n += 1; body = json.loads(req.content)
        if n == 1:
            assert any(t["function"]["name"] == "Skill" for t in body["tools"])
            assert "Example: explain float versus fixed ambiguity" not in str(body["messages"])
            return httpx.Response(200, json=completion(calls=[call("Skill", {"skill":"gnss-theory"})]))
        assert "Example: explain float versus fixed ambiguity" in str(body["messages"])
        return httpx.Response(200, json=completion("Theory answer"))
    result = await engine(respond).reply("explain", history=[], context=None, state=None, propose=forbidden)
    assert result.metrics["tool_calls"] == 1
    assert result.metrics["physical_model_requests"] == 2


@pytest.mark.parametrize("proxy_key,proxy_value", [("HTTPS_PROXY","http://proxy.example:8080"), ("ALL_PROXY","socks5://proxy.example:1080")])
async def test_conversation_proxy_routing_and_client_closure(monkeypatch, proxy_key, proxy_value):
    from great_agent.runtime.conversation_agent import AgentScopeConversationEngine
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(proxy_key, proxy_value)
    created = {}; closed = set(); used = []
    real_init = httpx.AsyncHTTPTransport.__init__
    real_exit = httpx.AsyncHTTPTransport.__aexit__
    def init(transport, *args, **kwargs):
        proxy = kwargs.get("proxy")
        created[id(transport)] = str(proxy.url) if proxy else None
        real_init(transport, *args, **kwargs)
    async def respond(transport, request):
        used.append(created[id(transport)])
        return httpx.Response(200, json=completion("Proxy answer"))
    async def close(transport, *args):
        await real_exit(transport, *args); closed.add(id(transport))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", init)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__aexit__", close)
    instance = AgentScopeConversationEngine(ModelSettings(base_url="https://model.example/v1",name="fixture", api_key="fixture-key"), resource_root=Path("config/agent"))
    answer = await instance.reply("hello", history=[],context=None,state=None,propose=forbidden)
    assert answer.text == "Proxy answer"
    assert used == [proxy_value]
    assert set(created) == closed


async def test_skill_calls_share_budget_and_sdk_logs_are_safe(caplog, capsys):
    import logging
    caplog.set_level(logging.DEBUG)
    def respond(req):
        return httpx.Response(200, json=completion(calls=[call("Skill", {"skill":"gnss-theory"}, str(i)) for i in range(3)]))
    with pytest.raises(ModelProviderError):
        await engine(respond, max_tool_calls=1).reply("PRIVATE_USER_PROMPT", history=[], context=None, state=None, propose=forbidden)
    captured = capsys.readouterr()
    output = caplog.text + captured.out + captured.err
    assert "PRIVATE_USER_PROMPT" not in output
    assert "SECRET" not in output
