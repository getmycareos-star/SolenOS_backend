from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Any
from app.core.caregiver_enums import (
    CaregiverRoleType,
    CaregiverParticipationCategory,
    CaregiverInvolvementStatus,
    CaregiverProximityCategory,
    CaregiverAvailabilityStatus,
    ResponsibilityStatus,
    ResponsibilityAcceptanceStatus,
    HandoffAcceptanceStatus,
    ObservationType,
    ClaimType,
    CommunicationDirection,
    InformationVisibility,
    PerspectiveConflictStatus,
    CareNetworkRelationshipType,
    CaregiverActivityType,
    CaregiverAcknowledgmentLevel,
    CoverageGapStatus,
    DuplicateActionStatus,
    IdentityMatchConfidence,
)


class CaregiverProfileBase(BaseModel):
    person_id: str
    name: str
    email: str
    relationship: str
    participation_category: CaregiverParticipationCategory = CaregiverParticipationCategory.FAMILY
    involvement_status: CaregiverInvolvementStatus = CaregiverInvolvementStatus.ACTIVE
    proximity_category: Optional[CaregiverProximityCategory] = None
    availability_status: CaregiverAvailabilityStatus = CaregiverAvailabilityStatus.UNKNOWN
    timezone: str = "UTC"
    is_primary_designated: bool = False
    primary_designation_source: Optional[str] = None
    identity_match_confidence: IdentityMatchConfidence = IdentityMatchConfidence.CERTAIN
    identity_notes: Optional[str] = None


class CaregiverProfileCreate(CaregiverProfileBase):
    pass


