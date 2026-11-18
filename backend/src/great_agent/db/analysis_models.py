"""Durable postprocessing work and the local solver's readiness heartbeat."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from great_agent.db.models import Base, utc_now


class AnalysisJobModel(Base):
    __tablename__ = 'analysis_jobs'
    __table_args__ = (UniqueConstraint('attempt_id', name='uq_analysis_job_attempt'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('daily_tasks.id'), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('task_attempts.id'))
    revision_id: Mapped[str] = mapped_column(ForeignKey('plan_revisions.id'))
    config_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    owner_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkerHeartbeatModel(Base):
    __tablename__ = 'worker_heartbeats'

    role: Mapped[str] = mapped_column(String(24), primary_key=True)
    owner_session_id: Mapped[str] = mapped_column(String(36))
    pid: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
