"""Durable postprocessing queue and local worker heartbeat."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    from great_agent.db.analysis_models import AnalysisJobModel, WorkerHeartbeatModel
    from great_agent.db.schema import _verify_reserved_extensions
    bind = op.get_bind()
    _verify_reserved_extensions(bind.engine)
    for table in (AnalysisJobModel.__table__, WorkerHeartbeatModel.__table__):
        table.create(bind, checkfirst=True)


def downgrade():
    op.drop_table('worker_heartbeats')
    op.drop_table('analysis_jobs')
