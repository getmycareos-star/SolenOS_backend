from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    ContradictionKind,
    ContradictionResolutionPath,
)


class ContradictionAnalysis(Base):
    """Why two records of the same thing disagree.

    The point of this table is that it almost never says "use the newer one".
    Most apparent contradictions are a supersession, a scope mismatch, or two
    people seeing different parts of the same evening, and those are all
    resolvable. The residual is the part that genuinely needs a person.
    """

    __tablename__ = "contradiction_analyses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    contradiction_id = Column(String, ForeignKey("contradictions.id"), nullable=True, index=True)

    primary_kind = Column(SQLEnum(ContradictionKind), nullable=True)
    likely_explanations = Column(JSONList, nullable=True)
    what_to_ask = Column(Text, nullable=True)
    who_can_resolve = Column(String, nullable=True)
    do_not_resolve_by = Column(Text, nullable=True)
    summary = Column(Text, nullable=False)

    recommended_path = Column(SQLEnum(ContradictionResolutionPath), nullable=True)
    is_auto_resolvable = Column(Boolean, nullable=False, default=False)

    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_analysis_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ContradictionResolution(Base):
    """A human decision about a contradiction.

    Only ever written by a person. The analysis proposes a path; this records
    what someone actually decided, who, and why, so a later reader can see
    whether the reasoning still holds.
    """

    __tablename__ = "contradiction_resolutions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey("contradiction_analyses.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    contradiction_id = Column(String, ForeignKey("contradictions.id"), nullable=True, index=True)

    path_taken = Column(SQLEnum(ContradictionResolutionPath), nullable=False)
    outcome = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    remaining_uncertainty = Column(Text, nullable=True)
    resolved_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), server_default=func.now())
