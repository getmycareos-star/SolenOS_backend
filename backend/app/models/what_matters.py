from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    MattersCategory,
    MattersImportance,
    AlignmentVerdict,
)


class WhatMattersEntry(Base):
    """Something this person or their family actually cares about.

    This is not a priority list. A priority is about urgency and sequencing; an
    entry here is about whether an action is worth doing at all, and about what a
    decision must never quietly override. The distinction is kept structural so
    the two cannot quietly collapse into each other.
    """

    __tablename__ = "what_matters_entries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    category = Column(SQLEnum(MattersCategory), nullable=False, index=True)
    statement = Column(Text, nullable=False)
    who_it_matters_to = Column(String, nullable=True)
    how_we_know = Column(Text, nullable=True)
    importance = Column(SQLEnum(MattersImportance), nullable=False, default=MattersImportance.MODERATE)
    what_it_rules_out = Column(JSONList, nullable=True)
    source_quote = Column(Text, nullable=True)
    stated_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    attributed_to_person = Column(Boolean, nullable=False, default=False)
    evidence_ids = Column(JSONList, nullable=True)
    confidence = Column(Float, nullable=True)
    possible_context = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_entry_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class MattersTension(Base):
    """Two things that pull against each other and cannot both be optimised."""

    __tablename__ = "what_matters_tensions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    between = Column(Text, nullable=False)
    note = Column(Text, nullable=True)
    entry_ids = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    confidence = Column(Float, nullable=True)
    possible_context = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AlignmentAssessment(Base):
    """Whether a proposed action serves what this person cares about.

    Separate from safety and from priority on purpose. An action can be safe and
    urgent and still be beside the point for this person, and that is a finding
    worth recording on its own.
    """

    __tablename__ = "alignment_assessments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    action_description = Column(Text, nullable=False)
    action_ref_type = Column(String, nullable=True)
    action_ref_id = Column(String, nullable=True, index=True)
    verdict = Column(SQLEnum(AlignmentVerdict), nullable=False, default=AlignmentVerdict.UNKNOWN)
    reasoning = Column(Text, nullable=True)
    tension = Column(Text, nullable=True)
    suggested_adaptation = Column(Text, nullable=True)
    entry_ids = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
