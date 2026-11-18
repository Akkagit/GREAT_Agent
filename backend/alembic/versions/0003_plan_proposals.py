"""Persist plan proposals without replacing historical execution records."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("plan_proposals"):
        required = {
            "id", "experiment_id", "base_revision_id", "source", "candidate_json",
            "candidate_hash", "diff_json", "explanation", "status",
            "source_proposal_id", "accepted_revision_id", "created_at",
        }
        columns = {column["name"] for column in inspector.get_columns("plan_proposals")}
        primary = set(inspector.get_pk_constraint("plan_proposals")["constrained_columns"])
        if not required <= columns or primary != {"id"}:
            raise RuntimeError("Existing plan_proposals schema is incompatible; refusing to overwrite it")
        return
    op.create_table(
        "plan_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("experiment_id", sa.String(36), sa.ForeignKey("experiments.id"), nullable=False),
        sa.Column("base_revision_id", sa.String(36), sa.ForeignKey("plan_revisions.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_proposal_id", sa.String(36), sa.ForeignKey("plan_proposals.id"), nullable=True),
        sa.Column("accepted_revision_id", sa.String(36), sa.ForeignKey("plan_revisions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plan_proposals_experiment_id", "plan_proposals", ["experiment_id"])


def downgrade():
    op.drop_table("plan_proposals")
