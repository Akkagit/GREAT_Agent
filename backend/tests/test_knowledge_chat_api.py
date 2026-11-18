from uuid import uuid4
import hashlib

import httpx
import pytest
from fastapi import FastAPI

from test_knowledge_chat_repository import env  # noqa: F401
from test_knowledge_chat_service import service


@pytest.fixture
def api(env):
    from great_agent.api.knowledge_chat import router, chat_service
    app=FastAPI(); app.include_router(router)
    s=service(env)
    app.dependency_overrides[chat_service]=lambda:s
    return app,s


@pytest.mark.asyncio
async def test_api_create_bind_send_and_profile_allowlist(env,api):
    app,s=api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        profiles=(await c.get('/api/v1/knowledge/model-profiles')).json()['items']
        assert profiles[0]['id']=='default' and profiles[0]['configured']
        payload={'base_id':env[2]['id'],'scope_mode':'selected','document_ids':[env[3]['id']],
                 'model_profile_id':'hacker','api_key':'browser-secret'}
        assert (await c.post('/api/v1/knowledge/conversations',json=payload)).status_code==422
        payload.pop('api_key')
        assert (await c.post('/api/v1/knowledge/conversations',json=payload)).status_code==422
        payload['model_profile_id']='default'
        detail=(await c.post('/api/v1/knowledge/conversations',json=payload)).json()
        cid=detail['id']; body={'message':'ambiguity?','request_id':str(uuid4())}
        response=await c.post(f'/api/v1/knowledge/conversations/{cid}/messages',json=body)
        assert response.status_code==200, response.text
        assert response.json()['assistant_message']['blocks'][1]['source_id']=='S1'
        patch={'expected_revision':1,'scope_mode':'selected','document_ids':[env[4]['id']],
               'model_profile_id':'default'}
        assert (await c.patch(f'/api/v1/knowledge/conversations/{cid}/binding',json=patch)).status_code==200
        assert (await c.patch(f'/api/v1/knowledge/conversations/{cid}/binding',json=patch)).status_code==409
        replay=(await c.post(f'/api/v1/knowledge/conversations/{cid}/messages',json=body)).json()
        assert {k:v for k,v in replay.items() if k!='retrieval_summary'}=={
            k:v for k,v in response.json().items() if k!='retrieval_summary'}
        assert replay['retrieval_summary']['available'] is False
        assert replay['retrieval_summary']['queries']==[] and replay['retrieval_summary']['documents']==[]
        assert replay['retrieval_summary']['hit_count']==0 and replay['retrieval_summary']['evidence_count']==0
        assert s.engine.calls==1


@pytest.mark.asyncio
async def test_api_page_identity_cannot_cross_document(env,api):
    app,_=api
    from great_agent.api.dependencies import get_services
    from types import SimpleNamespace
    app.dependency_overrides[get_services]=lambda:SimpleNamespace(knowledge_service=SimpleNamespace(repository=env[1]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        path=f"/api/v1/knowledge/documents/{env[3]['id']}/versions/{env[4]['active_version_id']}/pages/0"
        assert (await c.get(path)).status_code==404


@pytest.mark.asyncio
async def test_api_original_corruption_and_disabled_source_are_unavailable(env,api,tmp_path):
    from great_agent.api.dependencies import get_services
    from great_agent.db.knowledge_models import KnowledgeBlobModel,KnowledgeDocumentModel
    from types import SimpleNamespace
    app,s=api
    original=tmp_path/'paper.pdf'; original.write_bytes(b'%PDF-test-original')
    with env[-1].begin() as session:
        doc=session.get(KnowledgeDocumentModel,env[3]['id'])
        session.get(KnowledgeBlobModel,doc.blob_id).sha256=hashlib.sha256(original.read_bytes()).hexdigest()
    app.dependency_overrides[get_services]=lambda:SimpleNamespace(knowledge_service=SimpleNamespace(
        repository=env[1],original=lambda did:(original,env[1].get_document(did))))
    turn=await s.send_message(env[5]['id'],'ambiguity?',str(uuid4()))
    path=f"/api/v1/knowledge/conversations/{env[5]['id']}/messages/{turn['assistant_message']['id']}/citations/S1"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        source=(await c.get(path)).json()
        assert source['available']
        page=await c.get(f"/api/v1/knowledge/documents/{source['document_id']}/versions/{source['version_id']}/pages/0")
        assert page.status_code==200 and page.json()['raw_text']
        original.write_bytes(b'%PDF-corrupt')
        unavailable=(await c.get(path)).json()
        assert not unavailable['available'] and unavailable['unavailable_reason']=='original_missing_or_corrupt'
        env[1].set_enabled(env[3]['id'],False)
        assert not (await c.get(path)).json()['available']
        assert (await c.get(f"/api/v1/knowledge/documents/{source['document_id']}/versions/{source['version_id']}/pages/0")).status_code==404
