import asyncio
import json

import httpx
import pytest

from great_agent.adapters.model_provider import ModelProviderError, ModelTimeout
from great_agent.config import ModelSettings


def completion(text=None, calls=None):
    return {'id': 'research-fixture', 'object': 'chat.completion', 'created': 1, 'model': 'fixture',
            'choices': [{'index': 0, 'finish_reason': 'tool_calls' if calls else 'stop',
                         'message': {'role': 'assistant', 'content': text,
                                     **({'tool_calls': calls} if calls else {})}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140}}


def tool_call(name, arguments, identifier='t1'):
    return {'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}


class Tools:
    def __init__(self):
        self.evidence = []
        self.calls = []

    def definitions(self):
        return [{'type': 'function', 'function': {'name': name, 'description': 'Read fixed research evidence',
                 'parameters': {'type': 'object', 'properties': {'material_id': {'type': 'string'}},
                                'required': ['material_id'], 'additionalProperties': False}}}
                for name in ('read_experiment_summary', 'read_code_excerpt', 'read_literature_evidence', 'load_prior_evidence')]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        alias = {'read_experiment_summary': 'E1', 'read_code_excerpt': 'C1',
                 'read_literature_evidence': 'L1', 'load_prior_evidence': 'E1'}[name]
        item = {'id': 'id-' + alias, 'alias': alias, 'kind': name, 'title': name,
                'text': {'E1': 'convergence=12 min; provisional endpoint reference',
                         'C1': 'gap resets the continuous window', 'L1': 'assumption: continuous observations'}[alias],
                'locator': {'material_id': arguments['material_id']}, 'sha256': 'a' * 64,
                'quality_flags': [], 'available': True}
        self.evidence.append(item)
        return {'items': [item]}


def engine(handler):
    from great_agent.research.engine import ResearchEngine
    return ResearchEngine(ModelSettings(base_url='https://fixture.invalid/v1', name='glm-4.7',
        thinking='enabled', max_tokens=8192, api_key='TEST-SECRET'), transport=httpx.MockTransport(handler))


async def test_reads_cross_source_evidence_and_preserves_reasoning_configuration():
    sent, traced, returned = [], [], []
    tools = Tools()

    def response(request):
        body = json.loads(request.content); sent.append(body)
        assert body['thinking'] == {'type': 'enabled'}
        assert body['max_tokens'] == 8192
        assert not body['stream']
        if len(sent) == 1:
            return httpx.Response(200, json=completion(calls=[
                tool_call('read_experiment_summary', {'material_id': 'exp'}),
                tool_call('read_code_excerpt', {'material_id': 'code'}, 't2'),
                tool_call('read_literature_evidence', {'material_id': 'paper'}, 't3')]))
        assert 'continuous window' in str(body['messages'])
        return httpx.Response(200, json=completion(json.dumps({'answer': '观察[E1]，实现[C1]与前提[L1]需对照。',
            'source_ids': ['E1', 'C1', 'L1'], 'answer_status': 'partial',
            'hypotheses': [{'text': '缺测可能影响连续窗口', 'support': ['E1', 'C1'], 'opposition': ['L1'], 'status': 'proposed'}]})))

    async def on_request(messages, usage):
        traced.append((messages, usage)); return 'call-' + str(len(traced))

    async def on_response(call_id, response, usage):
        returned.append((call_id, response, usage))

    result = await engine(response).reply('为什么收敛变慢？', context={'goal': '诊断', 'items': []},
        toolset=tools, mode='deep', on_request=on_request, on_response=on_response)
    assert result['source_ids'] == ['E1', 'C1', 'L1']
    assert len(tools.calls) == 3 and len(traced) == len(returned) == 2
    assert result['metrics']['physical_model_requests'] == 2
    assert result['metrics']['tool_calls'] == 3
    assert result['metrics']['total_tokens'] == 280
    assert 'TEST-SECRET' not in json.dumps(result)


async def test_old_alias_is_not_evidence_until_reloaded_in_current_turn():
    payload = {'answer': '旧判断[E1]', 'source_ids': ['E1'], 'answer_status': 'answered'}
    context = {'items': [{'id': 'H1', 'text': '旧假设', 'support': ['stable-evidence']}],
               'messages': [{'role': 'assistant', 'text': '旧回答[E1]', 'status': 'completed'}],
               'followup_target': {'item_id': 'H1'}}
    requests = []

    def forged(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(json.dumps(payload)))

    with pytest.raises(ModelProviderError):
        await engine(forged).reply('针对 H1 继续', context=context, toolset=Tools())
    assert len(requests) == 1 and 'H1' in str(requests[0]['messages'])
    assert '旧回答[E1]' not in str(requests[0]['messages'])

    def reloaded(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(calls=[tool_call('load_prior_evidence', {'material_id': 'old'})])
            if len(requests) == 2 else completion(json.dumps(payload)))

    result = await engine(reloaded).reply('针对 H1 继续', context=context, toolset=Tools())
    assert result['source_ids'] == ['E1']


@pytest.mark.parametrize('response', [
    completion(json.dumps({'answer': '伪造[E99]', 'source_ids': ['E99']})),
    completion(json.dumps({'answer': '无来源', 'source_ids': [], 'observations': [{'text': '实测改善 50%'}]})),
    completion(calls=[tool_call('execute_python', {'material_id': 'secret'})]),
    completion('not json'),
])
async def test_invalid_claim_or_execution_tool_stops_without_paid_repair(response):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    tools = Tools()
    with pytest.raises(ModelProviderError):
        await engine(respond).reply('分析', context={}, toolset=tools)
    assert len(requests) == 1 and not tools.calls


async def test_general_consultation_and_missing_evidence_are_explicit():
    answer = {'answer': '当前没有实验或原文依据；可以先讨论条件假说。', 'source_ids': [],
              'answer_status': 'needs_input', 'open_questions': ['请补充基准实验。']}
    result = await engine(lambda r: httpx.Response(200, json=completion(json.dumps(answer)))).reply(
        '研究思路', context={}, toolset=Tools())
    assert result['answer_status'] == 'needs_input' and result['open_questions']


async def test_physical_request_and_tool_budgets_are_enforced():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion(calls=[tool_call('read_code_excerpt', {'material_id': 'code'}, str(len(requests)))]))

    with pytest.raises(ModelProviderError):
        await engine(respond).reply('q', context={}, toolset=Tools(), budget={'max_model_requests': 2})
    assert len(requests) == 2
    requests.clear()
    tools = Tools()
    with pytest.raises(ModelProviderError):
        await engine(respond).reply('q', context={}, toolset=tools, budget={'max_tool_calls': 1})
    assert len(tools.calls) == 1


@pytest.mark.parametrize('question', ['x' * 8000, '测' * 8000], ids=['ascii', 'cjk'])
async def test_context_budget_rejects_oversized_input_before_provider_send(question):
    requests = []
    with pytest.raises(ModelProviderError):
        await engine(lambda request: requests.append(request)).reply(question, context={},
            toolset=Tools(), mode='quick', budget={'input_tokens': 4000})
    assert not requests


async def test_scope_is_checked_before_each_request_and_no_provider_retry():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503, json={'error': 'TEST-SECRET UPSTREAM BODY'})

    with pytest.raises(ModelProviderError) as caught:
        await engine(respond).reply('q', context={}, toolset=Tools())
    assert len(requests) == 1 and 'TEST-SECRET' not in str(caught.value)


