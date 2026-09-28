from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    ConversationStage,
    SpeakerRole,
)


class ConversationSession(Base):
    """A conversation thread with a caregiver, and where in it we are.

    Persisted because the drift policy is only meaningful over time. Knowing a
    caregiver has been off caring for an hour is what lets SolenOS pick the
    thread back up when they return rather than starting over.
    """

    __tablename__ = "conversation_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True, index=True)
    stage = Column(SQLEnum(ConversationStage), nullable=False, default=ConversationStage.IN_CARE_CONTEXT)
    current_topic = Column(Text, nullable=True)
    last_care_topic = Column(Text, nullable=True)
    last_care_at = Column(DateTime(timezone=True), nullable=True)
    turn_count = Column(Integer, nullable=False, default=0)
    out_of_context_turns = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ConversationTurn(Base):
    """One exchange.

    The caregiver's words are stored exactly as spoken. The reply is stored
    separately and is always attributable to the model, so a caregiver can
    always tell which half of a conversation was theirs.
    """

    __tablename__ = "conversation_turns"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("conversation_sessions.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False, default=0)
    role = Column(SQLEnum(SpeakerRole), nullable=False)
    text = Column(Text, nullable=False)
    stage = Column(SQLEnum(ConversationStage), nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    guard_verdict = Column(String, nullable=True)
    guard_qualifications = Column(JSONList, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)
    safety_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
