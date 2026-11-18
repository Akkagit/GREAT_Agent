"""Add execution previews and per-attempt approval; preserve all historical rows."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # Startup's create_all may already have added these tables. Validate rather
    # than replacing them, so a later operator-driven upgrade preserves approvals.
    def exists(name, columns, primary_key):
        if not inspector.has_table(name):
            return False
        if not columns <= {c['name'] for c in inspector.get_columns(name)} or set(inspector.get_pk_constraint(name)['constrained_columns']) != primary_key:
            raise RuntimeError(f'Existing {name} schema is incompatible; refusing to overwrite it')
        return True

    if not exists("execution_previews", {"id", "experiment_id", "revision_id", "payload_json", "confirmed_at"}, {"id"}):
        op.create_table("execution_previews",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("experiment_id", sa.String(36), sa.ForeignKey("experiments.id"), nullable=False),
            sa.Column("revision_id", sa.String(36), sa.ForeignKey("plan_revisions.id"), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_execution_previews_experiment_id", "execution_previews", ["experiment_id"])
    if not exists("execution_approvals", {"task_id", "attempt_number", "preview_id", "job_index"}, {"task_id", "attempt_number"}):
        op.create_table("execution_approvals",
            sa.Column("task_id", sa.String(36), sa.ForeignKey("daily_tasks.id"), primary_key=True),
            sa.Column("attempt_number", sa.Integer(), primary_key=True),
            sa.Column("preview_id", sa.String(36), sa.ForeignKey("execution_previews.id"), nullable=False),
            sa.Column("job_index", sa.Integer(), nullable=False),
        )
        op.create_index("ix_execution_approvals_preview_id", "execution_approvals", ["preview_id"])


def downgrade():
    op.drop_table("execution_approvals")
    op.drop_table("execution_previews")
