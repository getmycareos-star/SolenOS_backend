"""Pydantic schemas for the Care Responsibility Handoff system."""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

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


# ============================================================================
# Request Schemas
# ============================================================================

class HandoffCreate(BaseModel):
    """Create a new handoff draft."""
    person_id: str
    handoff_type: HandoffType = HandoffType.TEMPORARY
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    notes: Optional[str] = None
    expires_at: Optional[datetime] = None
    sender_caregiver_id: str


class HandoffUpdate(BaseModel):
    """Update handoff details (only in DRAFT state)."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    notes: Optional[str] = None
    expires_at: Optional[datetime] = None
    handoff_type: Optional[HandoffType] = None


class HandoffPropose(BaseModel):
    """Submit handoff for review/acceptance."""
    pass  # No additional fields needed, just lifecycle transition


class HandoffAccept(BaseModel):
    """Accept a proposed handoff."""
    participant_id: str  # The participant accepting


class HandoffDecline(BaseModel):
    """Decline a proposed handoff."""
    participant_id: str
    reason: Optional[str] = None


class HandoffActivate(BaseModel):
    """Activate an accepted handoff."""
    pass


class HandoffPause(BaseModel):
    """Pause an active handoff."""
    reason: Optional[str] = None


class HandoffResume(BaseModel):
    """Resume a paused handoff."""
    pass


class HandoffComplete(BaseModel):
    """Complete a handoff."""
    completion_notes: Optional[str] = None


class HandoffRevoke(BaseModel):
    """Revoke a handoff."""
    reason: str = Field(..., min_length=1)


class HandoffInvite(BaseModel):
    """Invite a participant to a handoff."""
    caregiver_id: str
    role: HandoffParticipantRole = HandoffParticipantRole.SECONDARY_RECIPIENT
    access_level: HandoffAccessLevel = HandoffAccessLevel.PARTICIPATE
    invitation_expires_at: Optional[datetime] = None
    message: Optional[str] = None


class HandoffQuestionAsk(BaseModel):
    """Ask a question during handoff."""
    question_text: str = Field(..., min_length=1)
    context_reference: Optional[str] = None


class HandoffQuestionAnswer(BaseModel):
    """Answer a question."""
    answer_text: str = Field(..., min_length=1)


class HandoffAcknowledge(BaseModel):
    """Record an acknowledgment."""
    acknowledgment_type: AcknowledgmentType
    context_snapshot_id: Optional[str] = None
    notes: Optional[str] = None


class HandoffPermissionGrant(BaseModel):
    """Grant a permission to a participant."""
    participant_id: str
    permission: HandoffPermission


class HandoffContextSnapshotRequest(BaseModel):
    """Request a context snapshot."""
    trigger: ContextSnapshotTrigger = ContextSnapshotTrigger.MANUAL_REQUEST


# ============================================================================
# Response Schemas
# ============================================================================

class HandoffParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    caregiver_id: str
    caregiver_name: Optional[str] = None
    role: HandoffParticipantRole
    invitation_status: InvitationStatus
    invited_at: datetime
    responded_at: Optional[datetime] = None
    invitation_expires_at: Optional[datetime] = None
    access_level: HandoffAccessLevel
    created_at: datetime
    updated_at: datetime


class HandoffResponsibilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    responsibility_type: ResponsibilityType
    custom_type: Optional[str] = None
    title: str
    description: Optional[str] = None
    instructions: Optional[str] = None
    status: ResponsibilityStatus
    permission_scope: PermissionScope
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    assigned_to_caregiver_id: Optional[str] = None
    assigned_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class HandoffPermissionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    participant_id: str
    permission: HandoffPermission
    granted_by: str
    granted_at: datetime
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None


class HandoffContextSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    trigger: ContextSnapshotTrigger
    care_events: List[str] = []
    evidence_items: List[str] = []
    continuity_threads: List[str] = []
    active_actions: List[str] = []
    meanings: List[str] = []
    medications: List[str] = []
    vitals: List[str] = []
    scales: List[str] = []
    insights: List[str] = []
    follow_ups: List[str] = []
    snapshot_summary: Optional[str] = None
    evidence_count: int = 0
    event_count: int = 0
    taken_by: str
    taken_at: datetime


class HandoffQuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    participant_id: str
    asker_name: Optional[str] = None
    question_text: str
    context_reference: Optional[str] = None
    status: QuestionStatus
    answer_text: Optional[str] = None
    answered_by: Optional[str] = None
    answered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class HandoffAcknowledgmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    participant_id: str
    participant_name: Optional[str] = None
    acknowledgment_type: AcknowledgmentType
    context_snapshot_id: Optional[str] = None
    notes: Optional[str] = None
    acknowledged_at: datetime


class HandoffEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handoff_id: str
    participant_id: Optional[str] = None
    event_type: HandoffEventType
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    actor_caregiver_id: Optional[str] = None
    actor_name: Optional[str] = None
    created_at: datetime


class HandoffSummaryResponse(BaseModel):
    """Lightweight handoff for list views."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    person_name: Optional[str] = None
    handoff_type: HandoffType
    lifecycle_state: HandoffLifecycleState
    title: str
    sender_caregiver_id: str
    sender_name: Optional[str] = None
    proposed_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    participant_count: int = 0
    created_at: datetime
    updated_at: datetime


