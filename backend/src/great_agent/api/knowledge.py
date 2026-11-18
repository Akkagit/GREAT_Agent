"""Local, scoped knowledge management. Upload bytes never authorize execution."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from great_agent.api.dependencies import get_services
from great_agent.db.knowledge_repository import KnowledgeConflict, KnowledgeNotFound, KnowledgeValidationError

router = APIRouter(prefix='/api/v1/knowledge', tags=['knowledge'])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BaseInput(Input):
    name: str = Field(min_length=1, max_length=200)


class EnabledInput(Input):
    enabled: bool


class ReparseInput(Input):
    parser: Literal['pdfplumber','docling'] = 'pdfplumber'
    request_id: UUID


class SearchInput(Input):
    query: str = Field(min_length=1,max_length=2000)
    document_ids: list[UUID] | None = Field(default=None,max_length=100)
    limit: int = Field(default=8,ge=1,le=50)


def service(services=Depends(get_services)):
    result = services.knowledge_service
    if result is None:
        raise HTTPException(503,detail='知识库未启用。')
    return result


async def call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn,*args,**kwargs)
    except KnowledgeNotFound as error:
        raise HTTPException(404,detail=str(error)) from error
    except KnowledgeConflict as error:
        raise HTTPException(409,detail=str(error)) from error
    except (KnowledgeValidationError,ValueError) as error:
        raise HTTPException(422,detail=str(error)) from error


@router.get('/bases')
async def bases(s=Depends(service)):
    return {'items':await call(s.repository.list_bases)}


@router.post('/bases',status_code=201)
async def create_base(body:BaseInput,s=Depends(service)):
    return await call(s.repository.create_base,body.name)


@router.get('/bases/{base_id}/documents')
async def documents(base_id:UUID,s=Depends(service)):
    return {'items':await call(s.repository.list_documents,str(base_id))}


@router.post('/bases/{base_id}/documents',status_code=202)
async def upload(base_id:UUID,request:Request,filename:Annotated[str,Query(min_length=1,max_length=255)],s=Depends(service)):
    await call(s.repository.get_base,str(base_id))
    try:
        s.storage._filename_type(filename)
    except ValueError as error:
        raise HTTPException(422,detail=str(error)) from error
    size_header=request.headers.get('content-length')
    if size_header and (not size_header.isdecimal() or int(size_header)>s.storage.max_upload_bytes):
        raise HTTPException(413,detail='上传文件超过大小限制。')
    staging=s.storage.original_path('staging')
    await asyncio.to_thread(staging.mkdir,parents=True,exist_ok=True)
    path=s.storage.original_path(f'staging/upload-{uuid4()}.part')
    total=0
    stream=await asyncio.to_thread(path.open,'xb')
    try:
        async for data in request.stream():
            total+=len(data)
            if total>s.storage.max_upload_bytes:
                raise HTTPException(413,detail='上传文件超过大小限制。')
            await asyncio.to_thread(stream.write,data)
        await asyncio.to_thread(stream.close)
        return await call(s.register_staged_upload,str(base_id),filename,path)
    finally:
        if not stream.closed:
            await asyncio.to_thread(stream.close)
        with suppress(OSError):
            await asyncio.to_thread(path.unlink,missing_ok=True)


@router.get('/documents/{document_id}')
async def document(document_id:UUID,s=Depends(service)):
    return await call(s.repository.get_document,str(document_id))


@router.patch('/documents/{document_id}')
async def enabled(document_id:UUID,body:EnabledInput,s=Depends(service)):
    return await call(s.repository.set_enabled,str(document_id),body.enabled)


@router.post('/documents/{document_id}/reparse',status_code=202)
async def reparse(document_id:UUID,body:ReparseInput,s=Depends(service)):
    job=await call(s.repository.queue_reparse,str(document_id),body.parser,str(body.request_id))
    return {'document':await call(s.repository.get_document,str(document_id)),'job':job,'duplicate':False}


@router.get('/jobs/{job_id}')
async def job(job_id:UUID,s=Depends(service)):
    return await call(s.repository.get_job,str(job_id))


@router.post('/bases/{base_id}/search')
async def search(base_id:UUID,body:SearchInput,s=Depends(service)):
    ids=None if body.document_ids is None else [str(i) for i in body.document_ids]
    return await call(s.repository.search,str(base_id),body.query,document_ids=ids,limit=body.limit)


@router.get('/documents/{document_id}/chunks')
async def chunks(document_id:UUID,version_id:UUID|None=None,offset:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100),s=Depends(service)):
    return await call(s.repository.list_chunks,str(document_id),version_id=str(version_id) if version_id else None,offset=offset,limit=limit)


@router.get('/chunks/{chunk_id}')
async def chunk(chunk_id:UUID,s=Depends(service)):
    return await call(s.repository.get_chunk,str(chunk_id))


@router.get('/documents/{document_id}/original')
async def original(document_id:UUID,s=Depends(service)):
    path,doc=await call(s.original,str(document_id))
    return FileResponse(path,media_type=doc['blob']['media_type'],filename=doc['filename'],
                        content_disposition_type='inline',headers={'X-Content-Type-Options':'nosniff'})
