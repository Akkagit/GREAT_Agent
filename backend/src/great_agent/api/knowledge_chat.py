"""Server-scoped document conversations, immutable sources and lifecycle events."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from great_agent.api.dependencies import get_services
from great_agent.api.knowledge import service as ingestion_service
from great_agent.db.knowledge_chat_repository import (
    KnowledgeChatNotFound,KnowledgeChatConflict,KnowledgeChatValidationError,
)
from great_agent.db.knowledge_repository import KnowledgeNotFound
from great_agent.db.knowledge_models import KnowledgeDocumentModel,KnowledgeVersionModel,KnowledgePageModel

router=APIRouter(prefix='/api/v1/knowledge',tags=['knowledge-chat'])


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class BindingInput(Input):
    scope_mode: Literal['selected','all_enabled']='selected'
    document_ids: list[UUID]=Field(default_factory=list,max_length=200)
    model_profile_id: str=Field(default='default',min_length=1,max_length=100)


class CreateInput(BindingInput):
    base_id: UUID


class UpdateInput(BindingInput):
    expected_revision: int=Field(ge=1,strict=True)


class TurnInput(Input):
    message: str=Field(min_length=1,max_length=2000)
    request_id: UUID


class CancelInput(Input):
    request_id: UUID


def chat_service(services=Depends(get_services)):
    result=getattr(services,'knowledge_chat_service',None)
    if result is None: raise HTTPException(503,detail='文献对话未启用。')
    return result


def public_error(error):
    if isinstance(error,(KnowledgeChatNotFound,KnowledgeNotFound)): return HTTPException(404,detail='文献或会话不可用。')
    if isinstance(error,KnowledgeChatConflict): return HTTPException(409,detail='会话状态已变化，请刷新后重试。')
    if isinstance(error,(KnowledgeChatValidationError,ValueError)): return HTTPException(422,detail='文献范围或请求参数无效。')
    return None


async def call(fn,*args,**kwargs):
    try: return await asyncio.to_thread(fn,*args,**kwargs)
    except Exception as error:
        mapped=public_error(error)
        if mapped: raise mapped from None
        raise


@router.get('/model-profiles')
async def profiles(s=Depends(chat_service)):
    return {'items':s.model_profiles()}


@router.post('/model-profiles/{profile_id}/test')
async def test_model(profile_id:str,body:Input,s=Depends(chat_service)):
    try: return await s.test_model(profile_id)
    except (KnowledgeChatValidationError,KnowledgeChatConflict) as error: raise public_error(error) from None


@router.get('/conversations')
async def conversations(base_id:UUID|None=None,s=Depends(chat_service)):
    return {'items':await call(s.repository.list_conversations,str(base_id) if base_id else None)}


@router.post('/conversations',status_code=201)
async def create(body:CreateInput,s=Depends(chat_service)):
    await call(s.validate_profile,body.model_profile_id)
    return await call(s.repository.create_conversation,base_id=str(body.base_id),scope_mode=body.scope_mode,
        document_ids=[str(i) for i in body.document_ids],model_profile_id=body.model_profile_id)


@router.get('/conversations/{conversation_id}')
async def detail(conversation_id:UUID,s=Depends(chat_service)):
    return await call(s.repository.get_conversation,str(conversation_id))


@router.patch('/conversations/{conversation_id}/binding')
async def binding(conversation_id:UUID,body:UpdateInput,s=Depends(chat_service)):
    await call(s.validate_profile,body.model_profile_id)
    return await call(s.repository.update_binding,str(conversation_id),expected_revision=body.expected_revision,
        scope_mode=body.scope_mode,document_ids=[str(i) for i in body.document_ids],model_profile_id=body.model_profile_id)


@router.post('/conversations/{conversation_id}/messages')
async def send(conversation_id:UUID,body:TurnInput,request:Request,s=Depends(chat_service)):
    async def disconnected():
        while True:
            event=await request.receive()
            if event['type']=='http.disconnect': return
    generation=asyncio.create_task(s.send_message(str(conversation_id),body.message,str(body.request_id)))
    monitor=asyncio.create_task(disconnected())
    try:
        await asyncio.wait((generation,monitor),return_when=asyncio.FIRST_COMPLETED)
        if generation.done(): return await generation
        raise HTTPException(499,detail='文献对话请求已取消。')
    except Exception as error:
        mapped=public_error(error)
        if mapped: raise mapped from None
        raise
    finally:
        for task in (generation,monitor):
            if not task.done(): task.cancel()
        await asyncio.gather(generation,monitor,return_exceptions=True)


@router.post('/conversations/{conversation_id}/cancel')
async def cancel(conversation_id:UUID,body:CancelInput,s=Depends(chat_service)):
    try: return await s.cancel(str(conversation_id),str(body.request_id))
    except Exception as error:
        mapped=public_error(error)
        if mapped: raise mapped from None
        raise


@router.get('/conversations/{conversation_id}/events')
async def events(conversation_id:UUID,request:Request,last_event_id:str|None=Header(default=None),s=Depends(chat_service)):
    await call(s.repository.get_conversation,str(conversation_id))
    try: after=int(last_event_id or '0')
    except ValueError: raise HTTPException(400,detail='事件游标无效。') from None
    if after<0: raise HTTPException(400,detail='事件游标无效。')
    async def stream():
        nonlocal after
        while not await request.is_disconnected():
            rows=await call(s.repository.events,str(conversation_id),after)
            for row in rows:
                after=row['id']
                yield f"id: {after}\nevent: {row['kind']}\ndata: {json.dumps(row['data'],ensure_ascii=False)}\n\n"
            if not rows: yield ': keepalive\n\n'
            await asyncio.sleep(.5)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@router.get('/conversations/{conversation_id}/messages/{message_id}/citations/{source_id}')
async def citation(conversation_id:UUID,message_id:UUID,source_id:str,s=Depends(chat_service),ingestion=Depends(ingestion_service)):
    result=await call(s.repository.get_citation,str(conversation_id),str(message_id),source_id)
    if result['available']:
        def verify():
            try:
                path,document=ingestion.original(result['document_id'])
                if document['blob']['sha256']!=result['source_sha256']: return False
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    while block:=stream.read(1024*1024): digest.update(block)
                return digest.hexdigest()==result['source_sha256']
            except (OSError,ValueError,KnowledgeNotFound): return False
        if not await asyncio.to_thread(verify):
            result.update(available=False,unavailable_reason='original_missing_or_corrupt')
    return result


@router.get('/documents/{document_id}/versions/{version_id}/pages/{page_index}')
async def page(document_id:UUID,version_id:UUID,page_index:int=Path(ge=0),ingestion=Depends(ingestion_service)):
    def read():
        with ingestion.repository.sessions() as session:
            document=session.get(KnowledgeDocumentModel,str(document_id))
            version=session.get(KnowledgeVersionModel,str(version_id))
            if (not document or not document.enabled or not version or version.document_id!=document.id
                    or version.status not in ('ready','completed_with_warnings')):
                raise KnowledgeChatNotFound()
            row=session.scalar(select(KnowledgePageModel).where(KnowledgePageModel.version_id==version.id,
                KnowledgePageModel.page_index==page_index))
            if row is None: raise KnowledgeChatNotFound()
            result={k:getattr(row,k) for k in ('page_index','page_label','width','height','role','raw_text')}
            geometries=json.loads(version.metadata_json).get('page_geometry',{})
            geometry=geometries.get(str(page_index),{}) if isinstance(geometries,dict) else {}
            transform=geometry.get('to_pdf_transform') if isinstance(geometry,dict) else None
            if (isinstance(transform,list) and len(transform)==6
                    and all(type(v) in (int,float) and math.isfinite(v) for v in transform)
                    and geometry.get('coordinate_space')=='rotated_media_top_left'):
                result.update(coordinate_space='rotated_media_top_left',to_pdf_transform=transform,
                              rotation=geometry.get('rotation'))
            else:
                result['coordinate_space']='unverified_top_left'
            return result
    return await call(read)
