from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Boolean, Enum as SQLEnum, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.models.action import Action
from app.models.care_event import CareEvent
from app.models.evidence import Evidence
from app.models.follow_up import FollowUp
from app.models.care_planning import CareDecision
from app.models.care_recommendations import CareRecommendation
from app.models.medication import Medication
from app.models.time_location import Appointment
from app.models.open_loop import OpenLoop
from app.models.professional_response import ProfessionalResponse
from app.core.outcome_enums import OutcomeStatus, ExpectationType, ConsequenceType, ProvenanceType

class Outcome(Base):
    __tablename__ = "outcomes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Related entities
    decision_id = Column(String, ForeignKey("care_decisions.id"), nullable=True, index=True)
    action_id = Column(String, ForeignKey("actions.id"), nullable=True, index=True)
    recommendation_id = Column(String, ForeignKey("care_recommendations.id"), nullable=True, index=True)
    appointment_id = Column(String, ForeignKey("appointments.id"), nullable=True, index=True)
    medication_change_id = Column(String, ForeignKey("medications.id"), nullable=True, index=True)
    open_loop_id = Column(String, ForeignKey("open_loops.id"), nullable=True, index=True)
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True, index=True)

    # Expectation
    expectation_description = Column(Text, nullable=False)
    expectation_source = Column(String, nullable=False)
    expectation_type = Column(SQLEnum(ExpectationType), nullable=False)
    expected_timeframe = Column(String, nullable=True)
    expected_by_date = Column(DateTime(timezone=True), nullable=True)
    expectation_explicit = Column(Boolean, nullable=False, default=False)

    # Actual outcome
    actual_outcome_description = Column(Text, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    outcome_source = Column(String, nullable=True)
    reported_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    outcome_evidence_ids = Column(JSONList, nullable=True)
    outcome_status = Column(SQLEnum(OutcomeStatus), nullable=False, default=OutcomeStatus.UNKNOWN)

    # Comparison
    comparison_result = Column(SQLEnum(OutcomeStatus), nullable=True)

    # Consequence
    consequence_type = Column(SQLEnum(ConsequenceType), nullable=True)
    consequence_description = Column(Text, nullable=True)
    new_question_id = Column(String, ForeignKey("follow_ups.id"), nullable=True, index=True)
    new_decision_id = Column(String, ForeignKey("care_decisions.id"), nullable=True, index=True)
    new_action_id = Column(String, ForeignKey("actions.id"), nullable=True, index=True)
    professional_input_sought = Column(Boolean, nullable=False, default=False)
    open_loop_created = Column(Boolean, nullable=False, default=False)
    open_loop_updated = Column(Boolean, nullable=False, default=False)
    decision_reconsideration_triggered = Column(Boolean, nullable=False, default=False)

    # Provenance
    provenance_type = Column(SQLEnum(ProvenanceType), nullable=False)
    provenance_evidence_ids = Column(JSONList, nullable=False)
    author_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    recorded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    confidence = Column(Float, nullable=True)
    uncertainty = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    decision = relationship("CareDecision", foreign_keys=[decision_id])
    action = relationship("Action", foreign_keys=[action_id])
    recommendation = relationship("CareRecommendation", foreign_keys=[recommendation_id])
    appointment = relationship("Appointment", foreign_keys=[appointment_id])
    medication_change = relationship("Medication", foreign_keys=[medication_change_id])
    open_loop = relationship("OpenLoop", foreign_keys=[open_loop_id])
    evidence = relationship("Evidence", foreign_keys=[evidence_id])
    reported_by_caregiver = relationship("Caregiver", foreign_keys=[reported_by_caregiver_id])
    author_caregiver_rel = relationship("Caregiver", foreign_keys=[author_caregiver_id])
    recorded_by_caregiver_rel = relationship("Caregiver", foreign_keys=[recorded_by_caregiver_id])

class OutcomeVersion(Base):
    __tablename__ = "outcome_versions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    outcome_id = Column(String, ForeignKey("outcomes.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)

    person_id = Column(String, nullable=False)
    decision_id = Column(String, nullable=True)
    action_id = Column(String, nullable=True)
    recommendation_id = Column(String, nullable=True)
    appointment_id = Column(String, nullable=True)
    medication_change_id = Column(String, nullable=True)
    open_loop_id = Column(String, nullable=True)
    evidence_id = Column(String, nullable=True)

    expectation_description = Column(Text, nullable=False)
    expectation_source = Column(String, nullable=False)
    expectation_type = Column(SQLEnum(ExpectationType), nullable=False)
    expected_timeframe = Column(String, nullable=True)
    expected_by_date = Column(DateTime(timezone=True), nullable=True)
    expectation_explicit = Column(Boolean, nullable=False)

    actual_outcome_description = Column(Text, nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    outcome_source = Column(String, nullable=True)
    reported_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    outcome_evidence_ids = Column(JSONList, nullable=True)
    outcome_status = Column(SQLEnum(OutcomeStatus), nullable=False)

    comparison_result = Column(SQLEnum(OutcomeStatus), nullable=True)

    consequence_type = Column(SQLEnum(ConsequenceType), nullable=True)
    consequence_description = Column(Text, nullable=True)
    new_question_id = Column(String, nullable=True)
    new_decision_id = Column(String, nullable=True)
    new_action_id = Column(String, nullable=True)
    professional_input_sought = Column(Boolean, nullable=False)
    open_loop_created = Column(Boolean, nullable=False)
    open_loop_updated = Column(Boolean, nullable=False)
    decision_reconsideration_triggered = Column(Boolean, nullable=False)

    provenance_type = Column(SQLEnum(ProvenanceType), nullable=False)
    provenance_evidence_ids = Column(JSONList, nullable=False)
    author_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    recorded_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    confidence = Column(Float, nullable=True)
    uncertainty = Column(String, nullable=True)

    change_type = Column(String, nullable=False)
    change_reason = Column(Text, nullable=True)
    changed_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    changed_by_system = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("outcome_id", "version", name="uq_outcome_version"),
        Index("ix_outcome_version_outcome", "outcome_id", "version"),
    )
