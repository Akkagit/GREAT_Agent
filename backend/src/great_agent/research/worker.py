"""Independent durable research consumer; no solver or arbitrary execution tools."""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)


class ResearchWorker:
    def __init__(self, service, *, owner_id=None, heartbeat_seconds=1, lease_seconds=60):
        self.service = service
        self.repository = service.repository
        self.owner_id = owner_id or str(uuid4())
        self.heartbeat_seconds, self.lease_seconds = heartbeat_seconds, lease_seconds

    async def run_once(self):
        await asyncio.to_thread(self.repository.recover_expired)
        run = await asyncio.to_thread(self.repository.claim_next, self.owner_id, lease_seconds=self.lease_seconds)
        if run is None:
            return False
        task = asyncio.create_task(self.service.execute(run))
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=self.heartbeat_seconds)
                if done:
                    break
                alive = await asyncio.to_thread(self.repository.heartbeat, run['id'], self.owner_id,
                                                run['fencing_token'], lease_seconds=self.lease_seconds)
                if not alive:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return True
            await task
            return True
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def run_forever(self, idle_seconds=.5):
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(idle_seconds)
            except DBAPIError:
                logger.warning('research_queue_temporarily_unavailable')
                await asyncio.sleep(idle_seconds)
