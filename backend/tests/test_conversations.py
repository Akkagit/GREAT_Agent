import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from great_agent.db.engine import create_engine_and_session_factory
from great_agent.db.models import Base
from great_agent.services.plan_service import PlanService
from great_agent.services.conversation_service import ConversationService, ConversationConflict
from test_api import valid_spec, api_context

class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.action = None
        self.context = None
    async def reply(self, message, **kwargs):
        self.calls += 1
        self.context = kwargs['context']
        proposal = await self.action(kwargs) if self.action else None
        return SimpleNamespace(text='解释', proposal=proposal, state={'memory': 'ok'}, metrics={})

@pytest.fixture
def context(tmp_path):
    engine, sessions = create_engine_and_session_factory(tmp_path / 'conversation.db')
    Base.metadata.create_all(engine)
    plans = PlanService(sessions)
    fake = FakeEngine()
    service = ConversationService(sessions, plan_service=plans, engine=fake)
    yield service, plans, fake, sessions
    engine.dispose()

@pytest.fixture
def frozen_conversation_time(monkeypatch):
    from great_agent.db import conversation_repository

    # Exercise the asyncio request deadline without expiring the ownership
    # lease during synchronous database setup on a busy machine.
    now = 1_700_000_000.0
    monkeypatch.setattr(conversation_repository, 'time', SimpleNamespace(time=lambda: now))
    return now

@pytest.mark.asyncio
async def test_durable_default_text_and_replay(context):
    service, plans, fake, sessions = context
    conversation = service.ensure_conversation()
    assert service.ensure_conversation()['id'] == conversation['id']
    request = uuid4()
    turn = await service.send_message(conversation['id'], '理论问题', request)
    assert turn['assistant_message']['blocks'] == [{'type':'text', 'text':'解释'}]
    assert await service.send_message(conversation['id'], '理论问题', request) == turn
    assert fake.calls == 1
    with pytest.raises(ConversationConflict):
        await service.send_message(conversation['id'], '不同内容', request)
    restored = ConversationService(sessions, plan_service=plans, engine=fake)
    assert len(restored.get_conversation(conversation['id'])['messages']) == 2
    assert [e['kind'] for e in restored.repository.events(conversation['id'])] == ['started','completed']

@pytest.mark.asyncio
async def test_no_change_preserves_pending_and_frozen_chat(context, valid_spec):
    service, plans, fake, sessions = context
    experiment = plans.create_experiment(valid_spec)
    pending = plans.preview_revision(experiment.id, valid_spec, source='card')
    conversation = service.ensure_conversation(experiment.id)
    async def no_change(kwargs):
        result = await kwargs['propose'](valid_spec.model_dump(mode='json'), '无需修改')
        assert result == {'proposal': None, 'no_change': True}
    fake.action = no_change
    await service.send_message(conversation['id'], '检查', uuid4())
    assert plans.get_proposal(experiment.id, pending.id).status == 'pending'
    plans.reject_proposal(experiment.id, pending.id)
    plans.freeze_revision(experiment.id, expected_revision=1)
    fake.action = None
    await service.send_message(conversation['id'], '解释', uuid4())
    assert fake.context['read_only'] is True

@pytest.mark.asyncio
async def test_concurrent_cancel_and_safe_failure(context):
    service, plans, fake, sessions = context
    conversation = service.ensure_conversation()
    started = asyncio.Event()
    async def block(kwargs):
        started.set()
        await asyncio.Event().wait()
    fake.action = block
    task = asyncio.create_task(service.send_message(conversation['id'], '等待', uuid4()))
    await started.wait()
    with pytest.raises(ConversationConflict):
        await service.send_message(conversation['id'], '冲突', uuid4())
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert service.get_conversation(conversation['id'])['messages'][-1]['status'] == 'interrupted'
    async def fail(kwargs): raise RuntimeError('SECRET')
    fake.action = fail
    from great_agent.adapters.model_provider import ModelProposalError
    with pytest.raises(ModelProposalError) as error:
        await service.send_message(conversation['id'], '失败', uuid4())
    assert 'SECRET' not in str(error.value)
    assert error.value.request_id
