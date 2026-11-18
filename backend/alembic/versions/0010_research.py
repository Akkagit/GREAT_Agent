"""Add independent research workspaces, evidence and durable worker records."""
from alembic import op
from sqlalchemy.sql.ddl import sort_tables

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    from great_agent.db.models import Base
    from great_agent.db.schema import _verify_reserved_extensions
    from great_agent.research.models import TABLE_NAMES
    bind = op.get_bind()
    _verify_reserved_extensions(bind.engine)
    for table in sort_tables([Base.metadata.tables[name] for name in sorted(TABLE_NAMES)]):
        table.create(bind, checkfirst=True)


def downgrade():
    from great_agent.db.models import Base
    from great_agent.research.models import TABLE_NAMES
    for table in reversed(sort_tables([Base.metadata.tables[name] for name in sorted(TABLE_NAMES)])):
        op.drop_table(table.name)
