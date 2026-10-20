from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean, Enum as SQLEnum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    FollowUpStatus,
    FollowUpTrigger,
)


class OpenLoop(Base):
    """Something left open, scheduled to be re-checked at a specific time.
    
    An open loop is a re-check rather than a reminder. It exists because the
    record was not good enough to conclude, and the interval reflects how long
    that uncertainty is tolerable for this particular question.
    """

    __tablename__ = "open_loops"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True, index=True)
    field = Column(String, nullable=True)

    what_to_recheck = Column(Text, nullable=False)
    why_open = Column(Text, nullable=True)
    what_result_changes_the_plan = Column(Text, nullable=True)
    trigger = Column(SQLEnum(FollowUpTrigger), nullable=False, default=FollowUpTrigger.UNCERTAINTY)
    status = Column(SQLEnum(FollowUpStatus), nullable=False, default=FollowUpStatus.SCHEDULED)

    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    interval_days = Column(Integer, nullable=True)
    cadence = Column(String, nullable=True)
    priority_hint = Column(String, nullable=True)

    evidence_ids = Column(JSONList, nullable=True)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    completed_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(Text, nullable=True)
    completed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_open_loop_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
