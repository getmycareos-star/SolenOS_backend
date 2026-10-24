from sqlalchemy import Column, String, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class CommunityPost(Base):
    __tablename__ = "community_posts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    author_type = Column(String, nullable=False, default="peer_caregiver")
    author_label = Column(String, nullable=False, default="Anonymous caregiver")
    post_type = Column(String, nullable=False, default="experience")
    content = Column(Text, nullable=False)
    care_stage = Column(String, nullable=False, default="unspecified")
    tags_json = Column(Text, nullable=False, default="[]")
    visibility = Column(String, nullable=False, default="public", index=True)
    moderation_status = Column(String, nullable=False, default="published", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CommunityReaction(Base):
    __tablename__ = "community_reactions"
    __table_args__ = (UniqueConstraint("post_id", "caregiver_id", name="uq_community_reaction_actor_post"),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    post_id = Column(String, ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    reaction_type = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    title = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    guard_verdict = Column(String, nullable=True)
    safety_note = Column(Text, nullable=True)
    evidence_ids_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
