from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import psutil
from sqlalchemy.exc import DBAPIError

from great_agent.adapters.great_pppflt import GreatPppfltAdapter, RenderedTask
from great_agent.db.repositories import ClaimedTask, TaskRepository
from great_agent.domain.dates import YearDoy
from great_agent.runtime.process_runner import ProcessCleanupError, ProcessRunner
from great_agent.runtime.result_inspector import PppfltResultInspector
from great_agent.runtime.run_storage import RunStorage
from great_agent.services.plan_service import PlanService
from great_agent.runtime.analysis_worker import AnalysisWorker
from great_agent.services.execution_service import ExecutionService
from great_agent.errors import ExecutionReviewError, TaskStateError, UnsafePathError
from great_agent.db.recovery_repository import RecoveryRepository
from great_agent.runtime.process_identity import capture_identity
from great_agent.runtime.recovery_service import RecoveryService


CommandFactory = Callable[[RenderedTask, Path], tuple[str, ...]]
logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        repository: TaskRepository,
        plan_service: PlanService,
        adapter: GreatPppfltAdapter,
        run_storage: RunStorage,
        process_runner: ProcessRunner,
        result_inspector: PppfltResultInspector,
        max_concurrency: int,
        command_factory: CommandFactory | None = None,
        convergence_hold_seconds: float = 600,
        execution_service: ExecutionService | None = None,
        owner_session_id: str | None = None,
        recovery_service: RecoveryService | None = None,
    ) -> None:
        self.repository = repository
        self.plan_service = plan_service
        self.adapter = adapter
        self.run_storage = run_storage
        self.process_runner = process_runner
        self.result_inspector = result_inspector
        self.max_concurrency = max_concurrency
        self.convergence_hold_seconds = convergence_hold_seconds
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._command_factory = command_factory
        self.execution_service = execution_service
        self.owner_session_id = owner_session_id or str(uuid4())
        self.recovery_service = recovery_service or RecoveryService(
            RecoveryRepository(repository._session_factory), owner_session_id=self.owner_session_id)
        self.analysis_worker = AnalysisWorker(repository, plan_service, run_storage, self.owner_session_id)

    async def run_once(self) -> bool:
        claims: list[ClaimedTask] = []
        for slot in range(self.max_concurrency):
            try:
                claim = self.repository.claim_next(
                    f"worker-{slot + 1}", max_concurrency=self.max_concurrency,
                    owner_session_id=self.owner_session_id,
                )
            except DBAPIError:
                logger.exception('Task claim database unavailable; retrying next interval')
                break
            if claim is None:
                break
            claims.append(claim)
        if claims:
            await asyncio.gather(*(self._run_claimed(claim) for claim in claims))
        # Explicit one-shot/test callers retain their wait-until-analysis-done
        # contract. The production loop runs this consumer independently.
        analyzed = False
        while await self.analysis_worker.run_once():
            analyzed = True
        return bool(claims) or analyzed

    async def run_forever(self, *, idle_seconds: float = 0.5) -> None:
        # The observer must keep running while a batch awaits a long solver.
        await self.recovery_service.wait_until_ready()
        self.analysis_worker.repository.recover(self.owner_session_id)
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.recovery_service.run_forever())
                group.create_task(self._run_claim_loop(idle_seconds))
                group.create_task(self.analysis_worker.run_forever(idle_seconds))
                group.create_task(self._heartbeat_loop())
        finally:
            try:
                self._heartbeat('stopped')
            except DBAPIError:
                logger.exception('Final heartbeat unavailable; readiness will expire')

    async def _run_claim_loop(self, idle_seconds: float) -> None:
        active = set()
        try:
            while True:
                while len(active) < self.max_concurrency:
                    try:
                        claim = self.repository.claim_next('solver', max_concurrency=self.max_concurrency,
                            owner_session_id=self.owner_session_id)
                    except DBAPIError:
                        logger.exception('Task claim database unavailable; retrying next interval')
                        break
                    if claim is None:
                        break
                    active.add(asyncio.create_task(self._run_claimed(claim)))
                if active:
                    done, active = await asyncio.wait(active, timeout=idle_seconds,
                                                      return_when=asyncio.FIRST_COMPLETED)
                    for finished in done:
                        finished.result()
                else:
                    await asyncio.sleep(idle_seconds)
        finally:
            for future in active:
                future.cancel()
            await asyncio.gather(*active, return_exceptions=True)

    def _heartbeat(self, status):
        import os
        from great_agent.db.analysis_models import WorkerHeartbeatModel
        from great_agent.db.models import utc_now
        with self.repository._session_factory.begin() as session:
            row = session.get(WorkerHeartbeatModel, 'solver')
            if row is None:
                row = WorkerHeartbeatModel(role='solver', owner_session_id=self.owner_session_id,
                    pid=os.getpid(), status=status, checked_at=utc_now())
                session.add(row)
            elif status == 'running' or row.owner_session_id == self.owner_session_id:
                row.owner_session_id, row.pid = self.owner_session_id, os.getpid()
                row.status, row.checked_at = status, utc_now()

    async def _heartbeat_loop(self):
        while True:
            try:
                self._heartbeat('running')
            except DBAPIError:
                logger.exception('Worker heartbeat unavailable')
            await asyncio.sleep(1)

    async def cancel_experiment(self, experiment_id: UUID) -> None:
        self.repository.request_experiment_cancel(experiment_id)
        await asyncio.gather(
            *(
                self.process_runner.cancel(task_id)
                for task_id in self.repository.running_task_ids(experiment_id)
            )
        )

    def retry(self, task_id: UUID) -> None:
        raise ExecutionReviewError("重试必须先复核并确认新的 XML。", "execution_confirmation_required")

    async def _watch_cancellation(self, claim: ClaimedTask) -> None:
        while True:
            try:
                if self.repository.cancellation_requested(claim.task_id, claim.attempt_id):
                    if await self.process_runner.cancel(claim.task_id):
                        return
            except (DBAPIError, OSError):
                logger.exception('Cancellation check failed for attempt %s; retrying', claim.attempt_id)
            await asyncio.sleep(0.2)

    async def _run_claimed(self, claim: ClaimedTask) -> None:
        try:
            await self._execute_claimed(claim)
        except DBAPIError:
            # ProcessRunner reaps a child before propagating callback failures.
            # Other persistence sites occur before spawn or after a trusted wait.
            logger.exception('Attempt %s persistence failed', claim.attempt_id)
            self._record_failure(claim, 'state_persistence_error')
        except TaskStateError:
            logger.exception('Attempt %s ownership changed; ignoring stale callback', claim.attempt_id)

    def _record_failure(self, claim: ClaimedTask, error_code: str):
        try:
            self.repository.mark_attempt_failed(claim.task_id, claim.attempt_id, exit_code=None,
                error_code=error_code, owner_session_id=claim.owner_session_id)
        except DBAPIError:
            logger.exception('Attempt %s failure persistence deferred to recovery', claim.attempt_id)
            self.recovery_service.defer_failure(claim, error_code)
        except TaskStateError:
            logger.info('Attempt %s already closed or transferred', claim.attempt_id)

    async def _execute_claimed(self, claim: ClaimedTask) -> None:
        async with self._semaphore:
            revision = self.plan_service.get_current_revision(claim.experiment_id)
            if revision.id != claim.revision_id:
                self.repository.mark_attempt_failed(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=None,
                    error_code="plan_validation_error",
                    owner_session_id=claim.owner_session_id,
                )
                return
            date = YearDoy(year=claim.year, doy=claim.doy)
            task_dir = self.run_storage.task_dir(claim.experiment_id, date)
            attempt_dir = task_dir / "attempts" / f"{claim.attempt_number:04d}"
            try:
                if self.execution_service is None:
                    raise ExecutionReviewError("本次尝试尚未获得执行确认。", "execution_confirmation_required")
                job = self.execution_service.approved_job(claim.task_id, claim.attempt_number)
                attempt_dir = Path(job.cwd)
                attempt_dir.mkdir(parents=True, exist_ok=True)
                xml_path = Path(job.xml_path)
                approved_bytes = job.xml_content.encode("utf-8")
                if xml_path.exists():
                    if xml_path.read_bytes() != approved_bytes:
                        raise ExecutionReviewError("目标 XML 已被修改，拒绝覆盖和执行。")
                else:
                    with xml_path.open("xb") as stream:
                        stream.write(approved_bytes)
                rendered = RenderedTask(xml_path=xml_path, output_dir=Path(job.output_dir))
                command = self._command_factory(rendered, attempt_dir) if self._command_factory is not None else job.command
            except (ExecutionReviewError, UnsafePathError, OSError) as error:
                self.repository.mark_attempt_failed(claim.task_id, claim.attempt_id, exit_code=None,
                    owner_session_id=claim.owner_session_id,
                    error_code=error.code if isinstance(error, ExecutionReviewError) else "execution_review_invalid")
                self.repository.publish_event(claim.task_id, "execution_blocked", {"message": str(error)})
                return
            log_path = attempt_dir / "solver.log"
            self.repository.set_attempt_log_path(
                claim.task_id, claim.attempt_id, str(log_path), owner_session_id=claim.owner_session_id,
            )
            self.repository.prepare_spawn(claim.task_id, claim.attempt_id, command,
                                          owner_session_id=claim.owner_session_id)
            def on_started(pid):
                try:
                    identity = capture_identity(pid)
                except psutil.NoSuchProcess:
                    # A child that already exited is still awaited through the
                    # subprocess handle and has a trustworthy exit status.
                    identity = None
                self.repository.record_pid(claim.task_id, claim.attempt_id, pid,
                    owner_session_id=claim.owner_session_id, identity=identity)
            cancellation_watcher = asyncio.create_task(self._watch_cancellation(claim))
            try:
                outcome = await self.process_runner.run(
                    claim.task_id,
                    command,
                    cwd=attempt_dir,
                    log_path=log_path,
                    on_started=on_started,
                )
            except ProcessCleanupError:
                logger.exception('Attempt %s child cleanup uncertain; keeping quota occupied', claim.attempt_id)
                self.recovery_service.defer_cleanup(claim, self.process_runner)
                return
            except (OSError, psutil.Error, RuntimeError, DBAPIError):
                logger.exception('Attempt %s launch failed after process cleanup', claim.attempt_id)
                self._record_failure(claim, 'launch_error')
                return
            finally:
                cancellation_watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await cancellation_watcher
            if outcome.cancelled:
                self.repository.mark_attempt_cancelled(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=outcome.exit_code,
                    owner_session_id=claim.owner_session_id,
                )
                return
            if outcome.exit_code != 0:
                self.repository.mark_attempt_failed(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=outcome.exit_code,
                    error_code="solver_exit_error",
                    owner_session_id=claim.owner_session_id,
                )
                return
            report = self.result_inspector.inspect(
                attempt_dir, expected_stations=revision.spec.stations
            )
            if not report.ok:
                self.repository.add_result_files(
                    claim.task_id, claim.attempt_id, report.files
                )
                self.repository.mark_attempt_failed(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=outcome.exit_code,
                    error_code=report.error_code or "missing_output_error",
                    owner_session_id=claim.owner_session_id,
                )
                return
            scenario = revision.spec.scenario
            config = {'hold_seconds': self.convergence_hold_seconds} if (
                scenario.mode, scenario.frequency, scenario.ambiguity) == ('static', 'DF', 'float') else None
            self.repository.complete_solver(claim, report.files, analysis_config=config)
