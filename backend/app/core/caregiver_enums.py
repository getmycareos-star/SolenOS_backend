from enum import Enum


class CaregiverRoleType(str, Enum):
    MEDICATION_MANAGER = "medication_manager"
    APPOINTMENT_COORDINATOR = "appointment_coordinator"
    TRANSPORTATION_COORDINATOR = "transportation_coordinator"
    OVERNIGHT_CAREGIVER = "overnight_caregiver"
    EMERGENCY_CONTACT = "emergency_contact"
    OCCASIONAL_CAREGIVER = "occasional_caregiver"
    PAID_CAREGIVER = "paid_caregiver"
    HOME_HEALTH_WORKER = "home_health_worker"
    FINANCIAL_COORDINATOR = "financial_coordinator"
    PRIMARY_FAMILY_CAREGIVER = "primary_family_caregiver"
    SECONDARY_CAREGIVER = "secondary_caregiver"
    TEMPORARY_CAREGIVER = "temporary_caregiver"
    FACILITY_STAFF = "facility_staff"
    LEGAL_GUARDIAN = "legal_guardian"
    HEALTHCARE_PROXY = "healthcare_proxy"
    CUSTOM = "custom"


class CaregiverParticipationCategory(str, Enum):
    FAMILY = "family"
    PROFESSIONAL = "professional"
    INFORMAL = "informal"
    FACILITY = "facility"
    LEGAL = "legal"


class CaregiverInvolvementStatus(str, Enum):
    ACTIVE = "active"
    SECONDARY = "secondary"
    OCCASIONAL = "occasional"
    HISTORICAL = "historical"
    INACTIVE = "inactive"
    TEMPORARY = "temporary"


class CaregiverProximityCategory(str, Enum):
    LIVES_WITH = "lives_with"
    LOCAL = "local"
    REGIONAL = "regional"
    REMOTE = "remote"


class CaregiverAvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    LIMITED = "limited"
    UNKNOWN = "unknown"


class ResponsibilityStatus(str, Enum):
    ACTIVE = "active"
    JOINT = "joint"
    TEMPORARY = "temporary"
    HISTORICAL = "historical"
    UNASSIGNED = "unassigned"
    GAP = "gap"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"


class ResponsibilityAcceptanceStatus(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNCONFIRMED = "unconfirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class HandoffAcceptanceStatus(str, Enum):
    ACCEPTED = "accepted"
    UNCONFIRMED = "unconfirmed"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ObservationType(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    SECOND_HAND = "second_hand"
    DOCUMENT_REVIEW = "document_review"


class ClaimType(str, Enum):
    OBSERVATION = "observation"
    BELIEF = "belief"
    KNOWLEDGE_CLAIM = "knowledge_claim"
    INTERPRETATION = "interpretation"


class CommunicationDirection(str, Enum):
    TO_ONE = "to_one"
    TO_GROUP = "to_group"
    TO_PROVIDER = "to_provider"
    FROM_PROVIDER = "from_provider"
    BROADCAST = "broadcast"


class InformationVisibility(str, Enum):
    PRIVATE = "private"
    SHARED = "shared"
    CONDITIONALLY_SHARED = "conditionally_shared"
    RESTRICTED = "restricted"
    GROUP_SHARED = "group_shared"


class PerspectiveConflictStatus(str, Enum):
    ALIGNED = "aligned"
    CONTEXTUALLY_DIVERGENT = "contextually_divergent"
    CONFLICTING = "conflicting"
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class CareNetworkRelationshipType(str, Enum):
    RESPONSIBLE_FOR = "responsible_for"
    RECEIVES_HANDOFF = "receives_handoff"
    REPORTS_OBSERVATION = "reports_observation"
    COORDINATES = "coordinates"
    COMMUNICATES_WITH = "communicates_with"
    DELEGATED_TO = "delegated_to"
    INFORMED_BY = "informed_by"


class CaregiverActivityType(str, Enum):
    PERFORMED_TASK = "performed_task"
    REPORTED_OBSERVATION = "reported_observation"
    COMMUNICATED = "communicated"
    COORDINATED_APPOINTMENT = "coordinated_appointment"
    UPLOADED_DOCUMENT = "uploaded_document"
    ACKNOWLEDGED_INSTRUCTION = "acknowledged_instruction"
    DELEGATED = "delegated"
    HANDED_OFF = "handed_off"
    REQUESTED_CLARIFICATION = "requested_clarification"
    UPDATED_MEDICATION_INFO = "updated_medication_info"


class CaregiverAcknowledgmentLevel(str, Enum):
    DELIVERED = "delivered"
    READ = "read"
    ACKNOWLEDGED = "acknowledged"
    ACCEPTED = "accepted"
    COMPLETED = "completed"


class OwnershipConfidence(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    HISTORICAL = "historical"
    AMBIGUOUS = "ambiguous"


class CoverageGapStatus(str, Enum):
    OPEN = "open"
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    RESOLVED = "resolved"


class DuplicateActionStatus(str, Enum):
    POTENTIAL_DUPLICATE = "potential_duplicate"
    CONFIRMED_DUPLICATE = "confirmed_duplicate"
    APPROPRIATE_PARALLEL = "appropriate_parallel"


class IdentityMatchConfidence(str, Enum):
    CERTAIN = "certain"
    LIKELY = "likely"
    POSSIBLE = "possible"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"
