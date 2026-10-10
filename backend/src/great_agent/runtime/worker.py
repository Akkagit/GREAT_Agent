from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from great_agent.adapters.great_pppflt import GreatPppfltAdapter, RenderedTask
from great_agent.db.repositories import ClaimedTask, TaskRepository
from great_agent.domain.dates import YearDoy
from great_agent.runtime.process_runner import ProcessRunner
from great_agent.runtime.result_inspector import PppfltResultInspector
from great_agent.runtime.run_storage import RunStorage
from great_agent.services.plan_service import PlanService


CommandFactory = Callable[[RenderedTask, Path], tuple[str, ...]]


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
    ) -> None:
        self.repository = repository
        self.plan_service = plan_service
        self.adapter = adapter
        self.run_storage = run_storage
        self.process_runner = process_runner
        self.result_inspector = result_inspector
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._command_factory = command_factory

    async def run_once(self) -> bool:
        claims: list[ClaimedTask] = []
        for slot in range(self.max_concurrency):
            claim = self.repository.claim_next(f"worker-{slot + 1}")
            if claim is None:
                break
            claims.append(claim)
        if not claims:
            return False
        await asyncio.gather(*(self._run_claimed(claim) for claim in claims))
        return True

    async def run_forever(self, *, idle_seconds: float = 0.5) -> None:
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(idle_seconds)

    async def cancel_experiment(self, experiment_id: UUID) -> None:
        await asyncio.gather(
            *(
                self.process_runner.cancel(task_id)
                for task_id in self.repository.running_task_ids(experiment_id)
            )
        )

    def retry(self, task_id: UUID) -> None:
        self.repository.retry_task(task_id)

    async def _run_claimed(self, claim: ClaimedTask) -> None:
        async with self._semaphore:
            revision = self.plan_service.get_current_revision(claim.experiment_id)
            if revision.id != claim.revision_id:
                self.repository.mark_attempt_failed(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=None,
                    error_code="plan_validation_error",
                )
                return
            date = YearDoy(year=claim.year, doy=claim.doy)
            task_dir = self.run_storage.task_dir(claim.experiment_id, date)
            attempt_dir = task_dir / "attempts" / f"{claim.attempt_number:04d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            rendered = self.adapter.render_task(revision.spec, date, attempt_dir)
            command = (
                self._command_factory(rendered, attempt_dir)
                if self._command_factory is not None
                else self.adapter.build_command(rendered)
            )
            log_path = attempt_dir / "solver.log"
            self.repository.set_attempt_log_path(
                claim.task_id, claim.attempt_id, str(log_path)
            )
            try:
                outcome = await self.process_runner.run(
                    claim.task_id,
                    command,
                    cwd=attempt_dir,
                    log_path=log_path,
                    on_started=lambda pid: self.repository.record_pid(
                        claim.task_id, claim.attempt_id, pid
                    ),
                )
            except OSError:
                self.repository.mark_attempt_failed(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=None,
                    error_code="launch_error",
                )
                return
            if outcome.cancelled:
                self.repository.mark_attempt_cancelled(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=outcome.exit_code,
                )
                return
            if outcome.exit_code != 0:
                self.repository.mark_attempt_failed(
                    claim.task_id,
                    claim.attempt_id,
                    exit_code=outcome.exit_code,
                    error_code="solver_exit_error",
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
                )
                return
            self.repository.add_result_files(
                claim.task_id, claim.attempt_id, report.files
            )
            self.repository.mark_attempt_succeeded(
                claim.task_id, claim.attempt_id, exit_code=outcome.exit_code
            )
