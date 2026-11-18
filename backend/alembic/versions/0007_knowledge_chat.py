"""Knowledge-bound conversations, immutable evidence and index generations."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # A verified create_all bootstrap may already contain these tables. Validate
    # reserved shapes before accepting them; do not silently adopt collisions.
    from great_agent.db.schema import _verify_reserved_extensions
    _verify_reserved_extensions(bind.engine)
    present = set(sa.inspect(bind).get_table_names())
    if 'knowledge_conversation_bindings' not in present:
        op.create_table('knowledge_conversation_bindings',
            sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id'), primary_key=True),
            sa.Column('base_id', sa.String(36), sa.ForeignKey('knowledge_bases.id'), nullable=False),
            sa.Column('scope_mode', sa.String(24), nullable=False),
            sa.Column('document_ids_json', sa.Text(), nullable=False),
            sa.Column('model_profile_id', sa.String(100), nullable=False),
            sa.Column('binding_revision', sa.Integer(), nullable=False))
        op.create_index('ix_knowledge_conversation_bindings_base_id', 'knowledge_conversation_bindings', ['base_id'])
    if 'knowledge_retrieval_traces' not in present:
        op.create_table('knowledge_retrieval_traces',
            sa.Column('run_id', sa.String(36), sa.ForeignKey('conversation_runs.id'), primary_key=True),
            sa.Column('snapshot_json', sa.Text(), nullable=False),
            sa.Column('queries_json', sa.Text(), nullable=False),
            sa.Column('candidates_json', sa.Text(), nullable=False),
            sa.Column('evidence_json', sa.Text(), nullable=False),
            sa.Column('outbound_messages_json', sa.Text(), nullable=False),
            sa.Column('budget_json', sa.Text(), nullable=False),
            sa.Column('timings_json', sa.Text(), nullable=False),
            sa.Column('mode', sa.String(32), nullable=False),
            sa.Column('stage', sa.String(32), nullable=False))
    if 'knowledge_message_citations' not in present:
        op.create_table('knowledge_message_citations',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('message_id', sa.String(36), sa.ForeignKey('conversation_messages.id'), nullable=False),
            sa.Column('run_id', sa.String(36), sa.ForeignKey('conversation_runs.id'), nullable=False),
            sa.Column('source_id', sa.String(32), nullable=False),
            sa.Column('document_id', sa.String(36), sa.ForeignKey('knowledge_documents.id'), nullable=False),
            sa.Column('version_id', sa.String(36), sa.ForeignKey('knowledge_versions.id'), nullable=False),
            sa.Column('chunk_id', sa.String(36), sa.ForeignKey('knowledge_chunks.id'), nullable=False),
            sa.Column('source_sha256', sa.String(64), nullable=False),
            sa.Column('evidence_text', sa.Text(), nullable=False),
            sa.Column('evidence_text_sha256', sa.String(64), nullable=False),
            sa.Column('source_spans_json', sa.Text(), nullable=False),
            sa.Column('locations_json', sa.Text(), nullable=False),
            sa.Column('quality_flags_json', sa.Text(), nullable=False),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('filename', sa.Text(), nullable=False),
            sa.UniqueConstraint('message_id', 'source_id', name='uq_knowledge_message_source'))
        op.create_index('ix_knowledge_message_citations_message_id', 'knowledge_message_citations', ['message_id'])
        op.create_index('ix_knowledge_message_citations_run_id', 'knowledge_message_citations', ['run_id'])
    if 'knowledge_index_generations' not in present:
        op.create_table('knowledge_index_generations',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('version_id', sa.String(36), sa.ForeignKey('knowledge_versions.id'), nullable=False),
            sa.Column('embedding_fingerprint', sa.Text(), nullable=False),
            sa.Column('tokenizer_fingerprint', sa.Text(), nullable=False),
            sa.Column('config_fingerprint', sa.Text(), nullable=False),
            sa.Column('status', sa.String(32), nullable=False),
            sa.Column('dimensions', sa.Integer(), nullable=False),
            sa.Column('point_count', sa.Integer(), nullable=False),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ix_knowledge_index_generations_version_id', 'knowledge_index_generations', ['version_id'])
    if 'knowledge_index_segments' not in present:
        op.create_table('knowledge_index_segments',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('generation_id', sa.String(36), sa.ForeignKey('knowledge_index_generations.id'), nullable=False),
            sa.Column('parent_chunk_id', sa.String(36), sa.ForeignKey('knowledge_chunks.id'), nullable=False),
            sa.Column('ordinal', sa.Integer(), nullable=False),
            sa.Column('source_spans_json', sa.Text(), nullable=False),
            sa.Column('embedding_text_hash', sa.String(64), nullable=False),
            sa.Column('embedding_text', sa.Text(), nullable=False),
            sa.UniqueConstraint('generation_id', 'parent_chunk_id', 'ordinal', name='uq_knowledge_generation_segment'))
        op.create_index('ix_knowledge_index_segments_generation_id', 'knowledge_index_segments', ['generation_id'])
        op.create_index('ix_knowledge_index_segments_parent_chunk_id', 'knowledge_index_segments', ['parent_chunk_id'])


def downgrade():
    for name in ('knowledge_index_segments', 'knowledge_index_generations', 'knowledge_message_citations',
                 'knowledge_retrieval_traces', 'knowledge_conversation_bindings'):
        op.drop_table(name)
