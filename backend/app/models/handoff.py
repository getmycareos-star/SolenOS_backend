"""Care Responsibility Handoff models.

Implements the handoff system for transferring care responsibilities between caregivers.
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Integer, Enum as SQLEnum, Index, JSON, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from datetime import datetime, timedelta
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.handoff_enums import (
    HandoffLifecycleState,
    HandoffType,
    ResponsibilityType,
    PermissionScope,
    HandoffParticipantRole,
    InvitationStatus,
    QuestionStatus,
    AcknowledgmentType,
    HandoffEventType,
    ContextSnapshotTrigger,
    ResponsibilityStatus,
    EvidenceLinkType,
    HandoffAccessLevel,
    HandoffPermission,
)


class Handoff(Base):
    """Main handoff entity - represents a care responsibility transfer."""
    __tablename__ = "handoffs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)

    # Core identification
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    handoff_type = Column(SQLEnum(HandoffType), nullable=False, default=HandoffType.TEMPORARY)

    # Lifecycle
    lifecycle_state = Column(SQLEnum(HandoffLifecycleState), nullable=False, default=HandoffLifecycleState.DRAFT, index=True)
    lifecycle_state_reason = Column(Text, nullable=True)

    # Temporal
    proposed_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    resumed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    revocation_reason = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Sender info (who is handing off)
    sender_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    sender_person_id = Column(String, ForeignKey("persons.id"), nullable=True)  # When sender is also a care recipient

    # Context
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # Metadata
    created_by = Column(String, ForeignKey("caregivers.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    sender_caregiver = relationship("Caregiver", foreign_keys=[sender_caregiver_id])
    creator = relationship("Caregiver", foreign_keys=[created_by])
    revoker = relationship("Caregiver", foreign_keys=[revoked_by])

    participants = relationship("HandoffParticipant", back_populates="handoff", cascade="all, delete-orphan")
    responsibilities = relationship("HandoffResponsibility", back_populates="handoff", cascade="all, delete-orphan")
    permissions = relationship("HandoffPermission", back_populates="handoff", cascade="all, delete-orphan")
    context_snapshots = relationship("HandoffContextSnapshot", back_populates="handoff", cascade="all, delete-orphan")
    questions = relationship("HandoffQuestion", back_populates="handoff", cascade="all, delete-orphan")
    acknowledgments = relationship("HandoffAcknowledgment", back_populates="handoff", cascade="all, delete-orphan")
    events = relationship("HandoffEvent", back_populates="handoff", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_handoffs_person_state", "person_id", "lifecycle_state"),
        Index("ix_handoffs_sender_state", "sender_caregiver_id", "lifecycle_state"),
        Index("ix_handoffs_expires", "expires_at", "lifecycle_state"),
    )


class HandoffParticipant(Base):
    """Links people to handoffs with roles."""
    __tablename__ = "handoff_participants"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)

    # Participant identity
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    role = Column(SQLEnum(HandoffParticipantRole), nullable=False)

    # Invitation tracking
    invitation_status = Column(SQLEnum(InvitationStatus), nullable=False, default=InvitationStatus.PENDING)
    invited_at = Column(DateTime(timezone=True), server_default=func.now())
    responded_at = Column(DateTime(timezone=True), nullable=True)
    invitation_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Access level
    access_level = Column(SQLEnum(HandoffAccessLevel), nullable=False, default=HandoffAccessLevel.PARTICIPATE)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    handoff = relationship("Handoff", back_populates="participants")
    caregiver = relationship("Caregiver")

    __table_args__ = (
        Index("ix_handoff_participants_handoff_caregiver", "handoff_id", "caregiver_id", unique=True),
        Index("ix_handoff_participants_invitation", "invitation_status", "invitation_expires_at"),
    )


class HandoffResponsibility(Base):
    """Specific responsibilities being transferred in a handoff."""
    __tablename__ = "handoff_responsibilities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)

    # Responsibility definition
    responsibility_type = Column(SQLEnum(ResponsibilityType), nullable=False)
    custom_type = Column(String, nullable=True)  # For types not in enum
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    instructions = Column(Text, nullable=True)

    # Status within this handoff
    status = Column(SQLEnum(ResponsibilityStatus), nullable=False, default=ResponsibilityStatus.PROPOSED)

    # Scope
    permission_scope = Column(SQLEnum(PermissionScope), nullable=False, default=PermissionScope.READ_AND_ACT)

    # Temporal
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_until = Column(DateTime(timezone=True), nullable=True)

    # Assignment
    assigned_to_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    handoff = relationship("Handoff", back_populates="responsibilities")
    assigned_caregiver = relationship("Caregiver", foreign_keys=[assigned_to_caregiver_id])

    __table_args__ = (
        Index("ix_handoff_responsibilities_handoff_type", "handoff_id", "responsibility_type"),
        Index("ix_handoff_responsibilities_status", "status"),
    )


class HandoffPermission(Base):
    """Granular permissions per participant per handoff."""
    __tablename__ = "handoff_permissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)
    participant_id = Column(String, ForeignKey("handoff_participants.id"), nullable=False, index=True)

    permission = Column(SQLEnum(HandoffPermission), nullable=False)
    granted_by = Column(String, ForeignKey("caregivers.id"), nullable=False)
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("caregivers.id"), nullable=True)

    # Relationships
    handoff = relationship("Handoff", back_populates="permissions")
    participant = relationship("HandoffParticipant")
    granter = relationship("Caregiver", foreign_keys=[granted_by])
    revoker = relationship("Caregiver", foreign_keys=[revoked_by])

    __table_args__ = (
        Index("ix_handoff_permissions_handoff_participant", "handoff_id", "participant_id"),
        Index("ix_handoff_permissions_permission", "permission"),
    )


class HandoffContextSnapshot(Base):
    """Context captured at handoff points - evidence, care events, continuity threads."""
    __tablename__ = "handoff_context_snapshots"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)

    # Trigger
    trigger = Column(SQLEnum(ContextSnapshotTrigger), nullable=False)

    # Captured data (JSON for flexibility)
    care_events = Column(JSONList, nullable=True)  # List of care event IDs
    evidence_items = Column(JSONList, nullable=True)  # List of evidence IDs
    continuity_threads = Column(JSONList, nullable=True)  # List of thread IDs
    active_actions = Column(JSONList, nullable=True)  # List of action IDs
    meanings = Column(JSONList, nullable=True)  # List of meaning IDs
    medications = Column(JSONList, nullable=True)  # List of medication records
    vitals = Column(JSONList, nullable=True)  # List of vital sign records
    scales = Column(JSONList, nullable=True)  # List of scale observations
    insights = Column(JSONList, nullable=True)  # List of insight IDs
    follow_ups = Column(JSONList, nullable=True)  # List of follow-up IDs

    # Summary
    snapshot_summary = Column(Text, nullable=True)
    evidence_count = Column(Integer, default=0)
    event_count = Column(Integer, default=0)

    # Metadata
    taken_by = Column(String, ForeignKey("caregivers.id"), nullable=False)
    taken_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    handoff = relationship("Handoff", back_populates="context_snapshots")
    taker = relationship("Caregiver", foreign_keys=[taken_by])

    __table_args__ = (
        Index("ix_handoff_context_snapshots_handoff_trigger", "handoff_id", "trigger"),
    )


class HandoffQuestion(Base):
    """Questions asked during handoff process."""
    __tablename__ = "handoff_questions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)
    participant_id = Column(String, ForeignKey("handoff_participants.id"), nullable=False, index=True)

    # Question content
    question_text = Column(Text, nullable=False)
    context_reference = Column(Text, nullable=True)  # What evidence/event this relates to

    # Status
    status = Column(SQLEnum(QuestionStatus), nullable=False, default=QuestionStatus.ASKED)

    # Answer
    answer_text = Column(Text, nullable=True)
    answered_by = Column(String, ForeignKey("caregivers.id"), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    handoff = relationship("Handoff", back_populates="questions")
    asker = relationship("HandoffParticipant", foreign_keys=[participant_id])
    answerer = relationship("Caregiver", foreign_keys=[answered_by])

    __table_args__ = (
        Index("ix_handoff_questions_handoff_status", "handoff_id", "status"),
    )


class HandoffAcknowledgment(Base):
    """Read/understand/accept/decline records for handoff."""
    __tablename__ = "handoff_acknowledgments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)
    participant_id = Column(String, ForeignKey("handoff_participants.id"), nullable=False, index=True)

    # Acknowledgment type
    acknowledgment_type = Column(SQLEnum(AcknowledgmentType), nullable=False)

    # Context
    context_snapshot_id = Column(String, ForeignKey("handoff_context_snapshots.id"), nullable=True)
    notes = Column(Text, nullable=True)

    # Metadata
    acknowledged_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    handoff = relationship("Handoff", back_populates="acknowledgments")
    participant = relationship("HandoffParticipant", foreign_keys=[participant_id])
    context_snapshot = relationship("HandoffContextSnapshot", foreign_keys=[context_snapshot_id])

    __table_args__ = (
        Index("ix_handoff_acknowledgments_handoff_participant_type", "handoff_id", "participant_id", "acknowledgment_type"),
    )


class HandoffEvent(Base):
    """Audit trail events for handoff lifecycle."""
    __tablename__ = "handoff_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("handoffs.id"), nullable=False, index=True)
    participant_id = Column(String, ForeignKey("handoff_participants.id"), nullable=True, index=True)

    # Event
    event_type = Column(SQLEnum(HandoffEventType), nullable=False)
    description = Column(Text, nullable=True)
    event_metadata = Column(JSONDict, nullable=True)  # Additional context

    # Actor
    actor_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    handoff = relationship("Handoff", back_populates="events")
    participant = relationship("HandoffParticipant", foreign_keys=[participant_id])
    actor = relationship("Caregiver", foreign_keys=[actor_caregiver_id])

    __table_args__ = (
        Index("ix_handoff_events_handoff_type", "handoff_id", "event_type"),
        Index("ix_handoff_events_created", "created_at"),
    )