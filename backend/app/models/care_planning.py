from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    DecisionStatus,
    DecisionOwner,
    PlanStatus,
)


class CareDecision(Base):
    """A real decision put to a caregiver, with the options that were available.

    A decision that arrives without its alternatives is not a decision, it is an
    instruction. Doing nothing is recorded as a genuine option because it
    frequently is the right one.
    """

    __tablename__ = "care_decisions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    question = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    urgency = Column(String, nullable=True)
    status = Column(SQLEnum(DecisionStatus), nullable=False, default=DecisionStatus.PROPOSED)
    owner = Column(SQLEnum(DecisionOwner), nullable=False, default=DecisionOwner.CAREGIVER)

    options = Column(JSONList, nullable=True)
    option_count = Column(Integer, nullable=False, default=0)
    recommendation = Column(JSONList, nullable=True)
    deciding_factors = Column(JSONList, nullable=True)

    summary = Column(Text, nullable=False)
    safety_decision = Column(String, nullable=True)
    requires_clinician = Column(Boolean, nullable=False, default=False)
    requires_human_review = Column(Boolean, nullable=False, default=True)

    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    decision_note = Column(Text, nullable=True)
    supersedes_decision_id = Column(String, ForeignKey("care_decisions.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_decision_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CarePlan(Base):
    """A recommendation turned into steps a tired person can follow."""

    __tablename__ = "care_plans"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    decision_id = Column(String, ForeignKey("care_decisions.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(SQLEnum(PlanStatus), nullable=False, default=PlanStatus.ACTIVE)
    steps = Column(JSONList, nullable=True)
    step_count = Column(Integer, nullable=False, default=0)
    review_at = Column(DateTime(timezone=True), nullable=True)
    stop_and_escalate_if = Column(JSONList, nullable=True)
    if_it_is_not_working = Column(Text, nullable=True)
    summary = Column(Text, nullable=False)
    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_plan_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CarePlanStep(Base):
    """One step, with an owner and an observable success signal.

    A step without "how will we know it worked" is a wish, and a plan of wishes
    is not followed at 3am.
    """

    __tablename__ = "care_plan_steps"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plan_id = Column(String, ForeignKey("care_plans.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False, default=0)
    step = Column(Text, nullable=False)
    owner = Column(String, nullable=True)
    when = Column(String, nullable=True)
    how_we_know_it_worked = Column(Text, nullable=True)
    is_completed = Column(Boolean, nullable=False, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
