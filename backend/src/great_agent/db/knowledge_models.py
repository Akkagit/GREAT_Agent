"""Knowledge source identity, immutable versions and persistent ingestion jobs."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from great_agent.db.models import Base, utc_now


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgeBlobModel(Base):
    __tablename__ = "knowledge_blobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_relpath: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgeDocumentModel(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("base_id", "blob_id", name="uq_knowledge_base_blob"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    blob_id: Mapped[str] = mapped_column(ForeignKey("knowledge_blobs.id"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    active_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_versions.id", use_alter=True, name="fk_knowledge_active_version"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class KnowledgeJobModel(Base):
    __tablename__ = "knowledge_jobs"
    __table_args__ = (UniqueConstraint("document_id", "request_id", name="uq_knowledge_job_request"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False, index=True)
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeVersionModel(Base):
    __tablename__ = "knowledge_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)
    parser_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_document_json: Mapped[str] = mapped_column(Text, nullable=False, default="null", server_default="null")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class KnowledgePageModel(Base):
    __tablename__ = "knowledge_pages"
    __table_args__ = (UniqueConstraint("version_id", "page_index", name="uq_knowledge_version_page"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), nullable=False, index=True)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_label: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[float | None] = mapped_column(nullable=True)
    height: Mapped[float | None] = mapped_column(nullable=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)


class KnowledgeElementModel(Base):
    __tablename__ = "knowledge_elements"
    __table_args__ = (UniqueConstraint("version_id", "source_id", name="uq_knowledge_version_element"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    locations_json: Mapped[str] = mapped_column(Text, nullable=False)
    quality_flags_json: Mapped[str] = mapped_column(Text, nullable=False)
    structure_json: Mapped[str] = mapped_column(Text, nullable=False, default="null", server_default="null")


class KnowledgeChunkModel(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("version_id", "ordinal", name="uq_knowledge_version_chunk"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locations_json: Mapped[str] = mapped_column(Text, nullable=False)
    element_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    quality_flags_json: Mapped[str] = mapped_column(Text, nullable=False)


@event.listens_for(Base.metadata, "before_create")
def _check_knowledge_fts(metadata, connection, **kwargs):
    del metadata, kwargs
    if connection.dialect.name == "sqlite":
        from great_agent.db.knowledge_schema import verify_knowledge_fts
        verify_knowledge_fts(connection, allow_missing=True)


@event.listens_for(Base.metadata, "after_create")
def _create_knowledge_fts(metadata, connection, **kwargs):
    del metadata, kwargs
    if connection.dialect.name == "sqlite" and connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_chunks'").first():
        from great_agent.db.knowledge_schema import create_knowledge_fts
        create_knowledge_fts(connection)
