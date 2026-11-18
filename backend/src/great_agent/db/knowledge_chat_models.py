"""Knowledge bindings, immutable per-turn evidence and rebuildable index metadata."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from great_agent.db.models import Base, utc_now


class KnowledgeConversationBindingModel(Base):
    __tablename__ = 'knowledge_conversation_bindings'
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), primary_key=True)
    base_id: Mapped[str] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    scope_mode: Mapped[str] = mapped_column(String(24))
    document_ids_json: Mapped[str] = mapped_column(Text)
    model_profile_id: Mapped[str] = mapped_column(String(100), default='default')
    binding_revision: Mapped[int] = mapped_column(Integer, default=1)


class KnowledgeRetrievalTraceModel(Base):
    __tablename__ = 'knowledge_retrieval_traces'
    run_id: Mapped[str] = mapped_column(ForeignKey('conversation_runs.id'), primary_key=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    queries_json: Mapped[str] = mapped_column(Text, default='[]')
    candidates_json: Mapped[str] = mapped_column(Text, default='[]')
    evidence_json: Mapped[str] = mapped_column(Text, default='[]')
    outbound_messages_json: Mapped[str] = mapped_column(Text, default='[]')
    budget_json: Mapped[str] = mapped_column(Text, default='{}')
    timings_json: Mapped[str] = mapped_column(Text, default='{}')
    mode: Mapped[str] = mapped_column(String(32), default='lexical')
    stage: Mapped[str] = mapped_column(String(32), default='started')


class KnowledgeMessageCitationModel(Base):
    __tablename__ = 'knowledge_message_citations'
    __table_args__ = (UniqueConstraint('message_id', 'source_id', name='uq_knowledge_message_source'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey('conversation_messages.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('conversation_runs.id'), index=True)
    source_id: Mapped[str] = mapped_column(String(32))
    document_id: Mapped[str] = mapped_column(ForeignKey('knowledge_documents.id'))
    version_id: Mapped[str] = mapped_column(ForeignKey('knowledge_versions.id'))
    chunk_id: Mapped[str] = mapped_column(ForeignKey('knowledge_chunks.id'))
    source_sha256: Mapped[str] = mapped_column(String(64))
    evidence_text: Mapped[str] = mapped_column(Text)
    evidence_text_sha256: Mapped[str] = mapped_column(String(64))
    source_spans_json: Mapped[str] = mapped_column(Text)
    locations_json: Mapped[str] = mapped_column(Text)
    quality_flags_json: Mapped[str] = mapped_column(Text, default='[]')
    title: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)


class KnowledgeIndexGenerationModel(Base):
    __tablename__ = 'knowledge_index_generations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey('knowledge_versions.id'), index=True)
    embedding_fingerprint: Mapped[str] = mapped_column(Text, default='')
    tokenizer_fingerprint: Mapped[str] = mapped_column(Text, default='')
    config_fingerprint: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(32), default='staging')
    dimensions: Mapped[int] = mapped_column(Integer, default=0)
    point_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeIndexSegmentModel(Base):
    __tablename__ = 'knowledge_index_segments'
    __table_args__ = (UniqueConstraint('generation_id', 'parent_chunk_id', 'ordinal', name='uq_knowledge_generation_segment'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey('knowledge_index_generations.id'), index=True)
    parent_chunk_id: Mapped[str] = mapped_column(ForeignKey('knowledge_chunks.id'), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    source_spans_json: Mapped[str] = mapped_column(Text)
    embedding_text_hash: Mapped[str] = mapped_column(String(64))
    embedding_text: Mapped[str] = mapped_column(Text, default='')
