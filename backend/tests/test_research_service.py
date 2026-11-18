import asyncio
from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

import pytest
import httpx
from sqlalchemy.exc import DBAPIError

from great_agent.config import AppSettings
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.research.engine import ResearchEngine


class FixtureEngine:
    def __init__(self):
        self.contexts = []
        self.entered = asyncio.Event()
        self.release = None

    async def reply(self, question, *, context, toolset, on_request, on_response, on_progress, **kwargs):
        self.contexts.append(context)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        material = next(m for m in toolset.snapshot['materials'] if m['kind'] == 'code')
        read = await toolset.call('read_code_excerpt', {'material_id': material['id']})
        await on_progress('analyzing')
        alias = toolset.evidence[0]['alias']
        existing = next((item['id'] for item in context.get('items', []) if item['kind'] == 'hypothesis'), None)
        result = ResearchEngine.validate({'answer': f'静态源码观察[{alias}]；改法仍待人工验证。', 'source_ids': [alias],
            'answer_status': 'partial', 'hypotheses': [{'id': existing, 'text': '缺测可能影响连续窗口',
            'status': 'challenged' if existing else 'proposed', 'support': [alias], 'opposition': [],
            'unknowns': ['实际运行二进制与源码版本尚未关联。']}]}, toolset.evidence, context)
        call_id = await on_request([{'role': 'user', 'content': question}], {'physical_model_requests': 1, 'tool_calls': 1})
        await on_response(call_id, {'choices': [{'message': {'content': result['answer']}}]}, {'total_tokens': 10})
        result['metrics'] = {'physical_model_requests': 1, 'tool_calls': 1, 'total_tokens': 10}
        return result


@pytest.fixture
def research(valid_config):
    from great_agent.research.materials import ResearchMaterials
    from great_agent.research.repository import ResearchRepository
    from great_agent.research.service import ResearchService
    from great_agent.research.worker import ResearchWorker
    settings = AppSettings.load(valid_config, {'GREAT_AGENT_MODEL_API_KEY': 'fixture-only'})
    source = settings.project_root / 'algorithm.py'
    source.write_text('def reset_window(gap):\n    return gap > 30\n', encoding='utf-8')
    engine, sessions = create_engine_and_session_factory(settings.database_path)
    Base.metadata.create_all(engine)
    provider = FixtureEngine()
    service = ResearchService(ResearchRepository(sessions), ResearchMaterials(sessions, settings), settings, engine=provider)
    worker = ResearchWorker(service)
    yield service, worker, provider, source
    engine.dispose()


def create(service):
    return service.create_session(goal='解释收敛差异', materials=[{'kind': 'code', 'source_id': 'project', 'paths': ['algorithm.py']}])


async def test_two_turns_preserve_hypothesis_and_bind_independent_evidence(research):
    service, worker, provider, source = research
    session = create(service)
    first = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='核查 reset')
    assert first['status'] == 'queued' and not provider.contexts
    assert await worker.run_once()
    session = service.get_session(session['id'])
    assert len(session['messages']) == 2 and len(session['items']) == 1
    hypothesis = session['items'][0]
    source.write_text('changed = True\n', encoding='utf-8')
    second = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='这个假设有反例吗？',
        followup_target={'run_id': first['id'], 'item_id': hypothesis['id']})
    assert await worker.run_once()
    current = service.get_session(session['id'])
    assert len(current['messages']) == 4 and len(current['items']) == 1
    assert current['items'][0]['id'] == hypothesis['id']
    assert current['items'][0]['status'] == 'challenged'
    assert provider.contexts[-1]['followup_target']['item_id'] == hypothesis['id']
    assert 'return gap > 30' in service.evidence(first['id'], 'C1')['text']
    assert 'return gap > 30' in service.evidence(second['id'], 'C1')['text']
    assert source.read_text() == 'changed = True\n'


async def test_cancelled_provider_cannot_publish_message_or_memory(research):
    service, worker, provider, _ = research
    provider.release = asyncio.Event()
    session = create(service)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='等待')
    task = asyncio.create_task(worker.run_once())
    await provider.entered.wait()
    service.cancel(run['id'])
    provider.release.set()
    await task
    current = service.get_session(session['id'])
    assert service.repository.get_run(run['id'])['status'] == 'cancelled'
    assert current['state_revision'] == 0 and not current['items']
    assert len(current['messages']) == 1


