from __future__ import annotations

import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from great_agent.api.dependencies import AppServices, get_services
from great_agent.db.monitor_repository import CursorError, MonitorRepository


router = APIRouter(prefix="/api/v1/experiments", tags=["monitor"])
POLL_SECONDS = 0.5
HEARTBEAT_SECONDS = 15.0


def _repository(services: AppServices) -> MonitorRepository:
    return MonitorRepository(services.task_repository._session_factory)


def _cursor_error(error: CursorError) -> JSONResponse:
    return JSONResponse(status_code=error.status_code, content={
        "error": {"code": error.code, "message": str(error), "details": []},
    })


@router.get("/{experiment_id}/monitor")
def experiment_monitor(experiment_id: UUID, services: AppServices = Depends(get_services)) -> dict:
    return _repository(services).snapshot(experiment_id)


@router.get("/{experiment_id}/events", response_model=None)
async def experiment_events(
    experiment_id: UUID, request: Request, once: bool = False,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    services: AppServices = Depends(get_services),
):
    repository = await asyncio.to_thread(_repository, services)
    # Validate and materialize the initial bounded batch before HTTP headers are sent.
    try:
        batch = await asyncio.to_thread(repository.events, experiment_id, last_event_id)
    except CursorError as error:
        return _cursor_error(error)

    async def stream():
        cursor = last_event_id
        pending = batch
        last_write = time.monotonic()
        while True:
            for item in pending:
                if await request.is_disconnected():
                    return
                cursor = item["event_id"]
                data = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {data}\n\n"
                last_write = time.monotonic()
            if once or await request.is_disconnected():
                return
            if len(pending) < 200:
                if time.monotonic() - last_write >= HEARTBEAT_SECONDS:
                    yield ": heartbeat\n\n"
                    last_write = time.monotonic()
                await asyncio.sleep(POLL_SECONDS)
            try:
                # events() closes its read transaction and session before returning.
                pending = await asyncio.to_thread(repository.events, experiment_id, cursor)
            except CursorError:
                # An epoch rotation after headers cannot change the HTTP status.
                # Close the stream; reconnect validation returns the required 409.
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
