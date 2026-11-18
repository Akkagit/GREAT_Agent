"""Independent research records; JSON snapshots are immutable historical versions."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from great_agent.db.models import Base, utc_now


class ResearchSessionModel(Base):
    __tablename__ = 'research_sessions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    material_revision: Mapped[int] = mapped_column(Integer, default=1)
    settings_revision: Mapped[int] = mapped_column(Integer, default=1)
    state_revision: Mapped[int] = mapped_column(Integer, default=0)
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchMaterialRevisionModel(Base):
    __tablename__ = 'research_material_revisions'
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    selection_json: Mapped[str] = mapped_column(Text)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchSettingsRevisionModel(Base):
    __tablename__ = 'research_settings_revisions'
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchRunModel(Base):
    __tablename__ = 'research_runs'
    __table_args__ = (UniqueConstraint('session_id', 'request_id', name='uq_research_request'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    followup_json: Mapped[str] = mapped_column(Text)
    material_revision: Mapped[int] = mapped_column(Integer)
    settings_revision: Mapped[int] = mapped_column(Integer)
    base_state_revision: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text)
    settings_json: Mapped[str] = mapped_column(Text)
    budget_json: Mapped[str] = mapped_column(Text)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    retry_of: Mapped[str | None] = mapped_column(ForeignKey('research_runs.id'), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchMessageModel(Base):
    __tablename__ = 'research_messages'
    __table_args__ = (UniqueConstraint('session_id', 'sequence', name='uq_research_message_sequence'),
                      UniqueConstraint('run_id', 'role', name='uq_research_run_message_role'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('research_runs.id'), index=True)
    role: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    blocks_json: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)


class ResearchEvidenceModel(Base):
    __tablename__ = 'research_evidence'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchRunEvidenceModel(Base):
    __tablename__ = 'research_run_evidence'
    __table_args__ = (UniqueConstraint('run_id', 'evidence_id', name='uq_research_run_evidence_id'),)
    run_id: Mapped[str] = mapped_column(ForeignKey('research_runs.id'), primary_key=True)
    alias: Mapped[str] = mapped_column(String(32), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey('research_evidence.id'))


class ResearchStateRevisionModel(Base):
    __tablename__ = 'research_state_revisions'
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('research_runs.id'), nullable=True)
    items_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchEventModel(Base):
    __tablename__ = 'research_events'
    __table_args__ = {'sqlite_autoincrement': True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('research_runs.id'), nullable=True)
    type: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchCheckpointModel(Base):
    __tablename__ = 'research_checkpoints'
    run_id: Mapped[str] = mapped_column(ForeignKey('research_runs.id'), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchModelCallModel(Base):
    __tablename__ = 'research_model_calls'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('research_runs.id'), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    model_fingerprint: Mapped[str] = mapped_column(String(128))
    request_json: Mapped[str] = mapped_column(Text)
    evidence_ids_json: Mapped[str] = mapped_column(Text)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ResearchReportModel(Base):
    __tablename__ = 'research_reports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('research_sessions.id'), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


TABLE_NAMES = frozenset(table.name for table in Base.metadata.tables.values()
                        if table.name.startswith('research_'))
