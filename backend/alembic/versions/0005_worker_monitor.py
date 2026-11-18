"""Add conservative process recovery metadata and resumable monitor events."""
from alembic import op
import sqlalchemy as sa
from uuid import uuid4

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def columns():
    return [
        sa.Column('owner_session_id', sa.String(36), nullable=True),
        sa.Column('launch_phase', sa.String(24), nullable=False, server_default='legacy_unknown'),
        sa.Column('process_created_at', sa.BigInteger(), nullable=True),
        sa.Column('process_executable', sa.Text(), nullable=True),
        sa.Column('command_sha256', sa.String(64), nullable=True),
        sa.Column('recovery_state', sa.String(24), nullable=False, server_default='none'),
        sa.Column('recovery_reason', sa.String(64), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('analysis_status', sa.String(24), nullable=False, server_default='unknown'),
        sa.Column('artifact_version', sa.Integer(), nullable=False, server_default='0'),
    ]


def upgrade():
    bind = op.get_bind()
    existing = {c['name'] for c in sa.inspect(bind).get_columns('task_attempts')}
    for column in columns():
        if column.name not in existing:
            op.add_column('task_attempts', column)
    from great_agent.db.monitor_models import ExperimentEventModel, MonitorStateModel
    for model in (ExperimentEventModel, MonitorStateModel):
        model.__table__.create(bind, checkfirst=True)
    if bind.execute(sa.select(MonitorStateModel.id).where(MonitorStateModel.id == 1)).first() is None:
        bind.execute(MonitorStateModel.__table__.insert().values(id=1, epoch=str(uuid4()), retention_floor_json='{}'))


def downgrade():
    bind = op.get_bind()
    for name in ('experiment_events', 'monitor_state'):
        if sa.inspect(bind).has_table(name):
            op.drop_table(name)
    # SQLite supports native DROP COLUMN, avoiding table-rebuild surprises in
    # the circular task/attempt foreign key relationship.
    existing = {c['name'] for c in sa.inspect(bind).get_columns('task_attempts')}
    for column in reversed(columns()):
        if column.name in existing:
            op.drop_column('task_attempts', column.name)
