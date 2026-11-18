"""A durable analysis consumer independent from solver execution slots."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.exc import DBAPIError

from great_agent.db.analysis_repository import AnalysisJobRepository
from great_agent.errors import TaskStateError
from great_agent.services.result_analysis_service import analyze_task

logger = logging.getLogger(__name__)


class AnalysisWorker:
    def __init__(self, tasks, plan_service, storage, owner_session_id):
        self.tasks, self.plan_service, self.storage = tasks, plan_service, storage
        self.owner_session_id = owner_session_id
        self.repository = AnalysisJobRepository(tasks._session_factory)
        self._completion = None

    def _analyze(self, job):
        return analyze_task(job.task_id, repository=self.tasks, plan_service=self.plan_service,
            storage=self.storage, attempt_id=job.attempt_id, publish=False,
            hold_seconds=job.config['hold_seconds'])

    async def run_once(self):
        if self._completion is not None:
            self._save_completion()
            return True
        # The claim is a short transaction. Do not abandon it in a cancelled
        # thread, or leave a claimed job without its shutdown owner.
        job = self.repository.claim(self.owner_session_id)
        if job is None:
            return False
        generation = asyncio.create_task(asyncio.to_thread(self._analyze, job))
        cancelled = False
        try:
            while True:
                try:
                    files = await asyncio.shield(generation)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    # Repeated shutdown signals cannot cancel the future and
                    # leave its Python thread writing after WorkerLock release.
                    continue
        except Exception as error:
            self._completion = (job, {'status': 'failed', 'error_message': str(error)[:1500]})
        else:
            self._completion = (job, {'status': 'completed', 'files': files})
        try:
            self._save_completion()
        finally:
            if cancelled:
                raise asyncio.CancelledError
        return True

    def _save_completion(self):
        job, outcome = self._completion
        # Retain both files and the owned receipt during transient DB failures;
        # the next queue tick retries this write, never the solver or computation.
        try:
            self.repository.finish(job, **outcome)
        except TaskStateError:
            self._completion = None
            raise
        self._completion = None

    async def run_forever(self, idle_seconds=.5):
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(idle_seconds)
            except (DBAPIError, TaskStateError):
                logger.exception('Analysis queue unavailable; retrying without rerunning solver')
                await asyncio.sleep(idle_seconds)