async def test_accepted_request_recovers_after_settings_change_without_duplicate_generation(research):
    service, worker, provider, _ = research
    session = create(service)
    body = {'request_id': str(uuid4()), 'expected_version': session['version'], 'message': '分析'}
    accepted = service.submit(session['id'], **body)
    current = service.get_session(session['id'])
    service.update_settings(session['id'], expected_version=current['version'], model_profile_id='default', mode='quick')
    replay = service.submit(session['id'], **body)
    assert replay['id'] == accepted['id']
    await worker.run_once()
    assert len(provider.contexts) == 1 and service.repository.get_run(accepted['id'])['mode'] == 'deep'


async def test_report_is_immutable_after_followup_and_retains_evidence(research):
    service, worker, _, _ = research
    session = create(service)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='第一轮')
    await worker.run_once()
    report = service.create_report(session['id'], run_ids=[run['id']])
    saved = service.repository.list_reports(session['id'])[0]
    assert saved == report and saved['snapshot']['evidence']
    assert '静态源码观察' in saved['content'] and '人工验证' in saved['content']
    current = service.get_session(session['id'])
    service.submit(session['id'], request_id=str(uuid4()), expected_version=current['version'], message='继续')
    await worker.run_once()
    assert service.repository.list_reports(session['id'])[0] == saved


async def test_session_report_cannot_bypass_revoked_evidence_access(research, monkeypatch):
    service, worker, _, _ = research
    session = create(service)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='分析')
    await worker.run_once()
    service.create_report(session['id'], run_ids=[run['id']])

    def revoked(snapshot):
        if snapshot['materials']:
            raise ValueError('source access revoked')

    monkeypatch.setattr(service.materials, 'validate', revoked)
    report = service.get_session(session['id'])['reports'][0]
    assert report['snapshot']['evidence'][0]['available'] is False
    assert not report['snapshot']['evidence'][0]['text']
    assert not report['snapshot']['items']
    assert '静态源码观察' not in report['content']
    current = service.get_session(session['id'])
    patched = service.update_settings(session['id'], expected_version=current['version'], model_profile_id='default', mode='quick')
    assert patched['reports'][0]['snapshot']['evidence'][0]['available'] is False
    current = service.get_session(session['id'])
    patched = service.update_materials(session['id'], expected_version=current['version'], materials=[])
    assert patched['reports'][0]['snapshot']['evidence'][0]['available'] is False


async def test_history_revoked_between_physical_requests_stops_the_next_send(research):
    from test_research_engine import completion, tool_call
    service, worker, _, source = research
    session = create(service)
    first = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='first')
    await worker.run_once()
    current = service.get_session(session['id'])
    other = source.parent / 'other'
    other.mkdir()
    (other / 'allowed.py').write_text('allowed = True\n', encoding='utf-8')
    service.settings.research.code_roots['other'] = other
    current = service.update_materials(session['id'], expected_version=current['version'], materials=[
        {'kind': 'code', 'source_id': 'other', 'paths': ['allowed.py']}])
    material_id = current['material_summary'][0]['id']
    requests = []

    def response(request):
        requests.append(request)
        if len(requests) == 1:
            assert 'algorithm.py' in str(json.loads(request.content)['messages'])
            service.settings.research.code_roots = {'other': other}
            return httpx.Response(200, json=completion(calls=[tool_call('read_code_excerpt', {'material_id': material_id})]))
        return httpx.Response(200, json=completion(json.dumps({'answer': '未重查历史材料。', 'source_ids': []})))

    service.engine = ResearchEngine(service.model_settings, transport=httpx.MockTransport(response))
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=current['version'], message='继续')
    await worker.run_once()
    assert len(requests) == 1
    assert service.repository.get_run(run['id'])['status'] == 'failed'
    assert service.get_session(session['id'])['runs'][0]['result'] is None


async def test_model_intent_commit_ack_loss_does_not_create_duplicate_calls(research, monkeypatch):
    service, worker, _, _ = research
    original = service.repository.start_model_call
    writes = 0

    def lose_ack(*args, **kwargs):
        nonlocal writes
        saved = original(*args, **kwargs)
        writes += 1
        if writes == 1:
            raise DBAPIError(None, None, RuntimeError('local commit acknowledgement lost'))
        return saved

    monkeypatch.setattr(service.repository, 'start_model_call', lose_ack)
    session = create(service)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='分析')
    await worker.run_once()
    calls = service.repository.list_model_calls(run['id'])
    assert len(calls) == 1 and calls[0]['status'] == 'completed'


