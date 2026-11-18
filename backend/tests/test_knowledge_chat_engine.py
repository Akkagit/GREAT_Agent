import asyncio
import json

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProviderError, ModelTimeout
from great_agent.config import ModelSettings


def completion(text=None, calls=None):
    return {"id": "fixture", "object": "chat.completion", "created": 1, "model": "glm-4.7",
            "choices": [{"index": 0, "finish_reason": "tool_calls" if calls else "stop",
                         "message": {"role": "assistant", "content": text,
                                     **({"tool_calls": calls} if calls else {})}}]}


def call(name, args, ident="c1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def engine(handler, **kwargs):
    from great_agent.runtime.knowledge_chat_engine import KnowledgeChatEngine
    return KnowledgeChatEngine(ModelSettings(base_url="https://fixture.invalid/v1", name="glm-4.7", api_key="SECRET"),
                               transport=httpx.MockTransport(handler), **kwargs)


async def evidence(query):
    return {"items": [{"source_id": "S1", "text": "PPP convergence is assessed in minutes."}]}


async def read(source_ids):
    assert source_ids == ["S1"]
    return await evidence("")


async def test_native_tools_citations_and_actual_outbound_trace():
    requests, traces = [], []
    async def trace(messages, budget):
        traces.append((messages, budget))
    def respond(req):
        body = json.loads(req.content); requests.append(body)
        assert {t["function"]["name"] for t in body["tools"]} == {"search_knowledge", "read_evidence"}
        assert body["stream"] is False and body["max_tokens"] == 2048
        assert body["thinking"] == {"type": "disabled"}
        if len(requests) == 1:
            return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "收敛 PPP convergence"})]))
        if len(requests) == 2:
            assert "PPP convergence" in str(body["messages"])
            return httpx.Response(200, json=completion(calls=[call("read_evidence", {"source_ids": ["S1"]}, "c2")]))
        return httpx.Response(200, json=completion(json.dumps({"answer": "按分钟评估[S1]", "source_ids": ["S1"], "answerable": True})))
    result = await engine(respond).reply("如何评估收敛", history=[{"role": "user", "text": "previous"}],
                                          search_knowledge=evidence, read_evidence=read, on_request=trace)
    assert result["source_ids"] == ["S1"] and result["answerable"] is True
    assert result["metrics"]["physical_model_requests"] == 3
    assert len(traces) == 3 and traces[-1][0] == requests[-1]["messages"]
    assert traces[-1][1]["token_count_mode"] == "estimated"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("answer", [
    {"answer": "forged[S99]", "source_ids": ["S99"], "answerable": True},
    {"answer": "forged[S99]", "source_ids": ["S1"], "answerable": True},
    {"answer": "no evidence", "source_ids": [], "answerable": True},
])
async def test_forged_or_unsupported_answer_rejected(answer):
    n = 0
    def respond(req):
        nonlocal n
        n += 1
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "PPP"})]) if n == 1
                              else completion(json.dumps(answer)))
    with pytest.raises(ModelProviderError):
        await engine(respond).reply("q", history=[], search_knowledge=evidence, read_evidence=read)


async def test_no_evidence_is_explicitly_unanswerable():
    result = await engine(lambda req: httpx.Response(200, json=completion(json.dumps(
        {"answer": "当前文献证据不足，无法回答。", "source_ids": [], "answerable": False})))).reply(
            "q", history=[], search_knowledge=evidence, read_evidence=read)
    assert result["answerable"] is False and result["source_ids"] == []


async def test_unknown_tool_never_executes_and_provider_errors_are_private():
    for response in [httpx.Response(500, json={"error": "SECRET BODY"}),
                     httpx.Response(200, json=completion(calls=[call("create_experiment", {"path": "SECRET"})]))]:
        requests = []
        def respond(req):
            requests.append(req)
            return response
        with pytest.raises(ModelProviderError) as caught:
            await engine(respond).reply("q", history=[], search_knowledge=evidence, read_evidence=read)
        assert "SECRET" not in str(caught.value) and len(requests) == 1


async def test_question_and_evidence_budgets_stop_outbound():
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "PPP"})]))
    with pytest.raises((ValueError, ModelProviderError)):
        await engine(respond).reply("q" * 2001, history=[], search_knowledge=evidence, read_evidence=read)
    assert not requests
    async def large(query):
        return {"items": [{"source_id": "S1", "text": "X" * 12500}]}
    with pytest.raises(ModelProviderError):
        await engine(respond).reply("q", history=[], search_knowledge=large, read_evidence=read)
    assert len(requests) == 1


