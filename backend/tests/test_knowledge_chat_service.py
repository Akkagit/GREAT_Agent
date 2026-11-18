import asyncio
import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_knowledge_chat_repository import env  # noqa: F401
from great_agent.config import ModelSettings
from great_agent.db.knowledge_chat_models import KnowledgeRetrievalTraceModel
from great_agent.adapters.model_provider import ModelProposalError


class Retriever:
    def __init__(self, repo): self.repo, self.closed = repo, False
    async def search(self, snapshot, query, limit=8):
        result = self.repo.search_snapshot(snapshot, query, limit)
        return {**result, 'queries':[query], 'candidates':[], 'timings':{}}
    async def close(self): self.closed = True


class Engine:
    calls = 0
    async def reply(self, question, history, search_knowledge, read_evidence, on_request):
        self.calls += 1
        await on_request([{'role':'user','content':question}], {'physical_model_requests':1})
        evidence = await search_knowledge('ambiguity')
        assert evidence['items'][0]['source_id']=='S1'
        await read_evidence(['S1'])
        await on_request([{'role':'tool','content':json.dumps(evidence)}], {'physical_model_requests':2})
        return {'text':'Clocks support ambiguity resolution [S1].','source_ids':['S1'],
                'answerable':True,'metrics':{},'outbound_messages':[]}


def service(env, engine=None, **kwargs):
    from great_agent.services.knowledge_chat_service import KnowledgeChatService
    repo=env[0]
    return KnowledgeChatService(repo,Retriever(repo),engine or Engine(),ModelSettings(
        name='test-only',base_url='https://example.invalid',api_key='test'),**kwargs)


@pytest.mark.asyncio
async def test_service_persists_actual_outbound_and_replays_without_model(env):
    s=service(env); cid=env[5]['id']; request=str(uuid4())
    response=await s.send_message(cid,'ambiguity?',request)
    assert response['timings']['retrieval_mode']=='lexical'
    citation=response['assistant_message']['blocks'][1]
    assert citation['available'] and citation['evidence_text']
    with env[-1]() as session:
        trace=session.get(KnowledgeRetrievalTraceModel,response['run_id'])
        assert len(json.loads(trace.outbound_messages_json))==2
    assert await s.send_message(cid,'ambiguity?',request)==response
    assert s.engine.calls==1
    await s.close(); assert s.retriever.closed


@pytest.mark.asyncio
async def test_scope_disabled_before_second_request_never_completes(env):
    class Revoker(Engine):
        async def reply(self, question, history, search_knowledge, read_evidence, on_request):
            await search_knowledge('ambiguity')
            env[1].set_enabled(env[3]['id'],False)
            await on_request([], {})
            raise AssertionError('must not reach provider')
    s=service(env,Revoker())
    with pytest.raises(ModelProposalError): await s.send_message(env[5]['id'],'question',str(uuid4()))
    detail=env[0].get_conversation(env[5]['id'])
    assert detail['active_run_id'] is None and detail['messages'][-1]['status']=='interrupted'


@pytest.mark.asyncio
async def test_cancel_releases_owner_and_blocks_late_completion(env):
    entered=asyncio.Event()
    class Slow(Engine):
        async def reply(self,*args,**kwargs):
            entered.set(); await asyncio.sleep(100)
    s=service(env,Slow()); cid=env[5]['id']; request=str(uuid4())
    task=asyncio.create_task(s.send_message(cid,'question',request))
    await asyncio.wait_for(entered.wait(),3)
    assert (await s.cancel(cid,request))['status']=='interrupted'
    with pytest.raises(asyncio.CancelledError): await task
    assert env[0].get_conversation(cid)['active_run_id'] is None


@pytest.mark.asyncio
async def test_model_timeout_is_bounded_and_safe(env):
    class Slow(Engine):
        async def reply(self,*args,**kwargs): await asyncio.sleep(100)
    s=service(env,Slow(),timeout_seconds=.05)
    with pytest.raises(ModelProposalError): await s.send_message(env[5]['id'],'question',str(uuid4()))
    assert env[0].get_conversation(env[5]['id'])['active_run_id'] is None


@pytest.mark.asyncio
async def test_evidence_unknown_source_is_not_readable(env):
    class Bad(Engine):
        async def reply(self,question,history,search_knowledge,read_evidence,on_request):
            await read_evidence(['S999'])
    with pytest.raises(ModelProposalError):
        await service(env,Bad()).send_message(env[5]['id'],'question',str(uuid4()))


def test_old_conversation_service_rejects_knowledge_scope(env):
    from great_agent.services.conversation_service import ConversationService
    from great_agent.errors import ExperimentNotFoundError
    s=ConversationService(env[-1],plan_service=None,engine=None)
    assert not any(x['id']==env[5]['id'] for x in s.list_conversations())
    with pytest.raises(ExperimentNotFoundError): s.get_conversation(env[5]['id'])


@pytest.mark.asyncio
async def test_close_cannot_start_pending_claim_after_shutdown(env,monkeypatch):
    s=service(env); original=env[0].claim_turn
    entered,release=threading.Event(),threading.Event()
    def held(*args,**kwargs):
        entered.set(); assert release.wait(4)
        return original(*args,**kwargs)
    monkeypatch.setattr(env[0],'claim_turn',held)
    task=asyncio.create_task(s.send_message(env[5]['id'],'question',str(uuid4())))
    assert await asyncio.to_thread(entered.wait,3)
    closing=asyncio.create_task(s.close())
    await asyncio.sleep(.02)
    release.set()
    await closing
    await asyncio.gather(task,return_exceptions=True)
    assert s.engine.calls==0
    assert env[0].get_conversation(env[5]['id'])['active_run_id'] is None
