"""Research budget boundaries and compact navigation, using mocked wire only."""
from copy import deepcopy
import json

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProviderError
from great_agent.config import ModelSettings
from great_agent.research.engine import (
    MODES, SYSTEM, ResearchEngine, _ResearchTransport, _estimated_tokens,
)
from great_agent.research.tools import ResearchToolset
from great_agent.runtime.conversation_agent import _Budget


def completion(text='{"answer":"局部结论","source_ids":[]}', calls=None):
    return {'id': 'budget-fixture', 'object': 'chat.completion', 'created': 1,
            'model': 'fixture', 'choices': [{'index': 0,
                'finish_reason': 'tool_calls' if calls else 'stop',
                'message': {'role': 'assistant', 'content': None if calls else text,
                            **({'tool_calls': calls} if calls else {})}}],
            'usage': {'prompt_tokens': 71, 'completion_tokens': 13, 'total_tokens': 84}}


def native_definitions():
    # Schema construction needs no source files, database, or source permission.
    return object.__new__(ResearchToolset).definitions()


def boundary(*, input_limit=12000, request_limit=4, already_sent=0):
    sent, intents, responses = [], [], []

    def upstream(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=completion(), request=request)

    async def before(messages, metrics):
        intents.append({'messages': messages, 'metrics': metrics})
        return 'recorded-intent'

    async def after(call_id, response, usage):
        responses.append((call_id, response, usage))

    budget = _Budget(request_limit, 10, 30)
    budget.requests = already_sent
    limits = {**MODES['quick'], 'input_tokens': input_limit}
    transport = _ResearchTransport(httpx.MockTransport(upstream), budget, limits,
                                   set(), before, after)
    return transport, budget, sent, intents, responses


async def send(transport, payload):
    request = httpx.Request('POST', 'https://fixture.invalid/v1/chat/completions',
                           json=deepcopy(payload))
    response = await transport.handle_async_request(request)
    await response.aclose()


@pytest.mark.parametrize(('text', 'expected'), [
    ('', 0), ('abcde', 3), ('{}[]!?', 3), ('中文', 4),
    ('🙂', 4), ('A中🙂', 7), ('café', 4),
])
def test_mixed_estimate_accounts_for_unicode_and_code_punctuation(text, expected):
    assert _estimated_tokens(text) == expected
    assert _estimated_tokens(text + '🙂') == expected + 4


async def test_exact_request_budget_is_allowed_and_one_token_over_has_no_side_effects():
    payload = {'messages': [{'role': 'system', 'content': SYSTEM},
                            {'role': 'user', 'content': '比较现有指标并检查 reset(x != 0); 🙂'}],
               'tools': native_definitions(), 'max_tokens': 8192}
    probe, _, _, recorded, _ = boundary()
    await send(probe, payload)
    estimate = recorded[0]['metrics']['input_tokens_estimated']
    assert type(estimate) is int and 0 < estimate < MODES['quick']['input_tokens']
    assert recorded[0]['metrics']['token_count_mode'] == 'estimated_conservative'

    exact, budget, sent, intents, responses = boundary(input_limit=estimate)
    await send(exact, payload)
    assert budget.requests == len(sent) == len(intents) == len(responses) == 1
    assert sent[0]['max_tokens'] == MODES['quick']['output_tokens']
    assert intents[0]['metrics']['input_tokens_estimated'] == estimate
    assert responses[0][2]['prompt_tokens'] == 71  # measured usage stays separate

    rejected, budget, sent, intents, responses = boundary(input_limit=estimate - 1)
    with pytest.raises(ModelProviderError, match='context budget'):
        await send(rejected, payload)
    assert budget.requests == 0
    assert not sent and not intents and not responses


async def test_final_request_instruction_is_counted_before_intent_and_external_send():
    payload = {'messages': [{'role': 'user', 'content': '给出局部结论'}], 'tools': []}
    ordinary, _, _, ordinary_intents, _ = boundary(request_limit=2)
    await send(ordinary, payload)
    ordinary_estimate = ordinary_intents[0]['metrics']['input_tokens_estimated']

    rejected, budget, sent, intents, _ = boundary(
        input_limit=ordinary_estimate, request_limit=2, already_sent=1)
    with pytest.raises(ModelProviderError, match='context budget'):
        await send(rejected, payload)
    assert budget.requests == 1 and not sent and not intents

    final, _, sent, intents, _ = boundary(request_limit=2, already_sent=1)
    await send(final, payload)
    assert sent[0]['tool_choice'] == 'none'
    assert len(sent[0]['messages']) == len(payload['messages']) + 1
    assert sent[0]['messages'][-1]['role'] == 'system'
    assert intents[0]['metrics']['input_tokens_estimated'] > ordinary_estimate


class EvidenceTools:
    def __init__(self, text='static reset_window implementation', *, all_schemas=False):
        self.text = text
        self.all_schemas = all_schemas
        self.evidence, self.calls = [], []

    def definitions(self):
        return [definition for definition in native_definitions()
                if self.all_schemas or definition['function']['name'] == 'read_code_excerpt']

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        ident = arguments.get('evidence_id', 'evidence:support')
        existing = next((item for item in self.evidence if item['id'] == ident), None)
        if existing is None:
            existing = {'id': ident, 'alias': f'C{len(self.evidence) + 1}', 'kind': 'code',
                        'title': 'solver.cpp', 'text': self.text,
                        'locator': {'material_id': 'code-version', 'path': 'solver.cpp'},
                        'available': True}
            self.evidence.append(existing)
        return {'evidence': deepcopy(existing)}


def engine(handler):
    settings = ModelSettings(base_url='https://fixture.invalid/v1', name='glm-4.7',
                             api_key='fixture-only', thinking='enabled', max_tokens=8192)
    return ResearchEngine(settings, transport=httpx.MockTransport(handler))


