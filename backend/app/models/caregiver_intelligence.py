from sqlalchemy import Column, String, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
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


class CaregiverProfile(Base):
    __tablename__ = "caregiver_profiles"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    relationship = Column(String, nullable=False)
    participation_category = Column(Enum(CaregiverParticipationCategory), nullable=False, default=CaregiverParticipationCategory.FAMILY)
    involvement_status = Column(Enum(CaregiverInvolvementStatus), nullable=False, default=CaregiverInvolvementStatus.ACTIVE)
    proximity_category = Column(Enum(CaregiverProximityCategory), nullable=True)
    availability_status = Column(Enum(CaregiverAvailabilityStatus), nullable=False, default=CaregiverAvailabilityStatus.UNKNOWN)
    timezone = Column(String, nullable=False, default="UTC")
    is_primary_designated = Column(Boolean, nullable=False, default=False)
    primary_designation_source = Column(String, nullable=True)
    identity_match_confidence = Column(Enum(IdentityMatchConfidence), nullable=False, default=IdentityMatchConfidence.CERTAIN)
    identity_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CaregiverRole(Base):
    __tablename__ = "caregiver_roles"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    role_type = Column(Enum(CaregiverRoleType), nullable=False)
    custom_role_name = Column(String, nullable=True)
    scope = Column(Text, nullable=True)
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)
    is_current = Column(Boolean, nullable=False, default=True)
    assignment_source = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaregiverResponsibility(Base):
    __tablename__ = "caregiver_responsibilities"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    responsibility_type = Column(String, nullable=False)
    scope = Column(Text, nullable=True)
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(ResponsibilityStatus), nullable=False, default=ResponsibilityStatus.ACTIVE)
    acceptance_status = Column(Enum(ResponsibilityAcceptanceStatus), nullable=False, default=ResponsibilityAcceptanceStatus.UNKNOWN)
    assignment_source = Column(String, nullable=True)
    is_joint = Column(Boolean, nullable=False, default=False)
    joint_with_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True)
    schedule_pattern = Column(Text, nullable=True)
    provenance = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CaregiverObservation(Base):
    __tablename__ = "caregiver_observations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    observation_content = Column(Text, nullable=False)
    observation_type = Column(Enum(ObservationType), nullable=False, default=ObservationType.DIRECT)
    event_time = Column(DateTime(timezone=True), nullable=True)
    event_time_precision = Column(String, nullable=True)
    report_time = Column(DateTime(timezone=True), server_default=func.now())
    observation_context = Column(Text, nullable=True)
    location_context = Column(String, nullable=True)
    duration_context = Column(String, nullable=True)
    evidence_ref = Column(String, nullable=True)
    information_visibility = Column(Enum(InformationVisibility), nullable=False, default=InformationVisibility.PRIVATE)
    is_promoted_to_shared = Column(Boolean, nullable=False, default=False)
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    promoted_by_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True)
    extra_metadata = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaregiverPerspective(Base):
    __tablename__ = "caregiver_perspectives"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    claim_type = Column(Enum(ClaimType), nullable=False)
    claim_content = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    evidence_ref = Column(String, nullable=True)
    information_visibility = Column(Enum(InformationVisibility), nullable=False, default=InformationVisibility.PRIVATE)
    is_promoted_to_shared = Column(Boolean, nullable=False, default=False)
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    promoted_by_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True)
    extra_metadata = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaregiverHandoff(Base):
    __tablename__ = "caregiver_handoffs"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    from_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    to_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    responsibility_type = Column(String, nullable=False)
    scope = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    effective_time = Column(DateTime(timezone=True), nullable=True)
    context = Column(Text, nullable=True)
    acceptance_status = Column(Enum(HandoffAcceptanceStatus), nullable=False, default=HandoffAcceptanceStatus.UNCONFIRMED)
    acceptance_evidence = Column(Text, nullable=True)
    completion_evidence = Column(Text, nullable=True)
    is_completed = Column(Boolean, nullable=False, default=False)
    provenance = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaregiverActivity(Base):
    __tablename__ = "caregiver_activities"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    activity_type = Column(Enum(CaregiverActivityType), nullable=False)
    description = Column(Text, nullable=False)
    related_entity_type = Column(String, nullable=True)
    related_entity_id = Column(String, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
    is_anonymous = Column(Boolean, nullable=False, default=False)
    shared_account_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True)
    extra_metadata = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaregiverCommunication(Base):
    __tablename__ = "caregiver_communications"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    from_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    to_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    direction = Column(Enum(CommunicationDirection), nullable=False, default=CommunicationDirection.TO_ONE)
    subject = Column(String, nullable=True)
    content_summary = Column(Text, nullable=True)
    communication_time = Column(DateTime(timezone=True), server_default=func.now())
    is_private = Column(Boolean, nullable=False, default=True)
    recipient_caregiver_ids = Column(JSONList, nullable=True)
    visibility = Column(Enum(InformationVisibility), nullable=False, default=InformationVisibility.RESTRICTED)
    acknowledgment_level = Column(Enum(CaregiverAcknowledgmentLevel), nullable=True)
    acknowledgment_time = Column(DateTime(timezone=True), nullable=True)
    extra_metadata = Column(JSONList, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CareNetworkRelationship(Base):
    __tablename__ = "care_network_relationships"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    from_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    to_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True, index=True)
    relationship_type = Column(Enum(CareNetworkRelationshipType), nullable=False)
    description = Column(Text, nullable=True)
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    provenance = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ResponsibilityGap(Base):
    __tablename__ = "responsibility_gaps"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    responsibility_type = Column(String, nullable=False)
    scope = Column(Text, nullable=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    gap_start = Column(DateTime(timezone=True), nullable=True)
    last_known_caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=True)
    status = Column(Enum(CoverageGapStatus), nullable=False, default=CoverageGapStatus.OPEN)
    resolution = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    provenance = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PerspectiveConflict(Base):
    __tablename__ = "perspective_conflicts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_a_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    caregiver_b_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    conflict_type = Column(String, nullable=False)
    claim_a = Column(Text, nullable=False)
    claim_b = Column(Text, nullable=False)
    context_a = Column(Text, nullable=True)
    context_b = Column(Text, nullable=True)
    status = Column(Enum(PerspectiveConflictStatus), nullable=False, default=PerspectiveConflictStatus.UNRESOLVED)
    resolution = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    evidence_ref = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DuplicateAction(Base):
    __tablename__ = "duplicate_actions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_a_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    caregiver_b_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)
    action_description_a = Column(Text, nullable=False)
    action_description_b = Column(Text, nullable=False)
    time_a = Column(DateTime(timezone=True), nullable=True)
    time_b = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(DuplicateActionStatus), nullable=False, default=DuplicateActionStatus.POTENTIAL_DUPLICATE)
    resolution = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaregiverContext(Base):
    __tablename__ = "caregiver_contexts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    proximity_category = Column(Enum(CaregiverProximityCategory), nullable=True)
    observation_window_start = Column(String, nullable=True)
    observation_window_end = Column(String, nullable=True)
    availability_notes = Column(Text, nullable=True)
    context_metadata = Column(JSONList, nullable=True)
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CareNetworkState(Base):
    __tablename__ = "care_network_states"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    active_caregiver_ids = Column(JSONList, nullable=False)
    responsibility_summary = Column(JSONList, nullable=True)
    active_handoffs = Column(JSONList, nullable=True)
    observation_summary = Column(JSONList, nullable=True)
    perspective_conflicts = Column(JSONList, nullable=True)
    responsibility_gaps = Column(JSONList, nullable=True)
    communication_relationships = Column(JSONList, nullable=True)
    visibility_boundaries = Column(JSONList, nullable=True)
    snapshot_time = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
