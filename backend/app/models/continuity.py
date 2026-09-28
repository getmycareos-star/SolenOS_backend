from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    ContinuityDirection,
    HandoffReason,
)


class ContinuityThread(Base):
    """One subject followed over time: sleep, medication, mobility, mood.

    A thread is the unit that survives a change of caregiver. It is what makes
    "this is not new, it has been happening since March" answerable.
    """

    __tablename__ = "continuity_threads"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject = Column(String, nullable=False)
    label = Column(String, nullable=False)
    first_recorded_at = Column(DateTime(timezone=True), nullable=True)
    last_recorded_at = Column(DateTime(timezone=True), nullable=True, index=True)
    observation_count = Column(Integer, nullable=False, default=0)
    direction = Column(SQLEnum(ContinuityDirection), nullable=False, default=ContinuityDirection.INSUFFICIENT_DATA)
    is_stale = Column(Boolean, default=False)
    stale_since = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_thread_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ContinuitySnapshot(Base):
    """What was known, what is known, and what will matter next.

    The three-part shape is the point. A snapshot that reported only the present
    would lose the thing that matters most at a handoff, which is how we got here
    and how long this has been going on.
    """

    __tablename__ = "continuity_snapshots"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject = Column(String, nullable=True)
    thread_id = Column(String, ForeignKey("continuity_threads.id"), nullable=True, index=True)

    previously_known = Column(JSONList, nullable=True)
    currently_known = Column(JSONList, nullable=True)
    what_will_matter_next = Column(JSONList, nullable=True)
    gaps = Column(JSONList, nullable=True)

    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    care_giver_changes = Column(Integer, nullable=True)
    setting_changes = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_snapshot_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ContinuityHandoff(Base):
    """A brief written for a caregiver or setting taking over."""

    __tablename__ = "continuity_handoffs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    reason = Column(SQLEnum(HandoffReason), nullable=False, default=HandoffReason.CAREGIVER_CHANGE)
    from_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    to_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    setting_change_note = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)

    whats_changed = Column(JSONList, nullable=True)
    person_in_brief = Column(Text, nullable=True)
    what_goes_wrong = Column(JSONList, nullable=True)
    open_threads = Column(JSONList, nullable=True)
    first_24_hours = Column(JSONList, nullable=True)

    covering_since = Column(DateTime(timezone=True), nullable=True)
    gap_days = Column(Integer, nullable=True)

    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    is_acknowledged = Column(Boolean, nullable=False, default=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_handoff_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
