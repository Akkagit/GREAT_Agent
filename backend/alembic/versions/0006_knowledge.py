"""Versioned local knowledge catalogue and transactional lexical index."""
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None
ORDER = ('knowledge_bases','knowledge_blobs','knowledge_documents','knowledge_jobs',
         'knowledge_versions','knowledge_pages','knowledge_elements','knowledge_chunks')


def upgrade():
    from great_agent.db.models import Base
    from great_agent.db.knowledge_schema import TABLE_NAMES, create_knowledge_fts
    bind = op.get_bind()
    # Explicitly ordered from metadata to respect blob/base/version references.
    for name in ORDER:
        Base.metadata.tables[name].create(bind, checkfirst=True)
    create_knowledge_fts(bind)


def downgrade():
    from great_agent.db.models import Base
    from great_agent.db.knowledge_schema import TABLE_NAMES
    bind = op.get_bind()
    bind.exec_driver_sql('DROP TABLE IF EXISTS knowledge_chunks_fts')
    bind.exec_driver_sql('UPDATE knowledge_documents SET active_version_id=NULL')
    for name in reversed(ORDER):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
