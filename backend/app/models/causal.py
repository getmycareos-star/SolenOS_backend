from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Enum as SQLEnum, Integer
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.causal_enums import (
    EvidenceStrength,
    CausalConfidence,
    CausalRelationshipStatus,
    CausalDirection,
    EvidenceRole,
    EvidenceType,
)


class CausalHypothesis(Base):
    __tablename__ = "causal_hypotheses"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    potential_cause_type = Column(String, nullable=False)
    potential_cause_id = Column(String, nullable=True)
    potential_cause_description = Column(Text, nullable=False)

    outcome_type = Column(String, nullable=False)
    outcome_id = Column(String, nullable=True)
    outcome_description = Column(Text, nullable=False)

    causal_direction = Column(
        SQLEnum(CausalDirection), nullable=False, default=CausalDirection.UNKNOWN
    )
    relationship_status = Column(
        SQLEnum(CausalRelationshipStatus),
        nullable=False,
        default=CausalRelationshipStatus.OBSERVED_ASSOCIATION,
    )
    evidence_strength = Column(
        SQLEnum(EvidenceStrength), nullable=False, default=EvidenceStrength.WEAK
    )
    causal_confidence = Column(
        SQLEnum(CausalConfidence), nullable=False, default=CausalConfidence.INSUFFICIENT
    )

    temporal_relationship = Column(Text, nullable=True)
    time_gap_seconds = Column(Float, nullable=True)
    is_repeatable = Column(Boolean, nullable=False, default=False)
    repeat_count = Column(Integer, nullable=True)
    repeat_pattern = Column(Text, nullable=True)

    supporting_evidence_ids = Column(JSONList, nullable=True)
    contradictory_evidence_ids = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)

    first_observed_date = Column(DateTime(timezone=True), nullable=True)
    last_evaluated_date = Column(DateTime(timezone=True), nullable=True)
    provenance = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    superseded_by_hypothesis_id = Column(
        String, ForeignKey("causal_hypotheses.id"), nullable=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    updated_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)


class CausalEvidence(Base):
    __tablename__ = "causal_evidence"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    hypothesis_id = Column(
        String, ForeignKey("causal_hypotheses.id"), nullable=False, index=True
    )
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    role = Column(SQLEnum(EvidenceRole), nullable=False, default=EvidenceRole.SUPPORTING)
    evidence_type = Column(SQLEnum(EvidenceType), nullable=False)
    strength = Column(SQLEnum(EvidenceStrength), nullable=False, default=EvidenceStrength.WEAK)

    description = Column(Text, nullable=False)
    source_document = Column(Text, nullable=True)
    source_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)
    evidence_ref_id = Column(Text, nullable=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=True)

    is_derived = Column(Boolean, nullable=False, default=False)
    derivation_method = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class AlternativeExplanation(Base):
    __tablename__ = "alternative_explanations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    hypothesis_id = Column(
        String, ForeignKey("causal_hypotheses.id"), nullable=False, index=True
    )
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    explanation_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    plausibility = Column(SQLEnum(CausalConfidence), nullable=False, default=CausalConfidence.POSSIBLE)
    evidence_ids = Column(JSONList, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)


class Confounder(Base):
    __tablename__ = "causal_confounders"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    hypothesis_id = Column(
        String, ForeignKey("causal_hypotheses.id"), nullable=False, index=True
    )
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    factor_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    plausibility = Column(SQLEnum(CausalConfidence), nullable=False, default=CausalConfidence.POSSIBLE)
    evidence_ids = Column(JSONList, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
