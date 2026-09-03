from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Enum as SQLEnum, Integer
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.change_enums import (
    ChangeType,
    ChangeDirection,
    PatternType,
    PatternStrength,
    SituationState,
    SituationType,
    BaselineType,
    BaselineConfidence,
    SignificanceVerdict,
)


class Baseline(Base):
    __tablename__ = "baselines"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    baseline_type = Column(SQLEnum(BaselineType), nullable=False, default=BaselineType.PERSONAL_HISTORICAL)
    window_start = Column(DateTime(timezone=True), nullable=False)
    window_end = Column(DateTime(timezone=True), nullable=False)
    event_count = Column(Integer, nullable=False, default=0)
    frequency_per_period = Column(Float, nullable=True)
    state_value = Column(Text, nullable=True)
    confidence = Column(SQLEnum(BaselineConfidence), nullable=False, default=BaselineConfidence.MODERATE)
    is_stable = Column(Boolean, nullable=False, default=True)
    observation_density = Column(Float, nullable=True)
    documentation_bias_flag = Column(Boolean, nullable=False, default=False)
    evidence_ids = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    superseded_by_baseline_id = Column(String, ForeignKey("baselines.id"), nullable=True)
    is_active = Column(Boolean, default=True)


class Change(Base):
    __tablename__ = "changes"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    change_type = Column(SQLEnum(ChangeType), nullable=False)
    change_direction = Column(SQLEnum(ChangeDirection), nullable=False, default=ChangeDirection.UNKNOWN)
    previous_state = Column(Text, nullable=True)
    current_state = Column(Text, nullable=True)
    magnitude = Column(Float, nullable=True)
    previous_count = Column(Integer, nullable=True)
    current_count = Column(Integer, nullable=True)
    baseline_id = Column(String, ForeignKey("baselines.id"), nullable=True)
    comparison_window_start = Column(DateTime(timezone=True), nullable=True)
    comparison_window_end = Column(DateTime(timezone=True), nullable=True)
    persistence_days = Column(Integer, nullable=True)
    is_persistent = Column(Boolean, nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    evidence_ids = Column(JSONList, nullable=False)
    explanation = Column(Text, nullable=True)
    time_provenance = Column(String, nullable=True)
    significance_assessment_id = Column(String, ForeignKey("significance_assessments.id"), nullable=True, index=True)
    significance_verdict = Column(SQLEnum(SignificanceVerdict), nullable=True)
    attention_candidate = Column(Boolean, nullable=True, default=False)
    follow_up_candidate = Column(Boolean, nullable=True, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    superseded_by_change_id = Column(String, ForeignKey("changes.id"), nullable=True)
    is_active = Column(Boolean, default=True)


class Pattern(Base):
    __tablename__ = "patterns"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    pattern_type = Column(SQLEnum(PatternType), nullable=False)
    pattern_strength = Column(SQLEnum(PatternStrength), nullable=False, default=PatternStrength.EMERGING)
    event_ids = Column(JSONList, nullable=False)
    change_ids = Column(JSONList, nullable=True)
    temporal_start = Column(DateTime(timezone=True), nullable=False)
    temporal_end = Column(DateTime(timezone=True), nullable=False)
    frequency = Column(Float, nullable=True)
    rate_per_period = Column(Float, nullable=True)
    direction = Column(SQLEnum(ChangeDirection), nullable=True)
    confidence = Column(Float, nullable=False, default=1.0)
    evidence_count = Column(Integer, nullable=False, default=0)
    alternative_interpretations = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    superseded_by_pattern_id = Column(String, ForeignKey("patterns.id"), nullable=True)
    is_active = Column(Boolean, default=True)


class Situation(Base):
    __tablename__ = "situations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    situation_type = Column(SQLEnum(SituationType), nullable=False, default=SituationType.UNKNOWN)
    state = Column(SQLEnum(SituationState), nullable=False, default=SituationState.ACTIVE)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    signal_ids = Column(JSONList, nullable=False)
    signal_types = Column(JSONList, nullable=False)
    temporal_start = Column(DateTime(timezone=True), nullable=False)
    temporal_end = Column(DateTime(timezone=True), nullable=False)
    pattern_ids = Column(JSONList, nullable=True)
    change_ids = Column(JSONList, nullable=True)
    significance_assessment_id = Column(String, ForeignKey("significance_assessments.id"), nullable=True, index=True)
    confidence = Column(Float, nullable=False, default=1.0)
    alternative_interpretations = Column(JSONList, nullable=True)
    context = Column(JSONList, nullable=True)
    attention_candidate = Column(Boolean, nullable=True, default=False)
    follow_up_candidate = Column(Boolean, nullable=True, default=False)
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    superseded_by_situation_id = Column(String, ForeignKey("situations.id"), nullable=True)
    is_active = Column(Boolean, default=True)