@pytest.mark.asyncio
async def test_mixed_proposal_is_durable_authoritative_and_single(context, valid_spec):
    service, plans, fake, sessions = context
    experiment=plans.create_experiment(valid_spec)
    conversation=service.ensure_conversation(experiment.id)
    candidate=valid_spec.model_dump(mode='json')
    candidate['stations']=['ABCD']
    async def change(kwargs):
        result=await kwargs['propose'](candidate,'调整测站')
        return result['proposal']
    fake.action=change
    request=uuid4()
    response=await service.send_message(conversation['id'],'调整测站',request)
    proposal=response['assistant_message']['blocks'][1]['proposal']
    assert proposal['candidate']['station_group_id']=='custom'
    assert len(plans.list_proposals(experiment.id))==1
    assert plans.get_current_revision(experiment.id).number==1
    plans.reject_proposal(experiment.id,UUID(proposal['id']))
    assert service.ensure_conversation(experiment.id)['messages'][-1]['blocks'][1]['proposal']['status']=='rejected'
    assert await service.send_message(conversation['id'],'调整测站',request)==response
    assert fake.calls==1

@pytest.mark.asyncio
async def test_timeout_restart_and_late_callback_have_no_side_effects(context, valid_spec, frozen_conversation_time):
    from great_agent.db.conversation_models import ConversationRunModel
    service, plans, fake, sessions=context
    experiment=plans.create_experiment(valid_spec)
    conversation=service.ensure_conversation(experiment.id)
    request=uuid4()
    run,_=service.repository.claim(conversation['id'],'旧请求',request,1)
    with sessions.begin() as session:
        session.get(ConversationRunModel,run.id).lease_until=frozen_conversation_time-1
    restored=ConversationService(sessions,plan_service=plans,engine=fake)
    assert restored.get_conversation(conversation['id'])['messages'][-1]['status']=='interrupted'
    with pytest.raises(ConversationConflict):
        await restored.send_message(conversation['id'],'旧请求',request)
    assert fake.calls==0
    async def slow(kwargs): await asyncio.Event().wait()
    fake.action=slow
    restored.timeout_seconds=.01
    from great_agent.adapters.model_provider import ModelProposalError
    with pytest.raises(ModelProposalError) as error:
        await restored.send_message(conversation['id'],'超时',uuid4())
    assert error.value.category=='timeout'
    assert restored.get_conversation(conversation['id'])['messages'][-1]['status']=='failed'

@pytest.mark.asyncio
async def test_interrupted_saved_proposal_recovers_pending(context,valid_spec):
    service,plans,fake,sessions=context
    experiment=plans.create_experiment(valid_spec)
    conversation=service.ensure_conversation(experiment.id)
    saved=asyncio.Event()
    async def save_then_wait(kwargs):
        candidate=valid_spec.model_dump(mode='json');candidate['stations']=['ABCD']
        await kwargs['propose'](candidate,'调整')
        saved.set()
        await asyncio.Event().wait()
    fake.action=save_then_wait
    task=asyncio.create_task(service.send_message(conversation['id'],'调整',uuid4()))
    await saved.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    message=service.get_conversation(conversation['id'])['messages'][-1]
    assert message['status']=='interrupted'
    assert message['blocks'][1]['proposal']['status']=='pending'
    assert len(plans.list_proposals(experiment.id))==1


def test_conversation_migration_additive_and_bootstrap(tmp_path):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    for bootstrap in (False,True):
        database=tmp_path/f'migrate-{bootstrap}.db'
        config=Config(str(Path(__file__).parents[2]/'alembic.ini'))
        config.set_main_option('sqlalchemy.url',f'sqlite:///{database.as_posix()}')
        config.set_main_option('path_separator','os')
        command.upgrade(config,'0003')
        engine,sessions=create_engine_and_session_factory(database)
        if bootstrap: Base.metadata.create_all(engine)
        command.upgrade(config,'head')
        assert {'conversations','conversation_messages','conversation_runs','conversation_events'} <= set(inspect(engine).get_table_names())
        command.downgrade(config,'0003')
        assert 'plan_proposals' in inspect(engine).get_table_names()
        engine.dispose()


def test_conversation_api_contract(api_context):
    from fastapi.testclient import TestClient
    client, services, sessions=api_context
    services.conversation_service=ConversationService(sessions,plan_service=services.plan_service,engine=FakeEngine())
    with client:
        conversation=client.post('/api/v1/conversations',json={'experiment_id':None})
        assert conversation.status_code==200
        cid=conversation.json()['id']
        assert client.get('/api/v1/conversations').json()[0]['id']==cid
        request=str(uuid4())
        body={'message':'问题','client_request_id':request}
        result=client.post(f'/api/v1/conversations/{cid}/messages',json=body)
        assert result.status_code==200
        assert set(result.json())=={'conversation_id','run_id','request_id','status','user_message','assistant_message','timings'}
        assert client.post(f'/api/v1/conversations/{cid}/messages',json=body).json()==result.json()
        body['message']='更改'
        assert client.post(f'/api/v1/conversations/{cid}/messages',json=body).status_code==409
        assert len(client.get(f'/api/v1/conversations/{cid}').json()['messages'])==2

