from sqlalchemy import Column, String, DateTime, Text, ForeignKey, UniqueConstraint, LargeBinary, Integer, Boolean
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


class EvidenceUpload(Base):
    __tablename__ = "evidence_uploads"
    evidence_id = Column(String, ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True)
    filename = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    content = Column(LargeBinary, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ShareIntake(Base):
    __tablename__ = "share_intakes"
    id = Column(String, primary_key=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class ConsentRecord(Base):
    __tablename__ = "caregiver_consents"
    __table_args__ = (UniqueConstraint("person_id", "caregiver_id", name="uq_caregiver_consent_person_actor"),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    medical_disclaimer_acknowledged = Column(Boolean, nullable=False, default=False)
    privacy_model_acknowledged = Column(Boolean, nullable=False, default=False)
    multi_caregiver_acknowledged = Column(Boolean, nullable=False, default=False)
    no_advertising_acknowledged = Column(Boolean, nullable=False, default=False)
    data_improvement_consent = Column(Boolean, nullable=False, default=False)
    accepted_terms_version = Column(String, nullable=False, default="2026-10")
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