class CaregiverProfile(CaregiverProfileBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CaregiverRoleBase(BaseModel):
    caregiver_id: str
    person_id: str
    role_type: CaregiverRoleType
    custom_role_name: Optional[str] = None
    scope: Optional[str] = None
    effective_start: datetime
    effective_end: Optional[datetime] = None
    is_current: bool = True
    assignment_source: Optional[str] = None


class CaregiverRoleCreate(CaregiverRoleBase):
    pass


class CaregiverRole(CaregiverRoleBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverResponsibilityBase(BaseModel):
    caregiver_id: str
    person_id: str
    responsibility_type: str
    scope: Optional[str] = None
    effective_start: datetime
    effective_end: Optional[datetime] = None
    status: ResponsibilityStatus = ResponsibilityStatus.ACTIVE
    acceptance_status: ResponsibilityAcceptanceStatus = ResponsibilityAcceptanceStatus.UNKNOWN
    assignment_source: Optional[str] = None
    is_joint: bool = False
    joint_with_caregiver_id: Optional[str] = None
    schedule_pattern: Optional[str] = None
    provenance: Optional[str] = None


class CaregiverResponsibilityCreate(CaregiverResponsibilityBase):
    pass


class CaregiverResponsibility(CaregiverResponsibilityBase):
    id: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CaregiverObservationBase(BaseModel):
    caregiver_id: str
    person_id: str
    observation_content: str
    observation_type: ObservationType = ObservationType.DIRECT
    event_time: Optional[datetime] = None
    event_time_precision: Optional[str] = None
    observation_context: Optional[str] = None
    location_context: Optional[str] = None
    duration_context: Optional[str] = None
    evidence_ref: Optional[str] = None
    information_visibility: InformationVisibility = InformationVisibility.PRIVATE
    is_promoted_to_shared: bool = False
    promoted_at: Optional[datetime] = None
    promoted_by_caregiver_id: Optional[str] = None
    extra_metadata: Optional[dict[str, Any]] = None


class CaregiverObservationCreate(CaregiverObservationBase):
    pass


class CaregiverObservation(CaregiverObservationBase):
    id: str
    report_time: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverPerspectiveBase(BaseModel):
    caregiver_id: str
    person_id: str
    claim_type: ClaimType
    claim_content: str
    context: Optional[str] = None
    evidence_ref: Optional[str] = None
    information_visibility: InformationVisibility = InformationVisibility.PRIVATE
    is_promoted_to_shared: bool = False
    promoted_at: Optional[datetime] = None
    promoted_by_caregiver_id: Optional[str] = None
    extra_metadata: Optional[dict[str, Any]] = None


class CaregiverPerspectiveCreate(CaregiverPerspectiveBase):
    pass


class CaregiverPerspective(CaregiverPerspectiveBase):
    id: str
    timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverHandoffBase(BaseModel):
    from_caregiver_id: str
    to_caregiver_id: str
    person_id: str
    responsibility_type: str
    scope: Optional[str] = None
    effective_time: Optional[datetime] = None
    context: Optional[str] = None
    acceptance_status: HandoffAcceptanceStatus = HandoffAcceptanceStatus.UNCONFIRMED
    acceptance_evidence: Optional[str] = None
    completion_evidence: Optional[str] = None
    is_completed: bool = False
    provenance: Optional[str] = None


class CaregiverHandoffCreate(CaregiverHandoffBase):
    pass


class CaregiverHandoff(CaregiverHandoffBase):
    id: str
    timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverActivityBase(BaseModel):
    caregiver_id: str
    person_id: str
    activity_type: CaregiverActivityType
    description: str
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[str] = None
    occurred_at: datetime
    is_anonymous: bool = False
    shared_account_id: Optional[str] = None
    extra_metadata: Optional[dict[str, Any]] = None


class CaregiverActivityCreate(CaregiverActivityBase):
    pass


class CaregiverActivity(CaregiverActivityBase):
    id: str
    recorded_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverCommunicationBase(BaseModel):
    from_caregiver_id: str
    to_caregiver_id: Optional[str] = None
    person_id: str
    direction: CommunicationDirection = CommunicationDirection.TO_ONE
    subject: Optional[str] = None
    content_summary: Optional[str] = None
    is_private: bool = True
    recipient_caregiver_ids: Optional[List[str]] = None
    visibility: InformationVisibility = InformationVisibility.RESTRICTED
    acknowledgment_level: Optional[CaregiverAcknowledgmentLevel] = None
    acknowledgment_time: Optional[datetime] = None
    extra_metadata: Optional[dict[str, Any]] = None


class CaregiverCommunicationCreate(CaregiverCommunicationBase):
    pass


class CaregiverCommunication(CaregiverCommunicationBase):
    id: str
    communication_time: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class CareNetworkRelationshipBase(BaseModel):
    person_id: str
    from_caregiver_id: str
    to_caregiver_id: Optional[str] = None
    relationship_type: CareNetworkRelationshipType
    description: Optional[str] = None
    effective_start: datetime
    effective_end: Optional[datetime] = None
    is_active: bool = True
    provenance: Optional[str] = None


class CareNetworkRelationshipCreate(CareNetworkRelationshipBase):
    pass


class CareNetworkRelationship(CareNetworkRelationshipBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class ResponsibilityGapBase(BaseModel):
    person_id: str
    responsibility_type: str
    scope: Optional[str] = None
    gap_start: Optional[datetime] = None
    last_known_caregiver_id: Optional[str] = None
    status: CoverageGapStatus = CoverageGapStatus.OPEN
    resolution: Optional[str] = None
    resolved_at: Optional[datetime] = None
    provenance: Optional[str] = None


class ResponsibilityGapCreate(ResponsibilityGapBase):
    pass


class ResponsibilityGap(ResponsibilityGapBase):
    id: str
    detected_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class PerspectiveConflictBase(BaseModel):
    person_id: str
    caregiver_a_id: str
    caregiver_b_id: str
    conflict_type: str
    claim_a: str
    claim_b: str
    context_a: Optional[str] = None
    context_b: Optional[str] = None
    status: PerspectiveConflictStatus = PerspectiveConflictStatus.UNRESOLVED
    resolution: Optional[str] = None
    resolved_at: Optional[datetime] = None
    evidence_ref: Optional[str] = None


class PerspectiveConflictCreate(PerspectiveConflictBase):
    pass


class PerspectiveConflict(PerspectiveConflictBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class DuplicateActionBase(BaseModel):
    person_id: str
    caregiver_a_id: str
    caregiver_b_id: str
    action_type: str
    action_description_a: str
    action_description_b: str
    time_a: Optional[datetime] = None
    time_b: Optional[datetime] = None
    status: DuplicateActionStatus = DuplicateActionStatus.POTENTIAL_DUPLICATE
    resolution: Optional[str] = None


class DuplicateActionCreate(DuplicateActionBase):
    pass


class DuplicateAction(DuplicateActionBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverContextBase(BaseModel):
    caregiver_id: str
    person_id: str
    proximity_category: Optional[CaregiverProximityCategory] = None
    observation_window_start: Optional[str] = None
    observation_window_end: Optional[str] = None
    availability_notes: Optional[str] = None
    context_metadata: Optional[dict[str, Any]] = None
    effective_start: datetime
    effective_end: Optional[datetime] = None


class CaregiverContextCreate(CaregiverContextBase):
    pass


class CaregiverContext(CaregiverContextBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True


class CareNetworkStateBase(BaseModel):
    person_id: str
    active_caregiver_ids: List[str]
    responsibility_summary: Optional[dict[str, Any]] = None
    active_handoffs: Optional[dict[str, Any]] = None
    observation_summary: Optional[dict[str, Any]] = None
    perspective_conflicts: Optional[dict[str, Any]] = None
    responsibility_gaps: Optional[dict[str, Any]] = None
    communication_relationships: Optional[dict[str, Any]] = None
    visibility_boundaries: Optional[dict[str, Any]] = None


class CareNetworkStateCreate(CareNetworkStateBase):
    pass


class CareNetworkState(CareNetworkStateBase):
    id: str
    snapshot_time: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class CaregiverSummary(BaseModel):
    caregiver_id: str
    name: str
    relationship: str
    participation_category: CaregiverParticipationCategory
    involvement_status: CaregiverInvolvementStatus
    proximity_category: Optional[CaregiverProximityCategory]
    roles: List[dict[str, Any]]
    responsibilities: List[dict[str, Any]]
    recent_observations: List[dict[str, Any]]
    recent_perspectives: List[dict[str, Any]]
    recent_activities: List[dict[str, Any]]


class MultiCaregiverSummary(BaseModel):
    person_id: str
    caregivers: List[CaregiverSummary]
    active_responsibilities: List[dict[str, Any]]
    active_handoffs: List[dict[str, Any]]
    responsibility_gaps: List[dict[str, Any]]
    perspective_conflicts: List[dict[str, Any]]
    duplicate_actions: List[dict[str, Any]]
    coverage_gaps: List[dict[str, Any]]


class CaregiverQuestionResponse(BaseModel):
    question: str
    answer: str
    confidence: str
    evidence_refs: List[str]
    uncertainty: Optional[str] = None