@pytest.mark.asyncio
async def test_http_disconnect_interrupts_and_cleans_up(context):
    from fastapi import HTTPException
    from great_agent.api.conversations import send
    from great_agent.domain.conversation import SendMessageRequest
    service,plans,fake,sessions=context
    conversation=service.ensure_conversation()
    started=asyncio.Event()
    async def block(kwargs):
        started.set()
        await asyncio.Event().wait()
    fake.action=block
    async def receive():
        await started.wait()
        return {'type':'http.disconnect'}
    with pytest.raises(HTTPException) as error:
        await send(UUID(conversation['id']),SendMessageRequest(message='问题',client_request_id=uuid4()),
            SimpleNamespace(receive=receive),service)
    assert error.value.status_code==499
    assert service.get_conversation(conversation['id'])['messages'][-1]['status']=='interrupted'

@pytest.mark.asyncio
async def test_suppressed_cancellation_cannot_write_late_proposal(context,valid_spec,frozen_conversation_time):
    service,plans,fake,sessions=context
    experiment=plans.create_experiment(valid_spec)
    conversation=service.ensure_conversation(experiment.id)
    service.timeout_seconds=.01
    attempted=asyncio.Event()
    async def malicious(kwargs):
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: pass
        candidate=valid_spec.model_dump(mode='json');candidate['stations']=['ABCD']
        try: await kwargs['propose'](candidate,'迟到')
        finally: attempted.set()
    fake.action=malicious
    from great_agent.adapters.model_provider import ModelProposalError
    with pytest.raises(ModelProposalError):
        await service.send_message(conversation['id'],'超时',uuid4())
    assert attempted.is_set()
    assert plans.list_proposals(experiment.id)==[]
    assert service.get_conversation(conversation['id'])['messages'][-1]['status']=='failed'

@pytest.mark.asyncio
async def test_lifecycle_sse_resumes_after_durable_event_id(context):
    from great_agent.api.conversations import events
    service,plans,fake,sessions=context
    conversation=service.ensure_conversation()
    await service.send_message(conversation['id'],'问题',uuid4())
    rows=service.repository.events(conversation['id'])
    async def connected(): return False
    response=await events(UUID(conversation['id']),SimpleNamespace(is_disconnected=connected),str(rows[0]['id']),service)
    iterator=response.body_iterator
    frame=await anext(iterator)
    assert f"id: {rows[1]['id']}" in frame
    assert 'event: completed' in frame
    assert 'request_id' in frame
    await iterator.aclose()
@pytest.mark.asyncio
async def test_bound_consult_preserves_pending_and_task_bearing_is_read_only(context,valid_spec):
    from great_agent.db.models import DailyTaskModel
    service,plans,fake,sessions=context
    experiment=plans.create_experiment(valid_spec)
    pending=plans.preview_revision(experiment.id,valid_spec,source='card')
    conversation=service.ensure_conversation(experiment.id)
    await service.send_message(conversation['id'],'解释当前配置',uuid4())
    assert plans.get_proposal(experiment.id,pending.id).status=='pending'
    assert plans.get_current_revision(experiment.id).number==1
    with sessions.begin() as session:
        session.add(DailyTaskModel(id=str(uuid4()),experiment_id=str(experiment.id),revision_id=str(experiment.current_revision_id),year=2024,doy=350,status='pending'))
    await service.send_message(conversation['id'],'解释任务',uuid4())
    assert fake.context['read_only'] is True
    assert len(fake.context['tasks'])==1
    assert plans.get_proposal(experiment.id,pending.id).status=='pending'

@pytest.mark.asyncio
async def test_current_revision_rechecked_before_proposal_write(context,valid_spec):
    from great_agent.adapters.model_provider import ModelProposalError
    service,plans,fake,sessions=context
    experiment=plans.create_experiment(valid_spec)
    conversation=service.ensure_conversation(experiment.id)
    async def race(kwargs):
        plans.accept_revision(experiment.id,valid_spec,expected_revision=1)
        candidate=valid_spec.model_dump(mode='json');candidate['stations']=['ABCD']
        await kwargs['propose'](candidate,'陈旧提案')
    fake.action=race
    with pytest.raises(ModelProposalError): await service.send_message(conversation['id'],'修改',uuid4())
    assert plans.list_proposals(experiment.id)==[]
    assert plans.get_current_revision(experiment.id).number==2
