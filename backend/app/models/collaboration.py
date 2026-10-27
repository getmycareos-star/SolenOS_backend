"""Persisted community posts/reactions and Companion conversation history.

Community content is deliberately separate from the authoritative Living Care Record.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CommunityPost(Base):
    __tablename__ = "community_posts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    author_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    title = Column(String(180), nullable=True)
    body = Column(Text, nullable=False)
    post_type = Column(String(24), nullable=False, default="experience")
    visibility = Column(String(24), nullable=False, default="public")
    care_stage = Column(String(24), nullable=False, default="unspecified")
    tags = Column(JSON, nullable=False, default=list)
    status = Column(String(24), nullable=False, default="published", index=True)
    flagged_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=True)
    moderated_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=True)
    moderation_reason = Column(Text, nullable=True)
    moderated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class CommunityReaction(Base):
    __tablename__ = "community_reactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id = Column(String, ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(String, ForeignKey("auth_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    reaction_type = Column(String(24), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("post_id", "account_id", name="uq_community_reaction_post_account"),
    )


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    guard_verdict = Column(String(32), nullable=True)
    safety_note = Column(Text, nullable=True)
    evidence_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        Index("ix_conversation_messages_session_created", "session_id", "created_at"),
    )
