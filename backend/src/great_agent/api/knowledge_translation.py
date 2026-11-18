"""Independent, cancellable translation endpoint for one authoritative citation."""
import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from great_agent.api.dependencies import get_services
from great_agent.db.knowledge_chat_repository import KnowledgeChatNotFound, KnowledgeChatValidationError
from great_agent.db.knowledge_repository import KnowledgeNotFound
from great_agent.services.knowledge_translation_service import KnowledgeTranslationError

router = APIRouter(prefix='/api/v1/knowledge', tags=['knowledge-translation'])


class TranslationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_language: Literal['zh-CN', 'en']


def translation_service(services=Depends(get_services)):
    service = getattr(services, 'knowledge_translation_service', None)
    if service is None:
        raise HTTPException(503, detail='文献翻译未启用。')
    return service


@router.post('/conversations/{conversation_id}/messages/{message_id}/citations/{source_id}/translation')
async def translate(conversation_id:UUID, message_id:UUID, source_id:str, body:TranslationInput,
                    request:Request, service=Depends(translation_service)):
    async def disconnected():
        while True:
            event = await request.receive()
            if event['type'] == 'http.disconnect':
                return
    generation = asyncio.create_task(service.translate(str(conversation_id), str(message_id), source_id, body.target_language))
    monitor = asyncio.create_task(disconnected())
    try:
        await asyncio.wait((generation, monitor), return_when=asyncio.FIRST_COMPLETED)
        if generation.done():
            return await generation
        raise HTTPException(499, detail='文献翻译请求已取消。')
    except (KnowledgeChatNotFound, KnowledgeNotFound):
        return JSONResponse(status_code=404, content={'code':'knowledge_source_unavailable', 'detail':'文献或引用不可用。'})
    except KnowledgeChatValidationError:
        return JSONResponse(status_code=422, content={'code':'knowledge_source_unavailable', 'detail':'文献引用已不可用，请刷新来源。'})
    except KnowledgeTranslationError:
        return JSONResponse(status_code=502, content={'code':'knowledge_translation_failed', 'detail':'文献片段翻译未完成，请稍后重试。'})
    finally:
        for task in (generation, monitor):
            if not task.done():
                task.cancel()
        await asyncio.gather(generation, monitor, return_exceptions=True)