async def test_timeout_and_cancel_do_not_publish_result():
    entered = asyncio.Event()

    async def slow(request):
        entered.set(); await asyncio.sleep(10)

    with pytest.raises(ModelTimeout):
        await engine(slow).reply('q', context={}, toolset=Tools(), budget={'timeout_seconds': .05})
    task = asyncio.create_task(engine(slow).reply('q', context={}, toolset=Tools()))
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_saved_responses_replay_after_restart_without_new_provider_call():
    saved = []

    def response(request):
        body = json.loads(request.content)
        return httpx.Response(200, json=completion(calls=[tool_call('read_code_excerpt', {'material_id': 'code'})])
            if len(saved) == 1 else completion(json.dumps({'answer': '实现[C1]', 'source_ids': ['C1']})))

    async def before(messages, metrics):
        saved.append({'request': {'messages': messages}, 'response': None})
        return str(len(saved) - 1)

    async def after(call_id, response, usage):
        saved[int(call_id)]['response'] = response

    await engine(response).reply('检查窗口', context={}, toolset=Tools(), on_request=before, on_response=after)
    assert len(saved) == 2

    def forbidden(request):
        pytest.fail('completed physical requests must never be repeated during recovery')

    result = await engine(forbidden).reply('检查窗口', context={}, toolset=Tools(), replay_calls=saved)
    assert result['source_ids'] == ['C1'] and result['metrics']['replayed_model_requests'] == 2


def test_same_item_cannot_be_overwritten_twice_and_drop_new_counterevidence():
    from great_agent.research.engine import ResearchEngine
    evidence = [{'alias': 'C1', 'available': True}]
    context = {'items': [{'id': 'hypothesis-1', 'text': 'old'}]}
    with pytest.raises(ModelProviderError):
        ResearchEngine.validate({'answer': '反例[C1]', 'source_ids': ['C1'],
            'hypotheses': [{'id': 'hypothesis-1', 'text': 'challenged', 'opposition': ['C1'], 'status': 'challenged'}],
            'proposed_state_changes': [{'id': 'hypothesis-1', 'text': 'overwritten', 'status': 'proposed'}]}, evidence, context)


