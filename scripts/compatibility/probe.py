"""Isolated SDK experiment. Default: synthetic MockTransport only; --live opts in.

No GREAT services, solver, database, research input, or production provider are used.
Only AppSettings.load is imported in live mode. Exceptions are reported by type,
never str(error), because provider errors can contain response bodies or credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import sys
import time

import httpx


class ProbeBudgetExceeded(RuntimeError):
    pass


class DeadlineStream(httpx.AsyncByteStream):
    def __init__(self, inner, deadline):
        self.inner, self.deadline = inner, deadline

    async def __aiter__(self):
        iterator = self.inner.__aiter__()
        while True:
            try:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeBudgetExceeded("deadline")
                yield await asyncio.wait_for(anext(iterator), remaining)
            except StopAsyncIteration:
                return

    async def aclose(self):
        await self.inner.aclose()


class BudgetTransport(httpx.AsyncBaseTransport):
    """Counts physical attempts, including SDK retries/fallbacks, before send."""
    def __init__(self, inner, *, limit=8, seconds=180):
        if not 0 <= limit <= 8 or not 0 <= seconds <= 180:
            raise ValueError("probe authorization ceiling")
        self.inner = inner
        self.limit = limit
        self.deadline = time.monotonic() + seconds
        self.requests = 0

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        caps = [body[k] for k in ("max_tokens", "max_completion_tokens") if body.get(k) is not None]
        if not caps or any(type(v) is not int or not 1 <= v <= 512 for v in caps):
            raise ProbeBudgetExceeded("output token ceiling")
        remaining = self.deadline - time.monotonic()
        if self.requests >= self.limit or remaining <= 0:
            raise ProbeBudgetExceeded("request/deadline ceiling")
        self.requests += 1
        response = await asyncio.wait_for(self.inner.handle_async_request(request), remaining)
        response.stream = DeadlineStream(response.stream, self.deadline)
        return response

    async def aclose(self):
        await self.inner.aclose()


TOOL = {"type": "function", "function": {"name": "synthetic_echo", "description": "Return a synthetic integer; no side effects.", "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False}}}


def sse(*, usage=True):
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "synthetic-call", "type": "function", "function": {"name": "synthetic_echo", "arguments": '{"value":'}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "7}"}}]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    if usage:
        chunks.append({"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}})
    return "".join("data: " + json.dumps({"id": "synthetic", "object": "chat.completion.chunk", "created": 0, "model": "synthetic", **c}) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def sdk_model(http_client, *, live_settings=None, stream=True, legacy=False):
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel
    settings = live_settings
    extra = {"thinking": {"type": (settings.thinking or "disabled") if settings else "disabled"}}
    if legacy:
        extra["max_tokens"] = 512
    return OpenAIChatModel(
        credential=OpenAICredential(api_key=settings.api_key if settings else "synthetic-unused", base_url=str(settings.base_url) if settings else "https://synthetic.invalid/v1"),
        model=settings.name if settings else "synthetic",
        parameters=OpenAIChatModel.Parameters(max_tokens=None if legacy else 512, parallel_tool_calls=False),
        stream=stream, max_retries=0,
        client_kwargs={"http_client": http_client, "max_retries": 0}, extra_body=extra,
    )


async def consume(model, *, tools=True):
    from agentscope.message import Msg, TextBlock
    from agentscope.tool import ToolChoice
    kwargs = {"tools": [TOOL], "tool_choice": ToolChoice(mode="synthetic_echo")} if tools else {"response_format": {"type": "json_object"}}
    response = await model([Msg(name="user", role="user", content=[TextBlock(text='Synthetic compatibility check. Call synthetic_echo with value 7. If no tools, return JSON {"value":7}.')])], **kwargs)
    if hasattr(response, "__aiter__"):
        stream = response
        try:
            async for response in stream:
                pass
        finally:
            await stream.aclose()
    return response


async def offline():
    checks = []

    async def check(name, operation):
        try:
            metadata = await operation()
            checks.append({"name": name, "status": "pass", **(metadata or {})})
        except Exception as error:
            checks.append({"name": name, "status": "fail", "error_type": type(error).__name__})

    async def streaming(usage=True, legacy=False):
        captured = []
        def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse(usage=usage))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await consume(sdk_model(client, legacy=legacy))
        body = captured[0]
        assert len(captured) == 1 and body["stream_options"] == {"include_usage": True}
        assert body["tool_choice"]["function"]["name"] == "synthetic_echo"
        assert body["tools"][0]["function"]["name"] == "synthetic_echo"
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_tokens" if legacy else "max_completion_tokens"] == 512
        assert ("max_completion_tokens" if legacy else "max_tokens") not in body
        assert response.is_last
        blocks = [b for b in response.content if b.type == "tool_call"]
        assert len(blocks) == 1 and json.loads(blocks[0].input) == {"value": 7}
        assert (response.usage is not None) == usage
        if usage:
            assert response.usage.input_tokens == 11 and response.usage.output_tokens == 5
        return {"requests": len(captured), "request_keys": sorted(body), "is_last": response.is_last}

    await check("stream_tools_fragments_end_usage_thinking_native_tokens", streaming)
    await check("stream_without_usage", lambda: streaming(usage=False))
    await check("vendor_max_tokens_public_extra_body", lambda: streaming(legacy=True))

    async def error_case(status):
        requests = []
        def handler(request):
            requests.append(1)
            return httpx.Response(status, json={"error": {"message": "synthetic rejection", "type": "synthetic"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await consume(sdk_model(client))
            except Exception as error:
                assert getattr(error, "status_code", None) == status and len(requests) == 1
                return {"requests": 1, "http_status": status, "error_type": type(error).__name__}
        raise AssertionError("error ignored")

    for status in (400, 401, 422, 429, 500, 503):
        await check(f"http_{status}_no_retry", lambda status=status: error_case(status))

    async def connection_timeout():
        def handler(request):
            raise httpx.ConnectTimeout("synthetic", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await consume(sdk_model(client))
            except Exception as error:
                assert type(error).__name__ == "APITimeoutError"
                return {"error_type": type(error).__name__}
        raise AssertionError("timeout ignored")
    await check("connect_timeout", connection_timeout)

    async def stream_cleanup(cancel=False):
        class SlowStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                if cancel:
                    raise asyncio.CancelledError()
                await asyncio.sleep(10)
                yield b""
            async def aclose(self):
                self.closed = True
        stream = SlowStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))) as client:
            try:
                async with asyncio.timeout(0.05):
                    response = await consume(sdk_model(client))
                    assert str(response.finished_reason).lower().endswith("interrupted")
            except TimeoutError:
                assert not cancel
        assert stream.closed
        return {"stream_closed": stream.closed, "note": "SDK may convert cancellation to interrupted terminal response; caller must inspect reason"}
    await check("total_deadline_stream_cleanup", stream_cleanup)
    await check("cancellation_stream_cleanup", lambda: stream_cleanup(cancel=True))

    async def structured(explicit):
        from agentscope.message import Msg, TextBlock
        from agentscope.tool import ToolChoice
        requests = []
        def handler(request):
            requests.append(1)
            return httpx.Response(400, json={"error": {"message": "synthetic unsupported schema"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = sdk_model(client, stream=False)
            try:
                await model.generate_structured_output([Msg(name="user", role="user", content=[TextBlock(text="Synthetic JSON")])], {"type": "object", "properties": {"value": {"type": "integer"}}}, **({"tool_choice": ToolChoice(mode="generate_structured_output")} if explicit else {}))
            except Exception:
                assert len(requests) == (1 if explicit else 3)
                return {"requests": len(requests), "note": "Explicit tool_choice bypasses fallback ladder"}
        raise AssertionError("unsupported schema ignored")
    await check("structured_default_fallback_ignores_single_request_assumption", lambda: structured(False))
    await check("structured_explicit_tool_choice_single_request", lambda: structured(True))
    return checks


async def live(config):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/src"))
    from great_agent.config import AppSettings
    try:
        settings = AppSettings.load(config, os.environ).model
    except Exception as error:
        return {"status": "fail", "requests": 0, "error_type": type(error).__name__}
    if not settings.api_key.get_secret_value():
        return {"status": "skipped", "reason": "GREAT_AGENT_MODEL_API_KEY absent from process environment", "requests": 0}
    budget = BudgetTransport(httpx.AsyncHTTPTransport(retries=0), limit=8, seconds=180)
    cases = []
    start = time.monotonic()
    async with httpx.AsyncClient(transport=budget, follow_redirects=False, timeout=httpx.Timeout(connect=min(settings.connect_timeout_seconds, 30), read=60, write=30, pool=10)) as client:
        for name, stream, legacy, tools in [("sdk_native_stream_tools", True, False, True), ("vendor_max_tokens_stream_tools", True, True, True), ("json_object_baseline", False, True, False)]:
            before = budget.requests
            try:
                async with asyncio.timeout(max(0.001, budget.deadline - time.monotonic())):
                    response = await consume(sdk_model(client, live_settings=settings, stream=stream, legacy=legacy), tools=tools)
                interrupted = "interrupted" in str(response.finished_reason).lower()
                tool_blocks = [b for b in response.content if b.type == "tool_call"]
                tool_ok = any(b.name == "synthetic_echo" and json.loads(b.input) == {"value": 7} for b in tool_blocks)
                text_blocks = [b for b in response.content if b.type == "text"]
                json_ok = False
                if not tools:
                    try:
                        json_ok = json.loads("".join(b.text for b in text_blocks)) == {"value": 7}
                    except (ValueError, TypeError):
                        pass
                valid = not interrupted and time.monotonic() < budget.deadline and (tool_ok if tools else json_ok)
                cases.append({"name": name, "status": "pass" if valid else "fail", "requests": budget.requests - before, "is_last": response.is_last, "interrupted": interrupted, "synthetic_result_valid": tool_ok if tools else json_ok, "usage_present": response.usage is not None})
            except Exception as error:
                cases.append({"name": name, "status": "fail", "requests": budget.requests - before, "error_type": type(error).__name__, "http_status": getattr(error, "status_code", None)})
            if time.monotonic() >= budget.deadline:
                break
    return {"status": "pass" if len(cases) == 3 and all(c["status"] == "pass" for c in cases) else "fail", "requests": budget.requests, "elapsed_seconds": round(time.monotonic() - start, 3), "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, default=Path("config/local.toml"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    # Silence SDK/provider loggers, which may stringify provider response bodies.
    logging.disable(logging.CRITICAL)
    started = time.monotonic()
    try:
        version = importlib.metadata.version("agentscope")
    except importlib.metadata.PackageNotFoundError:
        version = None
    result = {"target_version": "2.0.11", "installed_version": version, "package_status": "installed" if version == "2.0.11" else "unavailable", "mode": "live" if args.live else "offline", "live_request_limit": 8, "output_token_limit": 512, "live_deadline_seconds": 180}
    if version != "2.0.11":
        result["status"] = "unavailable"
    else:
        try:
            result["offline"] = asyncio.run(offline())
            offline_pass = all(c["status"] == "pass" for c in result["offline"])
            result["live"] = asyncio.run(live(args.config)) if args.live and offline_pass else {"status": "skipped", "requests": 0, "reason": "requires --live and passing offline matrix"}
            result["status"] = "pass" if offline_pass and (not args.live or result["live"]["status"] == "pass") else "fail"
        except Exception as error:
            result.update(status="fail", error_type=type(error).__name__)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
