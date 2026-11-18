"""Read-only research workspace: durable acceptance, observation and explicit stop."""
from __future__ import annotations

import asyncio
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from great_agent.api.dependencies import get_services
from great_agent.research.errors import ResearchError

router = APIRouter(prefix='/api/v1/research', tags=['research'])


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ExperimentMaterial(Input):
    kind: Literal['experiment']
    experiment_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    role: Literal['baseline', 'comparison'] = 'baseline'


class CodeMaterial(Input):
    kind: Literal['code']
    source_id: str = Field(min_length=1, max_length=64)
    paths: list[str] = Field(min_length=1, max_length=200)
    provenance: Literal['unknown', 'user_declared'] = 'unknown'
    snapshot_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    snapshot_material_id: str | None = Field(default=None, min_length=1, max_length=1000)


class LiteratureMaterial(Input):
    kind: Literal['literature']
    document_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)


Material = Annotated[ExperimentMaterial | CodeMaterial | LiteratureMaterial, Field(discriminator='kind')]


class CreateSession(Input):
    title: str = Field(default='', max_length=200)
    goal: str = Field(default='', max_length=8000)
    materials: list[Material] = Field(default_factory=list, max_length=100)
    model_profile_id: str = 'default'
    mode: Literal['quick', 'deep'] = 'deep'


class MaterialUpdate(Input):
    expected_version: int = Field(ge=1, strict=True)
    materials: list[Material] = Field(default_factory=list, max_length=100)


class SettingsUpdate(Input):
    expected_version: int = Field(ge=1, strict=True)
    model_profile_id: str = 'default'
    mode: Literal['quick', 'deep'] = 'deep'


class Followup(Input):
    run_id: str = Field(min_length=1, max_length=128)
    message_id: str | None = None
    block_id: str | None = None
    item_id: str | None = None


class Turn(Input):
    request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1, strict=True)
    message: str = Field(min_length=1, max_length=8000)
    followup_target: Followup | None = None


class Retry(Input):
    request_id: str = Field(min_length=1, max_length=128)


class Report(Input):
    title: str = Field(default='', max_length=200)
    content: str = Field(default='', max_length=48000)
    run_ids: list[str] | None = Field(default=None, max_length=100)


def service(services=Depends(get_services)):
    research = getattr(services, 'research_service', None)
    if research is None:
        raise HTTPException(status_code=503, detail='科研分析尚未启用。')
    return research


async def call(function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except Exception as error:
        if isinstance(error, ResearchError) or (isinstance(error, ValueError) and hasattr(error, 'code')):
            raise ResearchError(error.code, str(error), getattr(error, 'status_code', 422), getattr(error, 'details', [])) from None
        if isinstance(error, ValueError):
            raise ResearchError('invalid_research_request', '研究问题或材料范围无效。', 422) from None
        raise


@router.get('/catalog')
async def catalog(s=Depends(service)):
    return await call(s.catalog)


@router.get('/sources')
async def sources(s=Depends(service)):
    return {'items': (await call(s.catalog))['sources']}


@router.get('/model-profiles')
async def profiles(s=Depends(service)):
    return {'items': s.model_profiles()}


@router.get('/sessions')
async def sessions(s=Depends(service)):
    return {'items': await call(s.repository.list_sessions)}


@router.post('/sessions', status_code=201)
async def create(body: CreateSession, s=Depends(service)):
    return await call(s.create_session, **body.model_dump(mode='json', exclude_none=True))


@router.get('/sessions/{session_id}')
async def detail(session_id: UUID, s=Depends(service)):
    return await call(s.get_session, str(session_id))


@router.patch('/sessions/{session_id}/materials')
async def materials(session_id: UUID, body: MaterialUpdate, s=Depends(service)):
    return await call(s.update_materials, str(session_id), **body.model_dump(mode='json', exclude_none=True))


@router.patch('/sessions/{session_id}/settings')
async def settings(session_id: UUID, body: SettingsUpdate, s=Depends(service)):
    return await call(s.update_settings, str(session_id), **body.model_dump())


@router.post('/sessions/{session_id}/runs', status_code=202)
async def submit(session_id: UUID, body: Turn, s=Depends(service)):
    return await call(s.submit, str(session_id), **body.model_dump(mode='json', exclude_none=True))


@router.get('/runs/{run_id}')
async def run(run_id: UUID, s=Depends(service)):
    return await call(s.get_run, str(run_id))


@router.post('/runs/{run_id}/cancel')
async def cancel(run_id: UUID, s=Depends(service)):
    return await call(s.cancel, str(run_id))


@router.post('/runs/{run_id}/retry', status_code=202)
async def retry(run_id: UUID, body: Retry, s=Depends(service)):
    return await call(s.retry, str(run_id), request_id=body.request_id)


@router.get('/runs/{run_id}/evidence/{alias}')
async def evidence(run_id: UUID, alias: str, s=Depends(service)):
    if len(alias) > 32:
        raise ResearchError('invalid_evidence', '证据编号无效。', 422)
    return await call(s.evidence, str(run_id), alias)


@router.get('/sessions/{session_id}/items')
async def items(session_id: UUID, s=Depends(service)):
    return {'items': (await call(s.get_session, str(session_id)))['items']}


@router.post('/sessions/{session_id}/reports', status_code=201)
async def save_report(session_id: UUID, body: Report, s=Depends(service)):
    return await call(s.create_report, str(session_id), **body.model_dump())


@router.get('/sessions/{session_id}/reports')
async def reports(session_id: UUID, s=Depends(service)):
    return {'items': await call(s.reports, str(session_id))}


@router.get('/sessions/{session_id}/events')
async def events(session_id: UUID, request: Request, last_event_id: str | None = Header(default=None),
                 once: bool = Query(default=False), s=Depends(service)):
    await call(s.get_session, str(session_id))
    try:
        after = int(last_event_id or '0')
        if after < 0:
            raise ValueError
    except ValueError:
        raise ResearchError('invalid_cursor', '事件游标无效。', 400) from None

    async def stream():
        nonlocal after
        while not await request.is_disconnected():
            rows = await call(s.repository.list_events, str(session_id), after=after)
            for row in rows:
                after = row['id']
                yield f'id: {after}\nevent: {row["type"]}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n'
            if once:
                return
            if not rows:
                yield ': keepalive\n\n'
            await asyncio.sleep(.5)
    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
