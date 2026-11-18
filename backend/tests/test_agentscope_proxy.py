from __future__ import annotations

import asyncio

import httpx
import pytest

from great_agent.adapters.agentscope_model import AgentScopeModelProvider
from great_agent.config import ModelSettings
from test_agentscope_model import completion, proposal_json
from test_chat import base_spec  # noqa: F401


@pytest.mark.parametrize("no_proxy,https_proxy,all_proxy,expected_proxy", [
    ("", "http://proxy.example:8080", None, "http://proxy.example:8080"),
    ("model.example", "http://proxy.example:8080", None, None),
    ("", None, "socks5://proxy.example:1080", "socks5://proxy.example:1080"),
    ("", "http://proxy.example:8080", "socks5://proxy.example:1080", "http://proxy.example:8080"),
])
async def test_default_client_honors_environment_proxy_and_exclusion(
    base_spec, monkeypatch, no_proxy, https_proxy, all_proxy, expected_proxy,
):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    if https_proxy is not None:
        monkeypatch.setenv("HTTPS_PROXY", https_proxy)
    if all_proxy is not None:
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
        monkeypatch.setenv("ALL_PROXY", all_proxy)
    monkeypatch.setenv("NO_PROXY", no_proxy)
    configured = {}
    used = []
    real_init = httpx.AsyncHTTPTransport.__init__

    def configured_transport(transport, *args, **kwargs):
        proxy = kwargs.get("proxy")
        configured[id(transport)] = str(proxy.url) if proxy is not None else None
        real_init(transport, *args, **kwargs)

    async def respond(transport, request):
        used.append(configured[id(transport)])
        return httpx.Response(200, json=completion(proposal_json(base_spec)))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", configured_transport)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    result = await AgentScopeModelProvider(ModelSettings(
        base_url="https://model.example/v1", name="fixture", api_key="fixture-key",
    )).propose_plan("change", base_spec)
    assert result.candidate == base_spec
    assert used == [expected_proxy]


async def test_cancel_closes_default_and_proxy_transports(base_spec, monkeypatch):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    started = asyncio.Event()
    created, closed = set(), set()
    real_init = httpx.AsyncHTTPTransport.__init__
    real_exit = httpx.AsyncHTTPTransport.__aexit__

    def configured_transport(transport, *args, **kwargs):
        created.add(id(transport))
        real_init(transport, *args, **kwargs)

    async def waiting(transport, request):
        started.set()
        await asyncio.Event().wait()

    async def exit_transport(transport, *args):
        await real_exit(transport, *args)
        closed.add(id(transport))

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", configured_transport)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", waiting)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__aexit__", exit_transport)
    sdk_provider = AgentScopeModelProvider(ModelSettings(base_url="https://model.example/v1", name="fixture", api_key="fixture-key"))
    task = asyncio.create_task(sdk_provider.propose_plan("change", base_spec))
    try:
        # Loading Windows CA/proxy configuration can exceed one second. The
        # contract under test is cancellation after the upstream call starts.
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert created and created == closed
