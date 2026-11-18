"""Durable experiment event log; legacy task events keep their local ordering."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from great_agent.db.models import Base, utc_now


class ExperimentEventModel(Base):
    __tablename__ = "experiment_events"
    __table_args__ = (
        Index("ix_experiment_events_experiment_id_id", "experiment_id", "id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("daily_tasks.id"), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(ForeignKey("task_attempts.id"), nullable=True)
    task_event_id: Mapped[str | None] = mapped_column(ForeignKey("task_events.id"), nullable=True, unique=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class MonitorStateModel(Base):
    __tablename__ = "monitor_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epoch: Mapped[str] = mapped_column(String(36), nullable=False)
    # A cleanup operation must advance this per-experiment floor in its transaction.
    retention_floor_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
