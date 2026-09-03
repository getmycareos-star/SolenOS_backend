from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Enum as SQLEnum, Integer
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.change_enums import SignificanceVerdict, EvidenceStatus


class SignificanceAssessment(Base):
    __tablename__ = "significance_assessments"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    target_type = Column(String, nullable=False, default="change")
    target_id = Column(String, nullable=True, index=True)
    baseline_id = Column(String, ForeignKey("baselines.id"), nullable=True)
    verdict = Column(SQLEnum(SignificanceVerdict), nullable=False, default=SignificanceVerdict.INSUFFICIENT_EVIDENCE)
    evidence_status = Column(SQLEnum(EvidenceStatus), nullable=False, default=EvidenceStatus.UNKNOWN)
    significance_confidence = Column(Float, nullable=False, default=0.0)
    evidence_confidence = Column(Float, nullable=False, default=0.0)
    dimensions = Column(JSONList, nullable=False)
    possible_context = Column(JSONList, nullable=True)
    reporters = Column(JSONList, nullable=True)
    attention_candidate = Column(Boolean, nullable=False, default=False)
    follow_up_candidate = Column(Boolean, nullable=False, default=False)
    explanation = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    superseded_by_assessment_id = Column(String, ForeignKey("significance_assessments.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    version = Column(Integer, nullable=False, default=1)


class AssessmentDimension(Base):
    __tablename__ = "assessment_dimensions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    assessment_id = Column(String, ForeignKey("significance_assessments.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    score = Column(Float, nullable=True)
    label = Column(String, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
