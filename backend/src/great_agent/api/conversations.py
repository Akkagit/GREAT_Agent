from __future__ import annotations
import asyncio
import json
from uuid import UUID
from fastapi import APIRouter, Depends, Request, HTTPException, Header
from fastapi.responses import StreamingResponse
from great_agent.api.dependencies import get_services
from great_agent.domain.conversation import ConversationDetail, ConversationSummary, TurnResponse, EnsureConversationRequest, SendMessageRequest

router=APIRouter(prefix='/api/v1/conversations',tags=['conversations'])
def service(services=Depends(get_services)):
    value=getattr(services,'conversation_service',None)
    if value is None: raise HTTPException(status_code=503,detail='conversation provider unavailable')
    return value
@router.post('',response_model=ConversationDetail)
def ensure(request: EnsureConversationRequest, conversations=Depends(service)):
    return conversations.ensure_conversation(request.experiment_id)
@router.get('',response_model=list[ConversationSummary])
def list_conversations(conversations=Depends(service)):
    return conversations.list_conversations()
@router.get('/{conversation_id}',response_model=ConversationDetail)
def detail(conversation_id:UUID,conversations=Depends(service)):
    return conversations.get_conversation(conversation_id)
@router.post('/{conversation_id}/messages',response_model=TurnResponse)
async def send(conversation_id:UUID,request:SendMessageRequest,http_request:Request,conversations=Depends(service)):
    async def disconnected():
        while True:
            event=await http_request.receive()
            if event['type']=='http.disconnect': return
    generation=asyncio.create_task(conversations.send_message(conversation_id,request.message,request.client_request_id,
        execution_preview_id=request.execution_preview_id))
    monitor=asyncio.create_task(disconnected())
    try:
        await asyncio.wait((generation,monitor),return_when=asyncio.FIRST_COMPLETED)
        if monitor.done(): raise HTTPException(status_code=499,detail='conversation request cancelled')
        return await generation
    finally:
        for task in (generation,monitor):
            if not task.done(): task.cancel()
        await asyncio.gather(generation,monitor,return_exceptions=True)
@router.get('/{conversation_id}/events')
async def events(conversation_id:UUID,request:Request,last_event_id:str|None=Header(default=None),conversations=Depends(service)):
    conversations.get_conversation(conversation_id)
    try: after=int(last_event_id or '0')
    except ValueError: raise HTTPException(status_code=400,detail='invalid Last-Event-ID')
    async def stream():
        nonlocal after
        while not await request.is_disconnected():
            rows=conversations.repository.events(conversation_id,after)
            for row in rows:
                after=row['id']
                yield f"id: {after}\nevent: {row['kind']}\ndata: {json.dumps(row['data'])}\n\n"
            if not rows: yield ': keepalive\n\n'
            await asyncio.sleep(.25)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
