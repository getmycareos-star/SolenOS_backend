from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.care_context_enums import (
    PersonAttributeDomain,
    ProfileCompleteness,
)


class PersonAttribute(Base):
    """One thing the record establishes about who this person is.

    Not a condition, not a symptom. "Lights up when the grandchildren visit" and
    "gets distressed if the TV is on during dinner" are attributes; "more
    confused than last month" is a change and belongs on a trajectory, not here.
    """

    __tablename__ = "person_attributes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    domain = Column(SQLEnum(PersonAttributeDomain), nullable=False, index=True)
    label = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    source_quote = Column(Text, nullable=True)
    stated_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    confidence = Column(Float, nullable=True)
    possible_context = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_attribute_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PersonTrigger(Base):
    """A specific, named thing that has been seen to set this person off.

    Deliberately narrow. If the trigger cannot be named concretely it is not
    recorded, because a vague trigger is not actionable and a wrong one costs a
    caregiver real trust.
    """

    __tablename__ = "person_triggers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    trigger = Column(String, nullable=False)
    category = Column(String, nullable=True)
    response_seen = Column(Text, nullable=True)
    occurrences = Column(Integer, nullable=True)
    calming_measure = Column(Text, nullable=True)
    last_observed_at = Column(DateTime(timezone=True), nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    confidence = Column(Float, nullable=True)
    possible_context = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_trigger_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class PersonCalmer(Base):
    """A specific thing known to settle this person."""

    __tablename__ = "person_calmers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    measure = Column(String, nullable=False)
    context = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    confidence = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_calmer_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PersonProfile(Base):
    """The synthesised view of a person that other layers read.

    Rebuilt rather than mutated, and superseded rather than overwritten, so an
    earlier reading of a person always remains inspectable. This is what makes
    "we had her down as calmer in the evenings" a checkable statement.
    """

    __tablename__ = "person_profiles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    completeness = Column(SQLEnum(ProfileCompleteness), nullable=False, default=ProfileCompleteness.THIN)
    attribute_count = Column(Integer, nullable=False, default=0)
    trigger_count = Column(Integer, nullable=False, default=0)
    calmer_count = Column(Integer, nullable=False, default=0)
    summary = Column(Text, nullable=False)
    personalisation = Column(Text, nullable=True)
    communication_style = Column(Text, nullable=True)
    what_settles_them = Column(JSONList, nullable=True)
    what_upsets_them = Column(JSONList, nullable=True)
    important_people = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    reasoning_result_id = Column(String, ForeignKey("llm_reasoning_results.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    superseded_by_profile_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