async def test_received_invalid_response_is_audited_before_rejection():
    received = []
    envelope = completion('truncated')
    envelope['choices'][0]['finish_reason'] = 'length'

    async def after(call_id, response, usage):
        received.append((response, usage))

    with pytest.raises(ModelProviderError):
        await engine(lambda request: httpx.Response(200, json=envelope)).reply(
            'q', context={}, toolset=Tools(), on_response=after)
    assert len(received) == 1 and received[0][1]['total_tokens'] == 140


def test_user_correction_memory_retains_exact_quote_without_becoming_observation():
    from great_agent.research.engine import ResearchEngine
    payload = {'answer': '将按这个前提继续。', 'source_ids': [],
               'user_statements': [{'text': '用户要求保持统计口径一致', 'quote': '只比较同站同日，不改变统计口径'}]}
    context = {'current_question': '只比较同站同日，不改变统计口径。继续核查。'}
    result = ResearchEngine.validate(payload, [], context)
    assert result['user_statements'][0]['quote'] == '只比较同站同日，不改变统计口径'
    assert not result['observations']
    with pytest.raises(ModelProviderError):
        ResearchEngine.validate(payload, [], {'current_question': '用户没有提出这个要求'})


def test_structured_scientific_number_is_checked_against_original_json_and_units():
    from great_agent.research.engine import ResearchEngine
    evidence = [{'alias': 'E1', 'available': True, 'text': '{"stations":[{"joint_convergence_minutes":12.0}]}'}]
    claim = {'evidence_id': 'E1', 'pointer': '/stations/0/joint_convergence_minutes', 'value': 12, 'unit': 'min'}
    payload = {'answer': '已有收敛时间为 12 分钟[E1]。', 'source_ids': ['E1'], 'numeric_claims': [claim]}
    result = ResearchEngine.validate(payload, evidence)
    assert result['numeric_claims'][0]['value'] == 12
    for wrong in ({'value': 50}, {'unit': 'm'}, {'pointer': '/stations/9/joint_convergence_minutes'}):
        with pytest.raises(ModelProviderError):
            ResearchEngine.validate({**payload, 'numeric_claims': [{**claim, **wrong}]}, evidence)


def test_numeric_claims_support_real_derived_rows_and_extracted_pointer_maps():
    from great_agent.research.engine import ResearchEngine
    sources = [
        {'alias': 'E1', 'text': '{"rows":[{"delta":-1,"relative_change":-0.5,"unit":"m"}]}',
         'locator': {'metric': 'rms_3d_all_epochs_m'}, 'kind': 'derived'},
        {'alias': 'E2', 'text': '{"/stations/0/joint_convergence_minutes":12}', 'locator': {}, 'kind': 'artifact'},
    ]
    claims = [{'evidence_id': 'E1', 'pointer': '/rows/0/delta', 'value': -1, 'unit': 'm'},
              {'evidence_id': 'E1', 'pointer': '/rows/0/relative_change', 'value': -0.5, 'unit': 'fraction'},
              {'evidence_id': 'E2', 'pointer': '/stations/0/joint_convergence_minutes', 'value': 12, 'unit': 'min'}]
    result = ResearchEngine.validate({'answer': '原值[E2]及已有比较[E1]。', 'source_ids': ['E1', 'E2'], 'numeric_claims': claims}, sources)
    assert len(result['numeric_claims']) == 3


def test_existing_user_statement_cannot_be_retyped_or_rewritten_by_model():
    from great_agent.research.engine import ResearchEngine
    context = {'items': [{'id': 'U1', 'kind': 'user_statement', 'text': '保留原口径', 'quote': '不要改口径'}]}
    for field in ('observations', 'hypotheses', 'proposed_state_changes'):
        with pytest.raises(ModelProviderError):
            ResearchEngine.validate({'answer': '新观察[E1]', 'source_ids': ['E1'],
                field: [{'id': 'U1', 'text': '改成模型观察', 'support': ['E1']}]}, [{'alias': 'E1'}], context)


def test_older_explicitly_targeted_user_message_can_supply_an_exact_quote():
    from great_agent.research.engine import ResearchEngine
    context = {'messages': [], 'current_question': '重新核对原约束', 'followup': {
        'messages': [{'id': 'old-user', 'role': 'user', 'text': '不要改变收敛统计口径'}]}}
    result = ResearchEngine.validate({'answer': '按原约束继续。', 'source_ids': [], 'user_statements': [
        {'text': '保留统计口径', 'quote': '不要改变收敛统计口径', 'source_message_id': 'old-user'}]}, [], context)
    assert result['user_statements'][0]['source_message_id'] == 'old-user'
