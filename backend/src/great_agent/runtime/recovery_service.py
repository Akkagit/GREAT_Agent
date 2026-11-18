from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import psutil
from sqlalchemy.exc import DBAPIError

from great_agent.runtime.process_identity import ProcessInspector
from great_agent.db.repositories import TaskRepository
from great_agent.errors import TaskStateError

logger = logging.getLogger(__name__)


class RecoveryService:
    def __init__(self, repository, *, owner_session_id: str, inspector=None, interval_seconds: float = 2):
        self.repository = repository
        self.owner_session_id = owner_session_id
        self.inspector = inspector if inspector is not None else ProcessInspector()
        self.interval_seconds = interval_seconds
        self._pending_failures = {}
        self._pending_cleanups = {}

    def defer_cleanup(self, claim, process_runner):
        self._pending_cleanups[str(claim.attempt_id)] = (claim, process_runner)

    def defer_failure(self, claim, error_code):
        # Only called after the process runner confirmed child cleanup or a
        # trusted wait. Keep quota occupied until this terminal write commits.
        self._pending_failures[str(claim.attempt_id)] = (claim, error_code)

    async def run_once(self):
        for attempt_id, (claim, runner) in tuple(self._pending_cleanups.items()):
            if not await runner.retry_cleanup(claim.task_id):
                try:
                    await asyncio.to_thread(self.repository.block_cleanup, claim)
                except (DBAPIError, TaskStateError):
                    logger.exception('Cannot persist uncertain cleanup for attempt %s', attempt_id)
                continue
            self.defer_failure(claim, 'launch_error')
            self._pending_cleanups.pop(attempt_id, None)
        for attempt_id, (claim, error_code) in tuple(self._pending_failures.items()):
            try:
                await asyncio.to_thread(TaskRepository(self.repository.sessions).mark_attempt_failed,
                    claim.task_id, claim.attempt_id, exit_code=None, error_code=error_code,
                    owner_session_id=claim.owner_session_id)
            except TaskStateError:
                pass
            except DBAPIError:
                continue
            self._pending_failures.pop(attempt_id, None)
        candidates = await asyncio.to_thread(self.repository.candidates, self.owner_session_id)
        for attempt in candidates:
            if attempt.attempt_id in self._pending_cleanups:
                # The original child handle is stronger evidence than process
                # discovery. Do not free its quota until owned cleanup succeeds.
                continue
            # Persist observer ownership before performing OS queries. Old callbacks
            # cannot write a terminal state after this point.
            if attempt.owner_session_id != self.owner_session_id:
                adopted = await asyncio.to_thread(self.repository.apply, attempt,
                    owner_session_id=self.owner_session_id, state='observing', reason='worker_lost', checked=False)
                if not adopted:
                    continue
                attempt = replace(attempt, owner_session_id=self.owner_session_id, version=attempt.version + 1)
            stop = await asyncio.to_thread(self.repository.stop_requested, attempt)
            identity = None
            if attempt.launch_phase == 'reserved':
                state, reason = 'interrupted', 'worker_lost'
            else:
                observed = await asyncio.to_thread(self.inspector.inspect, attempt, stop=stop)
                identity = observed.identity
                if observed.state == 'exited':
                    state = 'cancelled' if observed.stopped else 'interrupted'
                    reason = 'recovery_stopped' if observed.stopped else 'worker_lost'
                else:
                    state = 'observing' if observed.state == 'running' else 'blocked'
                    reason = observed.reason or ('orphan_running' if state == 'observing' else 'identity_unknown')
            await asyncio.to_thread(self.repository.apply, attempt, owner_session_id=self.owner_session_id,
                                    state=state, reason=reason, identity=identity)

    async def run_forever(self):
        while True:
            await self._guarded_once()
            await asyncio.sleep(self.interval_seconds)

    async def _guarded_once(self) -> bool:
        try:
            await self.run_once()
            return True
        except (DBAPIError, OSError, psutil.Error, ValueError):
            # A temporary database lock or inaccessible OS information
            # must not cancel healthy solvers in the worker TaskGroup.
            logger.exception('Recovery inspection failed; retrying on the next interval')
            return False

    async def wait_until_ready(self):
        while not await self._guarded_once():
            await asyncio.sleep(self.interval_seconds)
