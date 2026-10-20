"""Data Classification System for SolenOS.

Defines sensitivity levels for all data types in the system.
Used by authorization, audit, retention, and AI processing layers.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, List, Optional, Set
from datetime import timedelta


class DataClassification(str, Enum):
    """Sensitivity classification for data in SolenOS.

    Ordered by sensitivity (lowest to highest).
    Each level inherits protections of all lower levels.
    """

    PUBLIC = "public"
    """Publicly visible information: marketing pages, public community posts."""

    COMMUNITY = "community"
    """Community-contributed content: peer experiences, professional guidance (non-clinical)."""

    ACCOUNT = "account"
    """Account identity: email, name, role, preferences. Not care content."""

    PROFESSIONAL = "professional"
    """Professional identity: verified status, role, jurisdiction, verification metadata."""

    PRIVATE_CARE = "private_care"
    """Private care information: observations, events, medications, appointments, symptoms."""

    HIGHLY_SENSITIVE_CARE = "highly_sensitive_care"
    """Highly sensitive care data: clinical documents, diagnoses, mental health, end-of-life."""

    CREDENTIAL = "credential"
    """Professional credential data: license numbers, verification evidence, government IDs."""

    SECURITY = "security"
    """Security data: audit logs, session tokens, access grants, threat detection data."""

    GOVERNANCE = "governance"
    """Governance data: policies, consent records, retention schedules, legal holds."""

    SYSTEM_SECRET = "system_secret"
    """System secrets: encryption keys, API keys, database credentials, service tokens."""


@dataclass(frozen=True)
class ClassificationPolicy:
    """Policy rules for a data classification level."""

    classification: DataClassification
    # Minimum authentication required
    requires_authentication: bool
    # Minimum authorization level
    min_authorization_level: str
    # Encryption requirements
    encrypt_at_rest: bool
    encrypt_in_transit: bool
    # Retention
    default_retention: timedelta
    # Audit requirements
    audit_all_access: bool
    audit_modifications: bool
    # AI processing
    ai_processing_allowed: bool
    ai_minimization_required: bool
    # Export
    export_requires_consent: bool
    # Deletion
    hard_delete_allowed: bool
    # Search indexing
    searchable_by_owner_only: bool
    # Cache
    cache_ttl_seconds: int


# Policy definitions for each classification
CLASSIFICATION_POLICIES: Dict[DataClassification, ClassificationPolicy] = {
    DataClassification.PUBLIC: ClassificationPolicy(
        classification=DataClassification.PUBLIC,
        requires_authentication=False,
        min_authorization_level="none",
        encrypt_at_rest=False,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 7),
        audit_all_access=False,
        audit_modifications=True,
        ai_processing_allowed=True,
        ai_minimization_required=False,
        export_requires_consent=False,
        hard_delete_allowed=True,
        searchable_by_owner_only=False,
        cache_ttl_seconds=3600,
    ),
    DataClassification.COMMUNITY: ClassificationPolicy(
        classification=DataClassification.COMMUNITY,
        requires_authentication=True,
        min_authorization_level="community_member",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 3),
        audit_all_access=False,
        audit_modifications=True,
        ai_processing_allowed=True,
        ai_minimization_required=True,
        export_requires_consent=False,
        hard_delete_allowed=True,
        searchable_by_owner_only=False,
        cache_ttl_seconds=1800,
    ),
    DataClassification.ACCOUNT: ClassificationPolicy(
        classification=DataClassification.ACCOUNT,
        requires_authentication=True,
        min_authorization_level="account_owner",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 7),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=False,
        ai_minimization_required=True,
        export_requires_consent=True,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=300,
    ),
    DataClassification.PROFESSIONAL: ClassificationPolicy(
        classification=DataClassification.PROFESSIONAL,
        requires_authentication=True,
        min_authorization_level="professional_owner_or_verifier",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 7),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=False,
        ai_minimization_required=True,
        export_requires_consent=True,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=300,
    ),
    DataClassification.PRIVATE_CARE: ClassificationPolicy(
        classification=DataClassification.PRIVATE_CARE,
        requires_authentication=True,
        min_authorization_level="care_context_member",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 10),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=True,
        ai_minimization_required=True,
        export_requires_consent=True,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=60,
    ),
    DataClassification.HIGHLY_SENSITIVE_CARE: ClassificationPolicy(
        classification=DataClassification.HIGHLY_SENSITIVE_CARE,
        requires_authentication=True,
        min_authorization_level="care_context_member_explicit",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 20),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=True,
        ai_minimization_required=True,
        export_requires_consent=True,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=0,
    ),
    DataClassification.CREDENTIAL: ClassificationPolicy(
        classification=DataClassification.CREDENTIAL,
        requires_authentication=True,
        min_authorization_level="credential_owner_or_verifier",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 7),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=False,
        ai_minimization_required=True,
        export_requires_consent=True,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=0,
    ),
    DataClassification.SECURITY: ClassificationPolicy(
        classification=DataClassification.SECURITY,
        requires_authentication=True,
        min_authorization_level="security_admin",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 7),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=False,
        ai_minimization_required=True,
        export_requires_consent=False,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=0,
    ),
    DataClassification.GOVERNANCE: ClassificationPolicy(
        classification=DataClassification.GOVERNANCE,
        requires_authentication=True,
        min_authorization_level="privacy_admin",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 10),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=False,
        ai_minimization_required=True,
        export_requires_consent=False,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=0,
    ),
    DataClassification.SYSTEM_SECRET: ClassificationPolicy(
        classification=DataClassification.SYSTEM_SECRET,
        requires_authentication=True,
        min_authorization_level="system_admin",
        encrypt_at_rest=True,
        encrypt_in_transit=True,
        default_retention=timedelta(days=365 * 1),
        audit_all_access=True,
        audit_modifications=True,
        ai_processing_allowed=False,
        ai_minimization_required=True,
        export_requires_consent=False,
        hard_delete_allowed=False,
        searchable_by_owner_only=True,
        cache_ttl_seconds=0,
    ),
}


# Data inventory mapping: table/field -> classification
DATA_INVENTORY: Dict[str, DataClassification] = {
    # Account identity
    "caregiver_profiles.email": DataClassification.ACCOUNT,
    "caregiver_profiles.name": DataClassification.ACCOUNT,
    "caregiver_profiles.relationship": DataClassification.ACCOUNT,
    "caregiver_profiles.timezone": DataClassification.ACCOUNT,
    "caregiver_profiles.participation_category": DataClassification.ACCOUNT,

    # Professional identity
    "caregiver_profiles.verification_status": DataClassification.PROFESSIONAL,
    "caregiver_profiles.credential_type": DataClassification.PROFESSIONAL,
    "caregiver_profiles.credential_number": DataClassification.CREDENTIAL,
    "caregiver_profiles.credential_issuer": DataClassification.CREDENTIAL,
    "caregiver_profiles.credential_jurisdiction": DataClassification.CREDENTIAL,
    "caregiver_profiles.verification_evidence": DataClassification.CREDENTIAL,
    "caregiver_profiles.verification_date": DataClassification.PROFESSIONAL,
    "caregiver_profiles.verification_expiry": DataClassification.PROFESSIONAL,

    # Care recipient identity
    "persons.name": DataClassification.PRIVATE_CARE,
    "persons.date_of_birth": DataClassification.PRIVATE_CARE,

    # Evidence
    "evidence.source_text": DataClassification.PRIVATE_CARE,
    "evidence.normalized_text": DataClassification.PRIVATE_CARE,
    "evidence.subject_id": DataClassification.PRIVATE_CARE,
    "evidence.observer_id": DataClassification.PRIVATE_CARE,
    "evidence.author_id": DataClassification.PRIVATE_CARE,
    "evidence.event_time": DataClassification.PRIVATE_CARE,
    "evidence.confidence_*": DataClassification.PRIVATE_CARE,
    "evidence.status": DataClassification.PRIVATE_CARE,

    # Care Events
    "care_events.title": DataClassification.PRIVATE_CARE,
    "care_events.description": DataClassification.PRIVATE_CARE,
    "care_events.original_expression": DataClassification.PRIVATE_CARE,
    "care_events.event_time": DataClassification.PRIVATE_CARE,
    "care_events.participants": DataClassification.PRIVATE_CARE,
    "care_events.evidence_ids": DataClassification.PRIVATE_CARE,
    "care_events.severity": DataClassification.PRIVATE_CARE,
    "care_events.status": DataClassification.PRIVATE_CARE,
    "care_events.outcome": DataClassification.PRIVATE_CARE,

    # Documents
    "documents.original_filename": DataClassification.PRIVATE_CARE,
    "documents.stored_filename": DataClassification.PRIVATE_CARE,
    "documents.storage_path": DataClassification.PRIVATE_CARE,
    "documents.extracted_text": DataClassification.HIGHLY_SENSITIVE_CARE,
    "documents.content_hash": DataClassification.PRIVATE_CARE,
    "documents.document_type": DataClassification.PRIVATE_CARE,

    # Clinical documents
    "documents.document_type:clinical": DataClassification.HIGHLY_SENSITIVE_CARE,
    "documents.extracted_text:clinical": DataClassification.HIGHLY_SENSITIVE_CARE,

    # Community
    "community_posts.body": DataClassification.COMMUNITY,
    "community_posts.author_label": DataClassification.COMMUNITY,
    "community_posts.tags": DataClassification.COMMUNITY,
    "community_posts.visibility": DataClassification.COMMUNITY,

    # Sharing
    "care_share_grants": DataClassification.PRIVATE_CARE,
    "care_share_access_log": DataClassification.SECURITY,
    "care_share_disclosures": DataClassification.PRIVATE_CARE,

    # Person context
    "person_attributes.value": DataClassification.PRIVATE_CARE,
    "person_triggers.trigger": DataClassification.PRIVATE_CARE,
    "person_calmers.measure": DataClassification.PRIVATE_CARE,
    "person_profiles.summary": DataClassification.PRIVATE_CARE,

    # Audit
    "audit_logs.*": DataClassification.SECURITY,

    # Sessions
    "sessions.token": DataClassification.SECURITY,
    "sessions.refresh_token": DataClassification.SYSTEM_SECRET,

    # AI
    "llm_reasoning_results.*": DataClassification.PRIVATE_CARE,
    "embeddings.*": DataClassification.PRIVATE_CARE,

    # Caregiver intelligence
    "caregiver_observations.observation_content": DataClassification.PRIVATE_CARE,
    "caregiver_perspectives.claim_content": DataClassification.PRIVATE_CARE,
    "caregiver_communications.content_summary": DataClassification.PRIVATE_CARE,
    "caregiver_handoffs.context": DataClassification.PRIVATE_CARE,
}

# Reverse mapping: classification -> tables/fields
CLASSIFICATION_TO_FIELDS: Dict[DataClassification, List[str]] = {}
for field, cls in DATA_INVENTORY.items():
    CLASSIFICATION_TO_FIELDS.setdefault(cls, []).append(field)


def get_classification(field_path: str) -> DataClassification:
    """Get classification for a specific field path."""
    return DATA_INVENTORY.get(field_path, DataClassification.PRIVATE_CARE)


def get_policy(classification: DataClassification) -> ClassificationPolicy:
    """Get policy for a classification level."""
    return CLASSIFICATION_POLICIES[classification]


def requires_explicit_consent(classification: DataClassification) -> bool:
    """Check if classification requires explicit consent for sharing."""
    policy = get_policy(classification)
    return policy.export_requires_consent


def get_retention_period(classification: DataClassification) -> timedelta:
    """Get default retention period for classification."""
    return get_policy(classification).default_retention


def is_ai_processing_allowed(classification: DataClassification) -> bool:
    """Check if AI processing is allowed for classification."""
    return get_policy(classification).ai_processing_allowed


def requires_ai_minimization(classification: DataClassification) -> bool:
    """Check if AI data minimization is required."""
    return get_policy(classification).ai_minimization_required