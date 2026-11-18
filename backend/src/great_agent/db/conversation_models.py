from __future__ import annotations
from sqlalchemy import ForeignKey, Integer, String, Text, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from great_agent.db.models import Base

class ConversationModel(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), unique=True)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey('experiments.id'), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state_json: Mapped[str] = mapped_column(Text, default='{}')

class ConversationRunModel(Base):
    __tablename__ = 'conversation_runs'
    __table_args__ = (UniqueConstraint('conversation_id', 'request_id', name='uq_conversation_request'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    input_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    lease_until: Mapped[float] = mapped_column(Float)
    owner_pid: Mapped[int] = mapped_column(Integer)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(ForeignKey('plan_proposals.id'), nullable=True)

class ConversationMessageModel(Base):
    __tablename__ = 'conversation_messages'
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('conversation_runs.id'), index=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    blocks_json: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(36))

class ConversationEventModel(Base):
    __tablename__ = 'conversation_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('conversation_runs.id'))
    kind: Mapped[str] = mapped_column(String(24))
    payload_json: Mapped[str] = mapped_column(Text)