def tool_call(name, arguments, identifier):
    return {'id': identifier, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}


@pytest.mark.parametrize(('text', 'allowed'), [
    ('return(0);\n' * 1250, True),  # >12k ASCII characters; within quick token estimate
    ('🙂' * 3100, False),  # <12k characters but >12k estimated tokens
], ids=['ascii-code-within-estimate', 'emoji-over-estimate'])
async def test_tool_result_uses_mixed_estimate_instead_of_character_limit(text, allowed):
    sent, intents = [], []
    tools = EvidenceTools(text)

    def respond(request):
        sent.append(json.loads(request.content))
        response = completion(calls=[tool_call('read_code_excerpt', {'material_id': 'code-version'}, 'read')]) \
            if len(sent) == 1 else completion('{"answer":"静态观察[C1]","source_ids":["C1"]}')
        return httpx.Response(200, json=response)

    async def before(messages, metrics):
        intents.append(metrics)
        return str(len(intents))

    call = engine(respond).reply('检查所选源码', context={}, toolset=tools,
                                 mode='quick', on_request=before)
    if allowed:
        result = await call
        assert result['source_ids'] == ['C1']
        assert len(sent) == len(intents) == 2
        assert all(item['input_tokens_estimated'] <= 12000 for item in intents)
    else:
        with pytest.raises(ModelProviderError):
            await call
        assert len(sent) == len(intents) == len(tools.calls) == 1


async def test_quick_navigation_keeps_counterevidence_user_quote_and_older_target():
    quote = '只比较同站同日，不要改变收敛统计口径'
    hypothesis = {'id': 'H1', 'kind': 'hypothesis', 'text': '缺测可能影响重置窗口',
                  'status': 'challenged', 'support': ['evidence:support'],
                  'opposition': ['evidence:opposition'], 'unknowns': ['二进制关联未知'],
                  'origin_run_id': 'old-run', 'revision': 90,
                  'support_refs': [{'run_id': f'audit-only-run-{index}', 'alias': 'C99',
                                    'evidence_id': 'evidence:support'} for index in range(64)],
                  'opposition_refs': [{'run_id': f'audit-only-run-{index}', 'alias': 'C98',
                                       'evidence_id': 'evidence:opposition'} for index in range(64)]}
    user_item = {'id': 'U1', 'kind': 'user_statement', 'text': '保持比较口径',
                 'quote': quote, 'source_message_id': 'old-user', 'status': 'user_provided'}
    context = {'goal': '解释收敛差异', 'items': [hypothesis, user_item],
               'messages': [{'id': f'recent-{index}', 'role': 'user' if index % 2 == 0 else 'assistant',
                             'text': f'最近讨论 {index}', 'status': 'completed'} for index in range(8)],
               'followup_target': {'run_id': 'old-run', 'item_id': 'H1'},
               'followup': {'run_id': 'old-run', 'items': [deepcopy(hypothesis)],
                            'messages': [{'id': 'old-user', 'role': 'user', 'text': quote}]},
               'prior_evidence': [{'id': ident, 'kind': 'code', 'title': 'solver.cpp',
                                   'locator': {'material_id': 'code-version', 'path': 'solver.cpp',
                                               'start_line': 1, 'end_line': 2,
                                               'material_sha256': 'a' * 64, 'byte_end': 1000000}}
                                  for ident in ('evidence:support', 'evidence:opposition')]}
    original = deepcopy(context)
    sent = []

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if not any(message['role'] == 'tool' for message in payload['messages']):
            return httpx.Response(200, json=completion(calls=[
                tool_call('load_prior_evidence', {'evidence_id': 'evidence:support'}, 'support'),
                tool_call('load_prior_evidence', {'evidence_id': 'evidence:opposition'}, 'opposition')]))
        return httpx.Response(200, json=completion(json.dumps({
            'answer': '支持[C1]与反证[C2]仍需共同核查。', 'source_ids': ['C1', 'C2'],
            'user_statements': [{'text': '保持统计口径', 'quote': quote, 'source_message_id': 'old-user'}],
        }, ensure_ascii=False)))

    result = await engine(respond).reply('针对 H1 继续核查', context=context,
                                         toolset=EvidenceTools(all_schemas=True), mode='quick')
    assert len(sent) == 2
    content = sent[0]['messages'][-1]['content']
    prompt = content if isinstance(content, str) else ''.join(block.get('text', '') for block in content)
    navigation = json.loads(prompt.split('\n', 1)[1].rsplit('\n本轮问题：\n', 1)[0])
    items = {item['id']: item for item in navigation['items']}
    assert items['H1']['status'] == 'challenged'
    assert items['H1']['support'] == ['evidence:support']
    assert items['H1']['opposition'] == ['evidence:opposition']
    assert items['H1']['unknowns'] == ['二进制关联未知']
    assert items['U1']['quote'] == quote
    assert navigation['followup']['messages'][0]['text'] == quote
    assert navigation['followup']['items'] == []
    assert 'audit-only-run-' not in prompt
    assert {item['id'] for item in navigation['prior_evidence']} == {
        'evidence:support', 'evidence:opposition'}
    assert all(item['locator']['path'] == 'solver.cpp' for item in navigation['prior_evidence'])
    assert all('material_sha256' not in item['locator'] for item in navigation['prior_evidence'])
    assert len(sent[0]['tools']) == 11
    assert result['user_statements'][0]['quote'] == quote
    assert result['metrics']['physical_model_requests'] == 2
    assert result['metrics']['tool_calls'] == 2
    assert all(payload['max_tokens'] == MODES['quick']['output_tokens'] for payload in sent)
    assert context == original  # compact prompt construction cannot rewrite canonical state
