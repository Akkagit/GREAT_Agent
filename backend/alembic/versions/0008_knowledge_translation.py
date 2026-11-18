"""Disposable citation excerpt translations; original evidence stays immutable."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    from great_agent.db.schema import _verify_reserved_extensions
    _verify_reserved_extensions(bind.engine)
    if 'knowledge_citation_translations' in sa.inspect(bind).get_table_names():
        return
    op.create_table('knowledge_citation_translations',
        sa.Column('cache_key', sa.String(64), primary_key=True),
        sa.Column('citation_id', sa.String(36), sa.ForeignKey('knowledge_message_citations.id'), nullable=False),
        sa.Column('version_id', sa.String(36), sa.ForeignKey('knowledge_versions.id'), nullable=False),
        sa.Column('source_sha256', sa.String(64), nullable=False),
        sa.Column('evidence_text_sha256', sa.String(64), nullable=False),
        sa.Column('target_language', sa.String(16), nullable=False),
        sa.Column('model_fingerprint', sa.String(64), nullable=False),
        sa.Column('translation_version', sa.String(64), nullable=False),
        sa.Column('translated_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_knowledge_citation_translations_citation_id', 'knowledge_citation_translations', ['citation_id'])


def downgrade():
    op.drop_table('knowledge_citation_translations')
