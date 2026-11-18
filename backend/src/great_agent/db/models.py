from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ExperimentModel(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plan_revisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class PlanRevisionModel(Base):
    __tablename__ = "plan_revisions"
    __table_args__ = (
        UniqueConstraint("experiment_id", "number", name="uq_revision_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DailyTaskModel(Base):
    __tablename__ = "daily_tasks"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "revision_id",
            "year",
            "doy",
            name="uq_daily_task_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("experiments.id"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("plan_revisions.id"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    doy: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("task_attempts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TaskAttemptModel(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "number", name="uq_task_attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_tasks.id"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    log_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Historical attempts have unknown process identity. Never manufacture it
    # from the attempt timestamp or a PID which may since have been reused.
    owner_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    launch_phase: Mapped[str] = mapped_column(String(24), nullable=False, default="legacy_unknown", server_default="legacy_unknown")
    process_created_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    process_executable: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_state: Mapped[str] = mapped_column(String(24), nullable=False, default="none", server_default="none")
    recovery_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    analysis_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown", server_default="unknown")
    artifact_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class TaskEventModel(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_tasks.id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ResultFileModel(Base):
    __tablename__ = "result_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_tasks.id"), nullable=False, index=True
    )
    attempt_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_attempts.id"), nullable=False
    )
    station: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ExecutionPreviewModel(Base):
    __tablename__ = "execution_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("plan_revisions.id"))
    payload_json: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionApprovalModel(Base):
    __tablename__ = "execution_approvals"

    task_id: Mapped[str] = mapped_column(ForeignKey("daily_tasks.id"), primary_key=True)
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    preview_id: Mapped[str] = mapped_column(ForeignKey("execution_previews.id"), index=True)
    job_index: Mapped[int] = mapped_column(Integer)


class PlanProposalModel(Base):
    __tablename__ = "plan_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_id: Mapped[str] = mapped_column(
        ForeignKey("experiments.id"), nullable=False, index=True
    )
    base_revision_id: Mapped[str] = mapped_column(
        ForeignKey("plan_revisions.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_json: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_proposals.id"), nullable=True
    )
    accepted_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_revisions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

# Register conversation tables for create_all and Alembic metadata.
from great_agent.db import conversation_models  # noqa: E402,F401
from great_agent.db import monitor_models  # noqa: E402,F401
from great_agent.db import knowledge_models  # noqa: E402,F401
from great_agent.db import knowledge_chat_models  # noqa: E402,F401
from great_agent.db import knowledge_translation_models  # noqa: E402,F401
from great_agent.db import analysis_models  # noqa: E402,F401
from great_agent.research import models as research_models  # noqa: E402,F401
