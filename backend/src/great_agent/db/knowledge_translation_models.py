"""Disposable translations derived from one immutable citation excerpt."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from great_agent.db.models import Base, utc_now


class KnowledgeCitationTranslationModel(Base):
    __tablename__ = 'knowledge_citation_translations'

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    citation_id: Mapped[str] = mapped_column(String(36), ForeignKey('knowledge_message_citations.id'), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(36), ForeignKey('knowledge_versions.id'), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    target_language: Mapped[str] = mapped_column(String(16), nullable=False)
    model_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    translation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
