"""Durable care-continuity audit records.

The revision log is generated from ORM changes to the care-state models listed in
TRACKED_TABLES. It preserves before/after snapshots; it never rewrites source evidence.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
)
from sqlalchemy.sql import func
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.types import JSONList


class CareStateRevision(Base):
    __tablename__ = "care_state_revisions"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", "revision_number", name="uq_care_state_revision_target_version"),
        Index("ix_care_state_revisions_person_target", "person_id", "target_type", "target_id"),
        Index("ix_care_state_revisions_person_changed", "person_id", "changed_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    target_type = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    revision_number = Column(Integer, nullable=False)
    change_kind = Column(String, nullable=False)  # created | updated | deleted
    before_state = Column(Text, nullable=True)
    after_state = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=False, default=list)
    changed_by_caregiver_id = Column(String, nullable=True)
    changed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareAssertion(Base):
    """A source-attributed report, deliberately distinct from a confirmed care fact."""
    __tablename__ = "care_assertions"
    __table_args__ = (
        Index("ix_care_assertions_subject_field", "person_id", "subject_type", "subject_id", "field"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)
    field = Column(String, nullable=False)
    assertion_kind = Column(String, nullable=False, default="current_state")
    asserted_value = Column(Text, nullable=False)
    source_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)
    source_label = Column(String, nullable=False)
    source_text = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=False, default=list)
    assertion_status = Column(String, nullable=False, default="reported")
    observed_at = Column(DateTime(timezone=True), nullable=True)
    review_after_days = Column(Integer, nullable=True)
    created_by_caregiver_id = Column(String, nullable=True)
    updated_by_caregiver_id = Column(String, nullable=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareActionOutcome(Base):
    __tablename__ = "care_action_outcomes"
    __table_args__ = (
        Index("ix_care_action_outcomes_person_action", "person_id", "action_type", "action_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    action_id = Column(String, nullable=False)
    action_type = Column(String, nullable=False)
    outcome_status = Column(String, nullable=False)
    notes = Column(Text, nullable=True)
    correction_text = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=False, default=list)
    created_by_caregiver_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CareDeltaRun(Base):
    __tablename__ = "care_delta_runs"
    __table_args__ = (Index("ix_care_delta_runs_person_created", "person_id", "created_at"),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    source = Column(String, nullable=False)
    event_date = Column(DateTime(timezone=True), nullable=False)
    source_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)
    evidence_ids = Column(JSONList, nullable=False, default=list)
    delta_json = Column(Text, nullable=False)
    model_name = Column(String, nullable=False)
    created_by_caregiver_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


TRACKED_TABLES = {
    "persons", "caregivers", "evidence", "care_events", "temporal_facts", "temporal_reference_frames",
    "temporal_relations", "temporal_contradictions", "observations", "reasoning_memories",
    "care_windows", "appointments", "care_transitions", "locations", "daily_intelligence",
    "timezone_contexts", "temporal_relationships", "insights", "information_gaps", "care_assertions",
    "open_questions", "contradictions", "uncertainty_assessments",
    "baselines", "changes", "patterns", "situations", "corrections",
}


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(v) for v in value]
    return value


def _snapshot(obj, *, before=False):
    state = inspect(obj)
    result = {}
    for column in state.mapper.columns:
        key = column.key
        history = state.attrs[key].history
        if before and history.has_changes():
            if not history.deleted:
                history = state.attrs[key].load_history()
            value = history.deleted[0] if history.deleted else None
        else:
            value = getattr(obj, key, None)
        result[key] = _json_value(value)
    return result


@event.listens_for(Session, "before_flush")
def _version_care_state(session, flush_context, instances):
    """Create immutable before/after revisions in the same transaction as each write."""
    if any(isinstance(obj, (CareStateRevision, EvidenceReviewRecord)) for obj in list(session.dirty) + list(session.deleted)):
        raise ValueError("Care-state revisions and evidence review records are append-only and cannot be updated or deleted")
    candidates = list(session.new) + list(session.dirty) + list(session.deleted)
    seen = set()
    for obj in candidates:
        table = getattr(getattr(obj, "__table__", None), "name", None)
        if table not in TRACKED_TABLES or isinstance(obj, CareStateRevision):
            continue
        state = inspect(obj)
        if obj in session.dirty and not session.is_modified(obj, include_collections=True):
            continue
        if not getattr(obj, "id", None):
            obj.id = str(uuid.uuid4())
        person_id = getattr(obj, "person_id", None) or (obj.id if table == "persons" else None)
        if not person_id:
            continue
        target_id = str(obj.id)
        # Avoid creating two revisions for the same object in one flush.
        marker = (table, target_id)
        if marker in seen:
            continue
        seen.add(marker)
        is_new = obj in session.new
        is_deleted = obj in session.deleted
        before_state = None if is_new else _snapshot(obj, before=True)
        after_state = None if is_deleted else _snapshot(obj)
        evidence_ids = [] if is_deleted else (getattr(obj, "evidence_ids", None) or [])
        caregiver_id = (
            getattr(obj, "updated_by_caregiver_id", None)
            or getattr(obj, "created_by_caregiver_id", None)
            or getattr(obj, "caregiver_id", None)
        )
        with session.no_autoflush:
            latest = (
                session.query(CareStateRevision.revision_number)
                .filter(
                    CareStateRevision.person_id == str(person_id),
                    CareStateRevision.target_type == table,
                    CareStateRevision.target_id == target_id,
                )
                .order_by(CareStateRevision.revision_number.desc())
                .first()
            )
        revision_number = (latest[0] if latest else 0) + 1
        session.add(CareStateRevision(
            person_id=str(person_id),
            target_type=table,
            target_id=target_id,
            revision_number=revision_number,
            change_kind="created" if is_new else ("deleted" if is_deleted else "updated"),
            before_state=json.dumps(before_state, sort_keys=True, separators=(",", ":"), ensure_ascii=False) if before_state is not None else None,
            after_state=json.dumps(after_state, sort_keys=True, separators=(",", ":"), ensure_ascii=False) if after_state is not None else None,
            evidence_ids=list(evidence_ids),
            changed_by_caregiver_id=str(caregiver_id) if caregiver_id else None,
        ))



class EvidenceReviewRecord(Base):
    """Append-only, versioned assessment of persisted evidence and its attribution."""
    __tablename__ = "evidence_review_records"
    __table_args__ = (
        Index("ix_evidence_review_records_evidence", "evidence_id"),
        Index("ix_evidence_review_records_person", "person_id"),
        Index("ix_evidence_review_records_type", "review_type"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id = Column(String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False)
    review_type = Column(String(32), nullable=False)
    outcome = Column(String(32), nullable=False)
    rationale = Column(Text, nullable=False)
    criteria = Column(JSONList, nullable=False)
    policy_version = Column(String(64), nullable=False)
    source_attribution_snapshot = Column(Text, nullable=False)
    reviewed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceClaimAssessment(Base):
    """Append-only assessment version for a source-attributed care claim."""
    __tablename__ = "evidence_claim_assessments"
    __table_args__ = (
        UniqueConstraint("claim_id", "claim_version", name="uq_evidence_claim_assessment_version"),
        Index("ix_evidence_claim_assessments_person_claim", "person_id", "claim_id", "claim_version"),
        Index("ix_evidence_claim_assessments_person_created", "person_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    claim_id = Column(String, ForeignKey("care_assertions.id"), nullable=False, index=True)
    claim_version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    rationale = Column(Text, nullable=False)
    policy_version = Column(String(64), nullable=False)
    evidence_links = Column(JSONList, nullable=False)
    reviewed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    supersedes_assessment_id = Column(String, ForeignKey("evidence_claim_assessments.id"), nullable=True)
    superseded_by_claim_id = Column(String, ForeignKey("care_assertions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(Session, "before_flush")
def _enforce_evidence_review_append_only(session, flush_context, instances):
    """Review history is immutable: append a new review instead of editing an old one."""
    if any(isinstance(obj, EvidenceReviewRecord) for obj in session.dirty):
        raise ValueError("Evidence review records are append-only and cannot be updated")
    if any(isinstance(obj, EvidenceReviewRecord) for obj in session.deleted):
        raise ValueError("Evidence review records are append-only and cannot be deleted")


@event.listens_for(Session, "before_flush")
def _enforce_claim_assessment_append_only(session, flush_context, instances):
    """Claim assessment history is append-only; create a new version to correct it."""
    if any(isinstance(obj, EvidenceClaimAssessment) for obj in session.dirty):
        raise ValueError("Evidence claim assessments are append-only and cannot be updated")
    if any(isinstance(obj, EvidenceClaimAssessment) for obj in session.deleted):
        raise ValueError("Evidence claim assessments are append-only and cannot be deleted")


class EvidenceClaimDependency(Base):
    """Append-only dependency edge between two claims in the same care record."""
    __tablename__ = "evidence_claim_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "person_id", "claim_id", "depends_on_claim_id", "dependency_type",
            name="uq_evidence_claim_dependency",
        ),
        Index("ix_evidence_claim_dependencies_person_claim", "person_id", "claim_id"),
        Index("ix_evidence_claim_dependencies_person_depends", "person_id", "depends_on_claim_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id = Column(String, ForeignKey("care_assertions.id", ondelete="CASCADE"), nullable=False)
    depends_on_claim_id = Column(String, ForeignKey("care_assertions.id", ondelete="RESTRICT"), nullable=False)
    dependency_type = Column(String(32), nullable=False)
    rationale = Column(Text, nullable=False)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(Session, "before_flush")
def _enforce_evidence_claim_dependency_append_only(session, flush_context, instances):
    cls = EvidenceClaimDependency
    if any(isinstance(obj, cls) for obj in session.dirty):
        raise ValueError("Claim dependencies are append-only; create a new relationship instead")
    if any(isinstance(obj, cls) for obj in session.deleted):
        raise ValueError("Claim dependencies are append-only and cannot be deleted")


class EvidenceSourceRelationship(Base):
    """Append-only, person-scoped source lineage and corroboration review."""
    __tablename__ = "evidence_source_relationships"
    __table_args__ = (
        UniqueConstraint(
            "person_id", "source_evidence_id", "related_evidence_id", "relationship_type",
            name="uq_evidence_source_relationship",
        ),
        Index("ix_evidence_source_relationships_person_source", "person_id", "source_evidence_id"),
        Index("ix_evidence_source_relationships_person_related", "person_id", "related_evidence_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    source_evidence_id = Column(String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False)
    related_evidence_id = Column(String, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(32), nullable=False)
    source_independence_status = Column(String(24), nullable=False)
    origin_group_key = Column(String(128), nullable=True)
    rationale = Column(Text, nullable=False)
    reviewed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(Session, "before_flush")
def _enforce_evidence_source_relationship_append_only(session, flush_context, instances):
    """Source lineage and independence assessments are immutable audit facts."""
    cls = EvidenceSourceRelationship
    if any(isinstance(obj, cls) for obj in session.dirty):
        raise ValueError("Evidence source relationships are append-only; create a new assessment instead")
    if any(isinstance(obj, cls) for obj in session.deleted):
        raise ValueError("Evidence source relationships are append-only and cannot be deleted")


class EvidenceObservation(Base):
    """Structured, source-span-bound observation; never replaces its original evidence."""
    __tablename__ = "evidence_observations"
    __table_args__ = (
        Index("ix_evidence_observations_person_subject", "person_id", "subject_label", "attribute"),
        Index("ix_evidence_observations_person_time", "person_id", "occurred_at", "recorded_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id = Column(String, ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False, index=True)
    supersedes_observation_id = Column(String, ForeignKey("evidence_observations.id"), nullable=True)
    correction_rationale = Column(Text, nullable=True)
    observation_kind = Column(String(40), nullable=False)
    epistemic_status = Column(String(24), nullable=False)
    subject_label = Column(String(160), nullable=False)
    attribute = Column(String(160), nullable=False)
    value_text = Column(Text, nullable=True)
    normalized_value = Column(Text, nullable=True)
    units = Column(String(80), nullable=True)
    context = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True)
    observed_at = Column(DateTime(timezone=True), nullable=True)
    reported_at = Column(DateTime(timezone=True), nullable=True)
    observer_label = Column(Text, nullable=True)
    observer_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    observation_method = Column(String(120), nullable=True)
    source_passage = Column(Text, nullable=False)
    source_span_start = Column(Integer, nullable=False)
    source_span_end = Column(Integer, nullable=False)
    negated = Column(Boolean, nullable=True)
    hedge_marker = Column(String(120), nullable=True)
    ambiguity_note = Column(Text, nullable=True)
    extraction_method = Column(String(80), nullable=False)
    extraction_version = Column(String(80), nullable=False)
    extraction_run_id = Column(String(128), nullable=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(Session, "before_flush")
def _enforce_evidence_observation_append_only(session, flush_context, instances):
    """Corrections are new observation versions; original extracted records are not rewritten."""
    cls = EvidenceObservation
    if any(isinstance(obj, cls) for obj in session.dirty):
        raise ValueError("Evidence observations are append-only; create a corrected observation instead")
    if any(isinstance(obj, cls) for obj in session.deleted):
        raise ValueError("Evidence observations are append-only and cannot be deleted")
