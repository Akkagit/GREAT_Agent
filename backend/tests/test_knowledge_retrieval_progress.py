import json
from uuid import uuid4

import pytest

from test_knowledge_chat_repository import env  # noqa: F401
from test_knowledge_chat_service import service,Engine


@pytest.mark.asyncio
async def test_retrieval_events_and_restored_summary_use_real_library_and_scope(env):
    repo,_,base,_,_,conversation,_=env
    answer=await service(env).send_message(conversation['id'],'ambiguity?',str(uuid4()))
    events=repo.events(conversation['id'])
    retrieval=[e['data']['retrieval'] for e in events if e['kind']=='retrieving']
    assert retrieval and retrieval[0]['base_id']==base['id']
    assert retrieval[0]['base_name']=='GNSS'
    assert retrieval[0]['document_count']==1
    assert retrieval[0]['evidence_count']==0
    assert all(e['data']['request_id']==answer['request_id'] for e in events)
    summary=repo.get_conversation(conversation['id'])['retrieval_summary']
    assert summary['base_name']=='GNSS' and summary['evidence_count']==1
    assert summary['queries']==['ambiguity']
    assert summary['documents'][0]['document_id']==env[3]['id']
    assert answer['retrieval_summary']==summary
    assert not any(key in json.dumps(summary) for key in ('outbound_messages','system_prompt','api_key'))


@pytest.mark.asyncio
async def test_scope_revocation_hides_old_summary_content(env):
    repo,knowledge,_,first,_,conversation,_=env
    await service(env).send_message(conversation['id'],'ambiguity?',str(uuid4()))
    knowledge.set_enabled(first['id'],False)
    summary=repo.get_conversation(conversation['id'])['retrieval_summary']
    assert summary['available'] is False
    assert summary.get('documents',[])==[] and summary.get('queries',[])==[]
    replay=repo.events(conversation['id'])
    for event in replay:
        if 'retrieval' in event['data']:
            assert event['data']['retrieval']['available'] is False
            assert event['data']['retrieval'].get('documents',[])==[]
            assert event['data']['retrieval'].get('queries',[])==[]