async def test_old_state_evidence_is_available_beyond_recent_message_window(research):
    service, worker, _, _ = research
    session = create(service)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='原始判断')
    await worker.run_once()
    current = service.get_session(session['id'])
    prior = service._prior_evidence({'messages': [], 'items': current['items']})
    assert prior and prior[0]['id'] == current['items'][0]['support'][0]


async def test_recovery_replays_tools_and_models_after_original_source_changes(research, monkeypatch):
    from great_agent.db.models import utc_now
    from test_research_engine import completion, tool_call
    service, worker, _, source = research
    session = create(service)
    material_id = session['material_summary'][0]['id']
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json=completion(calls=[tool_call('read_code_excerpt', {'material_id': material_id})])
            if len(requests) == 1 else completion(json.dumps({'answer': '窗口实现[C1]', 'source_ids': ['C1']})))

    service.engine = ResearchEngine(service.model_settings, transport=httpx.MockTransport(response))
    original_checkpoint = service.repository.save_checkpoint

    class Crash(BaseException):
        pass

    def crash_before_final(*args, **kwargs):
        if kwargs['key'] == 'validated-result':
            raise Crash()
        return original_checkpoint(*args, **kwargs)

    monkeypatch.setattr(service.repository, 'save_checkpoint', crash_before_final)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='检查窗口')
    with pytest.raises(Crash):
        await worker.run_once()
    assert len(requests) == 2
    source.write_text('new_source = True\n', encoding='utf-8')
    monkeypatch.setattr(service.repository, 'save_checkpoint', original_checkpoint)
    service.repository.recover_expired(now=utc_now() + timedelta(seconds=120))
    await worker.run_once()
    assert service.repository.get_run(run['id'])['status'] == 'completed'
    assert len(requests) == 2


async def test_user_correction_becomes_source_typed_memory_for_later_questions(research):
    service, worker, provider, _ = research
    original = provider.reply
    quote = '只比较同站同日，不改变统计口径'

    async def with_correction(question, **kwargs):
        result = await original(question, **kwargs)
        if quote in question:
            result['user_statements'] = [{'text': '保持统计口径一致', 'quote': quote}]
        return result

    provider.reply = with_correction
    session = create(service)
    service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message=quote)
    await worker.run_once()
    current = service.get_session(session['id'])
    statement = next(item for item in current['items'] if item['kind'] == 'user_statement')
    assert statement['status'] == 'user_provided' and statement['quote'] == quote
    assert not statement['support']
    service.submit(session['id'], request_id=str(uuid4()), expected_version=current['version'], message='继续分析')
    await worker.run_once()
    assert any(item['id'] == statement['id'] for item in provider.contexts[-1]['items'])


async def test_revoked_history_and_derived_items_never_reenter_new_model_context(research):
    service, worker, provider, source = research
    original = provider.reply
    secret = 'confidential_algorithm_detail'

    async def source_quoting_reply(question, **kwargs):
        result = await original(question, **kwargs)
        if question == 'first':
            result['answer'] = secret + '[C1]'
            result['answer_blocks'][0]['text'] = result['answer']
            result['hypotheses'][0]['text'] = secret
        return result

    provider.reply = source_quoting_reply
    session = create(service)
    run = service.submit(session['id'], request_id=str(uuid4()), expected_version=session['version'], message='first')
    await worker.run_once()
    current = service.get_session(session['id'])
    target = current['items'][0]['id']
    other = source.parent / 'other'
    other.mkdir()
    (other / 'allowed.py').write_text('allowed = True\n', encoding='utf-8')
    service.settings.research.code_roots = {'other': other}
    current = service.update_materials(session['id'], expected_version=current['version'], materials=[
        {'kind': 'code', 'source_id': 'other', 'paths': ['allowed.py']}])
    service.submit(session['id'], request_id=str(uuid4()), expected_version=current['version'], message='继续',
                   followup_target={'run_id': run['id'], 'item_id': target})
    await worker.run_once()
    assert secret not in json.dumps(provider.contexts[-1], ensure_ascii=False)
    assert provider.contexts[-1]['limitations']
