from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from great_agent.api.dependencies import AppServices, get_services


router = APIRouter(prefix="/api/v1/tasks", tags=["events"])


@router.get("/{task_id}/events")
def task_events(
    task_id: UUID,
    request: Request,
    once: bool = False,
    last_event_id: int = Header(default=0, alias="Last-Event-ID"),
    services: AppServices = Depends(get_services),
) -> StreamingResponse:
    async def stream():
        sequence = last_event_id
        while True:
            events = services.task_repository.list_events(
                task_id, after_sequence=sequence
            )
            for event in events:
                sequence = event.sequence
                data = json.dumps(
                    event.payload, ensure_ascii=False, separators=(",", ":")
                )
                yield f"id: {event.sequence}\nevent: {event.kind}\ndata: {data}\n\n"
            if once or await request.is_disconnected():
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream")