async def test_request_limit_timeout_and_cancel():
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "PPP"}, str(len(requests)))]))
    with pytest.raises(ModelProviderError):
        await engine(respond, max_model_requests=2).reply("q", history=[], search_knowledge=evidence, read_evidence=read)
    assert len(requests) == 2
    entered = asyncio.Event()
    async def slow(req):
        entered.set(); await asyncio.sleep(10)
    with pytest.raises(ModelTimeout):
        await engine(slow, timeout_seconds=.05).reply("q", history=[], search_knowledge=evidence, read_evidence=read)
    task = asyncio.create_task(engine(slow).reply("q", history=[], search_knowledge=evidence, read_evidence=read))
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_real_token_counter_evidence_limit_is_cumulative():
    requests = []
    def respond(req):
        requests.append(req)
        # Two separately fitting evidence packages must not bypass the total.
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "PPP"}, str(len(requests)))])
                              if len(requests) <= 2 else completion(json.dumps({"answer": "ok[S1]", "source_ids": ["S1"], "answerable": True})))
    def count(text):
        if text.startswith('{"items":'):
            return 4000
        return 100
    with pytest.raises(ModelProviderError):
        await engine(respond, token_counter=count).reply("q", history=[], search_knowledge=evidence, read_evidence=read)
    assert len(requests) == 2


async def test_unknown_read_id_stops_before_server_callback_and_next_request():
    requests, reads = [], []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("read_evidence", {"source_ids": ["S99"]})]))
    async def record(source_ids):
        reads.append(source_ids)
        return {}
    with pytest.raises(ModelProviderError):
        await engine(respond).reply("q", history=[], search_knowledge=evidence, read_evidence=record)
    assert len(requests) == 1 and reads == []


async def test_scope_revalidation_can_stop_every_physical_request():
    requests, traces = [], []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "PPP"})]))
    async def scope(messages, budget):
        traces.append(messages)
        if len(traces) == 2:
            raise ValueError("SECRET revoked")
    with pytest.raises(ModelProviderError) as caught:
        await engine(respond).reply("q", history=[], search_knowledge=evidence, read_evidence=read, on_request=scope)
    assert len(requests) == 1 and len(traces) == 2
    assert "SECRET" not in str(caught.value)


async def test_seventh_tool_call_is_not_executed():
    requests, queries = [], []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": str(i)}, str(i)) for i in range(7)]))
    async def search(query):
        queries.append(query)
        return await evidence(query)
    with pytest.raises(ModelProviderError):
        await engine(respond).reply("q", history=[], search_knowledge=search, read_evidence=read)
    assert len(requests) == 1 and len(queries) == 6


async def test_history_keeps_recent_whole_messages_within_budget():
    requests = []
    def respond(req):
        body = json.loads(req.content); requests.append(body)
        return httpx.Response(200, json=completion(json.dumps({"answer": "Evidence insufficient.", "source_ids": [], "answerable": False})))
    result = await engine(respond).reply("current", history=[{"role": "user", "text": "OLD" * 2000},
        {"role": "assistant", "text": "RECENT"}], search_knowledge=evidence, read_evidence=read)
    assert "RECENT" in str(requests[0]["messages"]) and "OLDOLD" not in str(requests[0]["messages"])
    assert result["metrics"]["history_chars"] == 6


_ANSWER_JSON = json.dumps({"answer": "Only this answer[S1]", "source_ids": ["S1"], "answerable": True})


async def answer_from_completion(raw):
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json=completion(calls=[call("search_knowledge", {"query": "PPP"})])
                              if len(requests) == 1 else completion(raw))
    return await engine(respond).reply("q", history=[], search_knowledge=evidence, read_evidence=read)


@pytest.mark.parametrize("raw", [
    _ANSWER_JSON,
    "```json\n" + _ANSWER_JSON + "\n```",
    "```json " + _ANSWER_JSON + "```",
    "Provider preface that must never be shown.\n\n```json\n" + _ANSWER_JSON + "\n```",
])
async def test_unique_answer_object_accepts_plain_fenced_or_prefaced_json(raw):
    result = await answer_from_completion(raw)
    assert result["text"] == "Only this answer[S1]"
    assert result["source_ids"] == ["S1"]
    assert result["answerable"] is True


@pytest.mark.parametrize("raw", [
    "```json\n" + _ANSWER_JSON + "\n```\n```json\n{}\n```",
    "```text\nother\n```\n```json\n" + _ANSWER_JSON + "\n```",
    "```json\n" + _ANSWER_JSON + "\n{}\n```",
    "```json\n" + _ANSWER_JSON + "\n```\n{}",
    "```json\n" + _ANSWER_JSON + "\n```\nAnother object: {}",
    "{}\n```json\n" + _ANSWER_JSON + "\n```",
    "```json\n" + _ANSWER_JSON + "\n```\nnull",
    "```json\n[" + _ANSWER_JSON + "]\n```",
    "```json\n" + _ANSWER_JSON,
    "Preface without a fence: " + _ANSWER_JSON,
    '{"answer":"First[S1]","answer":"Second[S1]","source_ids":["S1"],"answerable":true}',
])
async def test_ambiguous_or_multiple_answer_payloads_are_rejected(raw):
    with pytest.raises(ModelProviderError):
        await answer_from_completion(raw)


@pytest.mark.parametrize("answer", [
    {"answer": "Forged[S99]", "source_ids": ["S99"], "answerable": True},
    {"answer": "Forged[S99]", "source_ids": ["S1"], "answerable": True},
    {"answer": "Only this answer[S1]", "source_ids": ["S1"], "answerable": "true"},
])
async def test_prefaced_json_still_requires_known_citations_and_boolean(answer):
    with pytest.raises(ModelProviderError):
        await answer_from_completion("Preface\n```json\n" + json.dumps(answer) + "\n```")
