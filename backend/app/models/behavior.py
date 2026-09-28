from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    BehaviorCategory,
    HypothesisStrength,
)


class BehaviorObservation(Base):
    """Something this person did, recorded before any interpretation is applied.

    Held separately from the interpretation so the original observation survives
    whatever reading is later attached to it, and so a caregiver can always see
    the behaviour without the system's theory of it.
    """

    __tablename__ = "behavior_observations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    category = Column(SQLEnum(BehaviorCategory), nullable=False, index=True)
    description = Column(Text, nullable=False)
    original_text = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    time_of_day = Column(String, nullable=True)
    location = Column(String, nullable=True)
    observed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    preceding_context = Column(Text, nullable=True)
    response_taken = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    is_safety_concern = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BehaviorInterpretation(Base):
    """Ranked, competing explanations for an observed behaviour.

    Plurality is enforced structurally. An interpretation with fewer than two
    hypotheses is recorded as incomplete rather than presented as an answer,
    because a single cause is almost never the whole of it.
    """

    __tablename__ = "behavior_interpretations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    observation_id = Column(String, ForeignKey("behavior_observations.id"), nullable=False, index=True)

    hypotheses = Column(JSONList, nullable=False)
    hypothesis_count = Column(Integer, nullable=False, default=0)
    is_complete = Column(Boolean, nullable=False, default=False)
    what_would_settle_it = Column(JSONList, nullable=True)
    pattern_match = Column(Text, nullable=True)
    summary = Column(Text, nullable=False)

    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    requires_human_review = Column(Boolean, nullable=False, default=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_interpretation_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BehaviorHypothesis(Base):
    """One candidate explanation, kept as its own row so it can be marked wrong.

    A caregiver who knows this is not what happened must be able to reject a
    specific hypothesis without discarding the rest of the interpretation.
    """

    __tablename__ = "behavior_hypotheses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    interpretation_id = Column(String, ForeignKey("behavior_interpretations.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    rank = Column(Integer, nullable=False, default=0)
    explanation = Column(Text, nullable=False)
    strength = Column(SQLEnum(HypothesisStrength), nullable=False, default=HypothesisStrength.POSSIBLE)
    supporting_evidence_ids = Column(JSONList, nullable=True)
    contradicting_evidence_ids = Column(JSONList, nullable=True)
    why_plausible = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    rejected_reason = Column(Text, nullable=True)
    rejected_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
