from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    FollowUpStatus,
    FollowUpTrigger,
    EngagementTrigger,
    EngagementUrgency,
)


class FollowUp(Base):
    """Something left open, scheduled to be re-checked at a specific time.

    A follow-up is a re-check rather than a reminder. It exists because the
    record was not good enough to conclude, and the interval reflects how long
    that uncertainty is tolerable for this particular question.
    """

    __tablename__ = "follow_ups"

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
    superseded_by_follow_up_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class EngagementOpportunity(Base):
    """A moment where SolenOS could reach out before the caregiver asks.

    Existence of a row does not mean an outreach should be sent. The
    suppression fields are the point: this is the one place in the product where
    being wrong means interrupting someone at a bad moment, so the default is
    silence and every entry to speak has to earn it.
    """

    __tablename__ = "engagement_opportunities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    trigger = Column(SQLEnum(EngagementTrigger), nullable=False, index=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True, index=True)

    should_reach_out = Column(Boolean, nullable=False, default=False)
    reason_now = Column(Text, nullable=True)
    what_to_say = Column(Text, nullable=True)
    urgency = Column(SQLEnum(EngagementUrgency), nullable=False, default=EngagementUrgency.NOT_NOW)
    why_quiet_is_better = Column(Text, nullable=True)
    channel = Column(String, nullable=True)
    quiet_hours_respected = Column(Boolean, nullable=False, default=True)

    suppressed = Column(Boolean, nullable=False, default=False)
    suppression_reason = Column(Text, nullable=True)

    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    is_acted_on = Column(Boolean, nullable=False, default=False)
    acted_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
