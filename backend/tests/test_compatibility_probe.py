"""Safety boundaries for the isolated probe, never a production integration test."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


PROBE = Path(__file__).resolve().parents[2] / "scripts/compatibility/probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("p0_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_budget_counts_actual_transport_attempts_and_blocks_ninth():
    probe = load_probe()
    seen = []

    async def run():
        transport = probe.BudgetTransport(httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200, json={}))[1]), limit=8, seconds=180)
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(8):
                await client.post("https://synthetic.invalid/chat/completions", json={"max_tokens": 512})
            with pytest.raises(probe.ProbeBudgetExceeded):
                await client.post("https://synthetic.invalid/chat/completions", json={"max_tokens": 512})
        assert transport.requests == len(seen) == 8

    asyncio.run(run())


@pytest.mark.parametrize("body", [{}, {"max_tokens": 513}, {"max_tokens": 512, "max_completion_tokens": 513}])
def test_budget_refuses_missing_or_excessive_output_cap(body):
    probe = load_probe()

    async def run():
        transport = probe.BudgetTransport(httpx.MockTransport(lambda _: pytest.fail("network reached")), limit=8, seconds=180)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(probe.ProbeBudgetExceeded):
                await client.post("https://synthetic.invalid/chat/completions", json=body)
        assert transport.requests == 0

    asyncio.run(run())


def test_expired_deadline_blocks_request_and_closes_transport():
    probe = load_probe()

    class Inner(httpx.AsyncBaseTransport):
        closed = False

        async def handle_async_request(self, request):
            pytest.fail("network reached")

        async def aclose(self):
            self.closed = True

    async def run():
        inner = Inner()
        transport = probe.BudgetTransport(inner, limit=8, seconds=0)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(probe.ProbeBudgetExceeded):
                await client.post("https://synthetic.invalid/chat/completions", json={"max_tokens": 512})
        assert inner.closed

    asyncio.run(run())
