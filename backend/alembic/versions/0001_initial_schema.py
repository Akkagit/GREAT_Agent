"""Create the initial GREAT Agent schema.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["current_revision_id"], ["plan_revisions.id"]
        ),
    )
    op.create_table(
        "plan_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("is_frozen", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.UniqueConstraint(
            "experiment_id", "number", name="uq_revision_number"
        ),
    )
    op.create_index(
        "ix_plan_revisions_experiment_id", "plan_revisions", ["experiment_id"]
    )
    op.create_table(
        "daily_tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("doy", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("active_attempt_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["plan_revisions.id"]),
        sa.ForeignKeyConstraint(["active_attempt_id"], ["task_attempts.id"]),
        sa.UniqueConstraint(
            "experiment_id",
            "revision_id",
            "year",
            "doy",
            name="uq_daily_task_date",
        ),
    )
    op.create_index(
        "ix_daily_tasks_experiment_id", "daily_tasks", ["experiment_id"]
    )
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("log_path", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["daily_tasks.id"]),
        sa.UniqueConstraint("task_id", "number", name="uq_task_attempt_number"),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.create_table(
        "task_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["daily_tasks.id"]),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_table(
        "result_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("station", sa.String(16), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["daily_tasks.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["task_attempts.id"]),
    )
    op.create_index("ix_result_files_task_id", "result_files", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_result_files_task_id", table_name="result_files")
    op.drop_table("result_files")
    op.drop_index("ix_task_events_task_id", table_name="task_events")
    op.drop_table("task_events")
    op.drop_index("ix_task_attempts_task_id", table_name="task_attempts")
    op.drop_table("task_attempts")
    op.drop_index("ix_daily_tasks_experiment_id", table_name="daily_tasks")
    op.drop_table("daily_tasks")
    op.drop_index("ix_plan_revisions_experiment_id", table_name="plan_revisions")
    op.drop_table("plan_revisions")
    op.drop_table("experiments")
