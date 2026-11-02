from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, UniqueConstraint, event
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList


class ActionType(str):
    SCHEDULE = "schedule"
    CALL = "call"
    ATTEND = "attend"
    MONITOR = "monitor"
    START = "start"
    STOP = "stop"
    CONTINUE = "continue"
    INCREASE = "increase"
    DECREASE = "decrease"
    HOLD = "hold"
    TAKE = "take"
    ASK = "ask"
    OBTAIN = "obtain"
    SUBMIT = "submit"
    BRING = "bring"
    REVIEW = "review"
    CONFIRM = "confirm"
    FOLLOW_UP = "follow_up"
    REFER = "refer"
    ARRANGE = "arrange"
    RECORD = "record"
    NOTIFY = "notify"
    OTHER = "other"


class ActionModality(str):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"
    CONDITIONAL = "conditional"
    PROHIBITED = "prohibited"
    INFORMATIONAL = "informational"


class ActionStatus(str):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    DEFERRED = "deferred"
    NEEDS_CLARIFICATION = "needs_clarification"
    DETECTED = "detected"
    PENDING_CONFIRMATION = "pending_confirmation"
    ACTIVE = "active"
    CONDITIONAL = "conditional"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class ActorType(str):
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    PROVIDER = "provider"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class SubjectType(str):
    PATIENT = "patient"
    MEDICATION = "medication"
    APPOINTMENT = "appointment"
    TEST = "test"
    CARE_PLAN = "care_plan"
    OTHER = "other"


class RiskTier(str):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class ActionAuthority(str):
    PHYSICIAN = "physician"
    NURSE = "nurse"
    CAREGIVER = "caregiver"
    ADMINISTRATIVE = "administrative"
    PATIENT = "patient"
    SYSTEM = "system"
    EDUCATIONAL = "educational"
    UNKNOWN = "unknown"


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (UniqueConstraint("person_id", "registration_key", name="uq_actions_person_registration_key"), UniqueConstraint("person_id", "suggestion_id", name="uq_actions_person_suggestion_id"), UniqueConstraint("person_id", "action_identity_key", name="uq_actions_person_identity_key"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    source_evidence_id = Column(String, ForeignKey("evidence.id"), nullable=False, index=True)
    source_passage = Column(Text, nullable=False)
    source_document_type = Column(String, nullable=True)
    source_authority = Column(String, nullable=False, default=ActionAuthority.UNKNOWN)
    extraction_confidence = Column(Float, nullable=True, default=None)
    provenance_notes = Column(Text, nullable=True)

    actor_type = Column(String, nullable=False, default=ActorType.UNKNOWN)
    actor_id = Column(String, nullable=True)
    actor_label = Column(String, nullable=True)

    subject_type = Column(String, nullable=False, default=SubjectType.PATIENT)
    subject_id = Column(String, nullable=True)
    subject_label = Column(String, nullable=True)

    action_type = Column(String, nullable=False, default=ActionType.OTHER)
    action_object = Column(Text, nullable=True)
    normalized_action = Column(Text, nullable=False)
    original_text = Column(Text, nullable=False)

    modality = Column(String, nullable=False, default=ActionModality.INFORMATIONAL)
    condition_text = Column(Text, nullable=True)
    trigger_text = Column(Text, nullable=True)

    deadline = Column(DateTime(timezone=True), nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=True)
    duration = Column(String, nullable=True)
    recurrence = Column(String, nullable=True)
    reference_frame_id = Column(String, ForeignKey("temporal_reference_frames.id"), nullable=True)

    prerequisite_action_ids = Column(JSONList, nullable=True)
    depends_on_event_id = Column(String, ForeignKey("care_events.id"), nullable=True)

    status = Column(String, nullable=False, default=ActionStatus.DETECTED)
    registration_key = Column(String(128), nullable=True)
    suggestion_id = Column(String(160), nullable=True)
    action_identity_key = Column(String(64), nullable=True)
    registration_request_sha256 = Column(String(64), nullable=True)
    source_evidence_ids = Column(JSONList, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    deferred_until = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    completion_evidence_ids = Column(JSONList, nullable=True)
    completion_confidence = Column(Float, nullable=True)
    superseded_by_action_id = Column(String, ForeignKey("actions.id"), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    expired_at = Column(DateTime(timezone=True), nullable=True)

    is_explicit = Column(Boolean, nullable=False, default=True)
    is_medication_action = Column(Boolean, nullable=False, default=False)
    risk_tier = Column(String, nullable=False, default=RiskTier.MODERATE)
    requires_confirmation = Column(Boolean, nullable=False, default=False)
    has_condition = Column(Boolean, nullable=False, default=False)
    is_recurring = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    responsible_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)

    __mapper_args__ = {"version_id_col": version}


class ActionLifecycleAudit(Base):
    """Append-only actor-attributed record of every registered-action transition."""
    __tablename__ = "action_lifecycle_audit"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    action_id = Column(String, ForeignKey("actions.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    actor_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    previous_status = Column(String, nullable=True)
    new_status = Column(String, nullable=False)
    reason = Column(Text, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@event.listens_for(ActionLifecycleAudit, "before_update")
def _prevent_action_audit_update(mapper, connection, target):
    raise ValueError("Action lifecycle audit is append-only")


@event.listens_for(ActionLifecycleAudit, "before_delete")
def _prevent_action_audit_delete(mapper, connection, target):
    raise ValueError("Action lifecycle audit is append-only")