class HandoffDetailResponse(BaseModel):
    """Full handoff detail with all related data."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    person_name: Optional[str] = None
    handoff_type: HandoffType
    lifecycle_state: HandoffLifecycleState
    lifecycle_state_reason: Optional[str] = None
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None
    sender_caregiver_id: str
    sender_name: Optional[str] = None
    sender_person_id: Optional[str] = None
    proposed_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    paused_at: Optional[datetime] = None
    resumed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revocation_reason: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_by: str
    created_at: datetime
    updated_at: datetime

    # Related data
    participants: List[HandoffParticipantResponse] = []
    responsibilities: List[HandoffResponsibilityResponse] = []
    permissions: List[HandoffPermissionResponse] = []
    context_snapshots: List[HandoffContextSnapshotResponse] = []
    questions: List[HandoffQuestionResponse] = []
    acknowledgments: List[HandoffAcknowledgmentResponse] = []
    events: List[HandoffEventResponse] = []


class HandoffContextFullResponse(BaseModel):
    """Full context snapshot with resolved data."""
    model_config = ConfigDict(from_attributes=True)

    snapshot: HandoffContextSnapshotResponse
    care_events: List[Dict[str, Any]] = []
    evidence_items: List[Dict[str, Any]] = []
    active_actions: List[Dict[str, Any]] = []
    medications: List[Dict[str, Any]] = []
    vitals: List[Dict[str, Any]] = []
    insights: List[Dict[str, Any]] = []


# ============================================================================
# List/Filter Schemas
# ============================================================================

class HandoffListParams(BaseModel):
    """Query parameters for listing handoffs."""
    person_id: Optional[str] = None
    lifecycle_state: Optional[HandoffLifecycleState] = None
    sender_caregiver_id: Optional[str] = None
    participant_caregiver_id: Optional[str] = None
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0)


class HandoffListResponse(BaseModel):
    handoffs: List[HandoffSummaryResponse]
    total: int
    limit: int
    offset: int


# ============================================================================
# Export Schemas
# ============================================================================

class HandoffExportContext(BaseModel):
    """Context for exporting handoff data."""
    handoff_id: str
    include_evidence: bool = True
    include_audit_log: bool = True
    format: str = "json"  # json, pdf


class HandoffExportResponse(BaseModel):
    handoff: HandoffDetailResponse
    context: Optional[HandoffContextFullResponse] = None
    exported_at: datetime
    exported_by: str