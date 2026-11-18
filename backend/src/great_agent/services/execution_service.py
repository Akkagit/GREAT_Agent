from __future__ import annotations

import hashlib
import shutil
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from great_agent.db.models import DailyTaskModel, ExecutionApprovalModel, ExecutionPreviewModel, PlanProposalModel, TaskAttemptModel, utc_now
from great_agent.db.repositories import PlanRepository, TaskRepository
from great_agent.domain.dates import YearDoy, expand_dates
from great_agent.domain.execution import ExecutionJob, ExecutionPreview, InputSnapshot
from great_agent.errors import (
    ExecutionReviewError, PendingProposalError, PreflightError, RevisionConflictError, TaskStateError, UnsafePathError,
)

if TYPE_CHECKING:
    from great_agent.services.task_service import DailyTaskRecord, TaskService


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ExecutionService:
    """Prepare immutable review material; preparing is never queue authorization."""

    def __init__(self, task_service: TaskService) -> None:
        self.tasks = task_service
        self.sessions = task_service._session_factory
        self.plans = task_service.plan_service
        self.adapter = task_service.adapter
        self.storage = task_service.run_storage
        self.repository = TaskRepository(self.sessions)

    def get(self, preview_id: UUID) -> ExecutionPreview:
        with self.sessions() as session:
            model = session.get(ExecutionPreviewModel, str(preview_id))
            if model is None:
                raise ExecutionReviewError("执行预览不存在，请重新生成 XML。")
            return ExecutionPreview.model_validate_json(model.payload_json)

    def prepare(
        self, experiment_id: UUID, *, expected_revision: int, task_id: UUID | None = None,
    ) -> ExecutionPreview:
        revision = self.plans.get_current_revision(experiment_id)
        if revision.number != expected_revision:
            raise RevisionConflictError("计划已变化，请重新载入后生成 XML。")
        dates = expand_dates(revision.spec)
        attempt_number = 1
        if task_id is not None:
            task = self.repository.get_task(task_id)
            if task.experiment_id != experiment_id or task.status not in ("failed", "interrupted"):
                raise TaskStateError("只能复核本实验中失败或中断任务的下一次尝试。")
            dates = (YearDoy(year=task.year, doy=task.doy),)
            attempt_number = max((a.number for a in self.repository.list_attempts(task_id)), default=0) + 1
        else:
            with self.sessions() as session:
                if session.scalar(select(DailyTaskModel.id).where(DailyTaskModel.experiment_id == str(experiment_id)).limit(1)):
                    raise TaskStateError("实验已有任务，请查看运行监控；失败任务需单独复核重试。")

        reports = [self.adapter.preflight_task(revision.spec, date, self.storage.task_dir(experiment_id, date)) for date in dates]
        missing = tuple(item for report in reports for item in report.missing)
        if missing:
            raise PreflightError("输入文件缺失或为空，尚未生成执行预览。", missing)
        preview_id = uuid4()
        staging = self.storage.resolve(str(experiment_id), ".preview-staging", str(preview_id))
        destination = self.storage.resolve(str(experiment_id), "previews", str(preview_id))
        staging_owned = moved = published = False
        try:
            staging.mkdir(parents=True, exist_ok=False)
            staging_owned = True
            executable_path = str(self.adapter.settings.executable.resolve())
            executable_sha256 = file_sha256(self.adapter.settings.executable)
            template_path = str(self.adapter.settings.template_path.resolve())
            template_sha256 = file_sha256(self.adapter.settings.template_path)
            jobs = []
            staged_paths = []
            for date, report in zip(dates, reports, strict=True):
                attempt_dir = self.storage.resolve(str(experiment_id), "tasks", f"{date.year}_{date.doy:03d}", "attempts", f"{attempt_number:04d}")
                snapshot = self.storage.resolve(str(experiment_id), "previews", str(preview_id), f"{date.year}_{date.doy:03d}", "config.xml")
                staged_xml = self.storage.resolve(str(experiment_id), ".preview-staging", str(preview_id), f"{date.year}_{date.doy:03d}", "config.xml")
                inputs = []
                for item in report.items:
                    stat = item.path.stat()
                    inputs.append(InputSnapshot(kind=item.kind, station=item.station, path=str(item.path.resolve()), size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns))
                rendered = self.adapter.render_task(revision.spec, date, attempt_dir, xml_path=staged_xml)
                # Canonical UTF-8/LF bytes are both shown and executed, including on Windows.
                content = staged_xml.read_text(encoding="utf-8")
                staged_xml.write_bytes(content.encode("utf-8"))
                final_xml = self.storage.resolve(str(experiment_id), "tasks", f"{date.year}_{date.doy:03d}", "attempts", f"{attempt_number:04d}", "config.xml")
                jobs.append(ExecutionJob(
                    year=date.year, doy=date.doy, attempt_number=attempt_number, task_id=task_id,
                    snapshot_path=str(snapshot), xml_path=str(final_xml), xml_content=content,
                    xml_sha256=file_sha256(staged_xml), output_dir=str(rendered.output_dir), cwd=str(attempt_dir),
                    command=(executable_path, "-x", str(final_xml)), inputs=tuple(inputs),
                ))
                staged_paths.append(staged_xml)
            preview = ExecutionPreview(
                id=preview_id, experiment_id=experiment_id, revision=revision.number, revision_id=revision.id,
                intent="retry" if task_id else "initial", expires_at=utc_now() + timedelta(minutes=30),
                executable_path=executable_path, executable_sha256=executable_sha256,
                template_path=template_path, template_sha256=template_sha256,
                spec=revision.spec, jobs=tuple(jobs),
            )
            self._verify_program_files(preview)
            for job, staged_xml in zip(preview.jobs, staged_paths, strict=True):
                self._verify_job(preview, job, staged_snapshot=staged_xml)

            # Rendering and file hashing never hold SQLite's writer reservation.
            # Reserve it only to compare the state again and atomically persist
            # the freeze and review record. No failure path ever thaws a plan.
            with self.sessions.begin() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                plans = PlanRepository(session)
                experiment = plans.require_experiment(str(experiment_id))
                current = plans.require_current_revision(experiment)
                if current.id != str(revision.id) or current.number != expected_revision:
                    raise RevisionConflictError("计划已变化，请重新生成 XML。")
                if session.scalar(select(PlanProposalModel.id).where(
                    PlanProposalModel.experiment_id == str(experiment_id),
                    PlanProposalModel.status == "pending",
                ).limit(1)) is not None:
                    raise PendingProposalError("存在尚未处理的提案，请先接受或放弃后再生成 XML。")
                if task_id is None:
                    if session.scalar(select(DailyTaskModel.id).where(DailyTaskModel.experiment_id == str(experiment_id)).limit(1)):
                        raise TaskStateError("实验已有任务，请查看运行监控；失败任务需单独复核重试。")
                else:
                    task = session.get(DailyTaskModel, str(task_id))
                    last_number = session.scalar(select(func.max(TaskAttemptModel.number)).where(TaskAttemptModel.task_id == str(task_id))) or 0
                    if (task is None or task.experiment_id != str(experiment_id) or task.revision_id != current.id
                            or task.status not in ("failed", "interrupted") or attempt_number != last_number + 1
                            or (task.year, task.doy) != (dates[0].year, dates[0].doy)):
                        raise TaskStateError("任务状态已变化，请重新复核下一次尝试。")
                if not current.is_frozen:
                    self.plans.freeze_revision(experiment_id, expected_revision=expected_revision, session=session)
                elif experiment.status != "ready":
                    raise RevisionConflictError("冻结计划状态不一致，请重新载入。")
                session.add(ExecutionPreviewModel(id=str(preview.id), experiment_id=str(experiment_id), revision_id=str(revision.id), payload_json=preview.model_dump_json()))
                session.flush()
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(destination)
                moved = True
            published = True
            return preview
        finally:
            if staging_owned and not moved:
                self._remove_unpublished_preview(staging)
            if moved and not published:
                # A commit error may have an ambiguous outcome. Keep files if
                # the row became durable, or if that outcome cannot be checked.
                try:
                    with self.sessions() as session:
                        registered = session.get(ExecutionPreviewModel, str(preview_id)) is not None
                except Exception:
                    registered = True
                if not registered:
                    self._remove_unpublished_preview(destination)

    def _remove_unpublished_preview(self, path: Path) -> None:
        # Check the absolute deletion target again: a replaced staging directory
        # or parent must not redirect recursive cleanup to another run.
        resolved = path.resolve()
        if resolved != path or resolved == self.storage.root or not resolved.is_relative_to(self.storage.root):
            raise UnsafePathError("preview cleanup path must remain inside run_root")
        if path.exists():
            shutil.rmtree(path)

    def confirm(
        self, experiment_id: UUID, *, preview_id: UUID, expected_revision: int, confirmed: bool,
        session=None,
    ) -> tuple[DailyTaskRecord, ...]:
        if confirmed is not True:
            raise ExecutionReviewError("请明确确认本次执行配置。", "execution_confirmation_required")
        # SQLite writer reservation serializes competing previews and retry approvals.
        # A chat confirmation supplies its reserved transaction so queue records
        # and its durable receipt either commit together or both roll back.
        owns_transaction = session is None
        with self.sessions.begin() if owns_transaction else nullcontext(session) as session:
            if owns_transaction:
                session.execute(text("BEGIN IMMEDIATE"))
            model = session.get(ExecutionPreviewModel, str(preview_id))
            if model is None or model.experiment_id != str(experiment_id):
                raise ExecutionReviewError("执行预览不存在或不属于当前实验。")
            preview = ExecutionPreview.model_validate_json(model.payload_json)
            revision = self.plans.get_current_revision(experiment_id)
            if preview.revision != expected_revision or revision.id != preview.revision_id or not revision.is_frozen:
                raise RevisionConflictError("执行预览与当前冻结计划不一致，请重新复核。")
            if model.confirmed_at is not None:
                task_ids = session.scalars(select(ExecutionApprovalModel.task_id).where(ExecutionApprovalModel.preview_id == str(preview_id))).all()
                return tuple(self.tasks._record(session.get(DailyTaskModel, task_id), len(preview.spec.stations)) for task_id in task_ids)
            if utc_now() >= preview.expires_at:
                raise ExecutionReviewError("执行预览已过期，请重新检查输入并生成 XML。")
            self._verify_identity(preview)
            for job in preview.jobs:
                self._verify_job(preview, job)
            records = []
            if preview.intent == "initial":
                existing = session.scalar(select(DailyTaskModel.id).where(DailyTaskModel.experiment_id == str(experiment_id)).limit(1))
                if existing:
                    raise TaskStateError("本实验已有任务，不能重复启动；请查看运行监控。")
                dates = expand_dates(preview.spec)
                if tuple((j.year, j.doy) for j in preview.jobs) != tuple((d.year, d.doy) for d in dates):
                    raise ExecutionReviewError("执行日期与冻结计划不一致。")
                reports = [self.adapter.preflight_task(preview.spec, d, self.storage.task_dir(experiment_id, d)) for d in dates]
                self.tasks._write_snapshots(experiment_id, revision, dates, [i for r in reports for i in r.items])
            for index, job in enumerate(preview.jobs):
                if preview.intent == "initial":
                    if job.attempt_number != 1 or job.task_id is not None:
                        raise ExecutionReviewError("首次执行的尝试编号无效。")
                    task = DailyTaskModel(id=str(uuid4()), experiment_id=str(experiment_id), revision_id=str(revision.id), year=job.year, doy=job.doy, status="pending", worker_id=None, active_attempt_id=None)
                    session.add(task)
                    session.flush()
                else:
                    task = session.get(DailyTaskModel, str(job.task_id))
                    last_number = session.scalar(select(func.max(TaskAttemptModel.number)).where(TaskAttemptModel.task_id == str(job.task_id))) or 0
                    if (task is None or task.experiment_id != str(experiment_id) or task.revision_id != str(preview.revision_id)
                            or task.status not in ("failed", "interrupted") or job.attempt_number != last_number + 1
                            or (task.year, task.doy) != (job.year, job.doy)):
                        raise TaskStateError("任务状态已变化，请重新复核下一次尝试。")
                    task.status = "pending"
                    task.worker_id = None
                session.add(ExecutionApprovalModel(task_id=task.id, attempt_number=job.attempt_number, preview_id=str(preview.id), job_index=index))
                self.repository._publish_event(
                    session, task.id,
                    "task_created" if preview.intent == "initial" else "attempt_queued",
                    {"status": "pending", "next_attempt_number": job.attempt_number},
                )
                records.append(self.tasks._record(task, len(preview.spec.stations)))
            model.confirmed_at = utc_now()
            return tuple(records)

    def approved_job(self, task_id: UUID, attempt_number: int) -> ExecutionJob:
        with self.sessions() as session:
            approval = session.get(ExecutionApprovalModel, (str(task_id), attempt_number))
            if approval is None:
                raise ExecutionReviewError("本次尝试尚未获得执行确认。", "execution_confirmation_required")
            model = session.get(ExecutionPreviewModel, approval.preview_id)
            if model is None or model.confirmed_at is None:
                raise ExecutionReviewError("执行授权无效。", "execution_confirmation_required")
            preview = ExecutionPreview.model_validate_json(model.payload_json)
            job = preview.jobs[approval.job_index]
            task = session.get(DailyTaskModel, str(task_id))
            if (task is None or task.experiment_id != str(preview.experiment_id) or task.revision_id != str(preview.revision_id)
                    or (task.year, task.doy, attempt_number) != (job.year, job.doy, job.attempt_number)):
                raise ExecutionReviewError("任务与已确认配置不一致。")
        self._verify_identity(preview)
        self._verify_job(preview, job)
        return job

    def _verify_identity(self, preview: ExecutionPreview) -> None:
        revision = self.plans.get_current_revision(preview.experiment_id)
        if revision.id != preview.revision_id or revision.spec != preview.spec or not revision.is_frozen:
            raise ExecutionReviewError("冻结计划已变化，请重新复核。")
        self._verify_program_files(preview)

    def _verify_program_files(self, preview: ExecutionPreview) -> None:
        try:
            for actual, reviewed, digest in (
                (self.adapter.settings.executable, preview.executable_path, preview.executable_sha256),
                (self.adapter.settings.template_path, preview.template_path, preview.template_sha256),
            ):
                if str(actual.resolve()) != reviewed or file_sha256(actual) != digest:
                    raise ExecutionReviewError("程序或模板已变化，请重新生成执行预览。")
        except OSError as error:
            raise ExecutionReviewError("程序或模板无法读取，请重新检查输入。") from error

    def _verify_job(self, preview: ExecutionPreview, job: ExecutionJob, *, staged_snapshot: Path | None = None) -> None:
        date = YearDoy(year=job.year, doy=job.doy)
        attempt_dir = self.storage.resolve(str(preview.experiment_id), "tasks", f"{date.year}_{date.doy:03d}", "attempts", f"{job.attempt_number:04d}")
        snapshot = self.storage.resolve(str(preview.experiment_id), "previews", str(preview.id), f"{date.year}_{date.doy:03d}", "config.xml")
        final_xml = self.storage.resolve(str(preview.experiment_id), "tasks", f"{date.year}_{date.doy:03d}", "attempts", f"{job.attempt_number:04d}", "config.xml")
        if (job.snapshot_path != str(snapshot) or job.xml_path != str(final_xml) or job.cwd != str(attempt_dir)
                or job.output_dir != str((attempt_dir / "results").resolve())
                or not Path(job.output_dir).is_relative_to(self.storage.root)
                or job.command != (preview.executable_path, "-x", job.xml_path)):
            raise ExecutionReviewError("执行路径或命令与复核快照不一致。")
        try:
            if staged_snapshot is not None:
                snapshot = staged_snapshot
            if file_sha256(snapshot) != job.xml_sha256 or snapshot.read_bytes() != job.xml_content.encode("utf-8"):
                raise ExecutionReviewError("XML 快照已变化，请重新生成执行预览。")
            for item in job.inputs:
                path = Path(item.path)
                stat = path.stat()
                if not path.is_file() or stat.st_size != item.size_bytes or stat.st_mtime_ns != item.mtime_ns:
                    raise ExecutionReviewError(f"输入已变化，请重新检查：{item.path}")
        except OSError as error:
            raise ExecutionReviewError("XML 或输入文件无法读取，请重新检查。") from error
