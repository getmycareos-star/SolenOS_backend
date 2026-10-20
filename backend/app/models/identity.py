"""Identity Models for SolenOS.

Implements the separated identity architecture:
- Account: Authentication identity (how you sign in)
- Person: Care context identity (who the care is about)
- Caregiver: Role identity within a care context
- Professional: Verified professional identity
- Session: Active session with trust level
- Device: Registered device with trust level
"""

from sqlalchemy import Column, String, DateTime, Date, Boolean, Text, ForeignKey, Float, Integer, Enum as SQLEnum, Index, UniqueConstraint, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.identity_enums import (
    AccountType,
    PersonType,
    CaregiverRole,
    ProfessionalRole,
    ProfessionalVerificationStatus,
    VerificationMethod,
    SessionType,
    DeviceTrustLevel,
    IdentityLinkType,
    AuthProvider
)
from app.models.care_graph import CareGraphNode, CareGraphEdge

from app.models.care_event import CareEvent; from app.models.caregiver import Caregiver
from app.models.evidence import Evidence



class Account(Base):
    """Authentication account - how someone signs into SolenOS.

    This is separate from Person (care recipient) and Caregiver/Professional (roles).
    One account can link to multiple person/role identities.
    """
    __tablename__ = "accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_type = Column(SQLEnum(AccountType), nullable=False, index=True)

    # Authentication credentials
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=True)  # Null for OAuth-only accounts
    auth_provider = Column(SQLEnum(AuthProvider), nullable=False, default=AuthProvider.EMAIL_PASSWORD)
    oauth_provider_id = Column(String, nullable=True)  # Provider's user ID
    oauth_provider_email = Column(String, nullable=True)

    # Account status
    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    verification_token = Column(String, nullable=True)
    verification_token_expires = Column(DateTime(timezone=True), nullable=True)

    # Security
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_login_ip = Column(String, nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    mfa_secret = Column(String, nullable=True)
    backup_codes = Column(JSONList, nullable=True)

    # Account metadata
    display_name = Column(String, nullable=True)
    timezone = Column(String, nullable=False, default="UTC")
    locale = Column(String, nullable=False, default="en")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    person_links = relationship(
        "AccountPersonLink",
        back_populates="account",
        foreign_keys="AccountPersonLink.account_id",
    )
    sessions = relationship("Session", back_populates="account", foreign_keys="Session.account_id")
    devices = relationship("Device", back_populates="account", foreign_keys="Device.account_id")
    professional_profile = relationship("ProfessionalProfile", back_populates="account", uselist=False, foreign_keys="ProfessionalProfile.account_id")
    staff_profile = relationship("StaffProfile", back_populates="account", uselist=False, foreign_keys="StaffProfile.account_id")
    # investor = relationship("Investor", back_populates="account", uselist=False, foreign_keys="Investor.account_id")

    __table_args__ = (
        Index("ix_accounts_email_active", "email", "is_active"),
        Index("ix_accounts_type_active", "account_type", "is_active"),
    )


class Person(Base):
    """Person identity in a care context - who the care is about.

    This is the care recipient or other person in the care network.
    Separate from Account (authentication) and Caregiver/Professional (roles).
    """
    __tablename__ = "persons"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_type = Column(SQLEnum(PersonType), nullable=False, default=PersonType.CARE_RECIPIENT, index=True)

    # Identity
    name = Column(String, nullable=False)
    legal_name = Column(String, nullable=True)
    preferred_name = Column(String, nullable=True)
    date_of_birth = Column(DateTime(timezone=True), nullable=True)

    # Contact (minimal, for care coordination only)
    phone = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    emergency_column = Column(JSONDict, nullable=True)

    # Care context
    primary_condition = Column(String, nullable=True)
    conditions = Column(JSONList, nullable=True)
    care_level = Column(String, nullable=True)

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    is_deceased = Column(Boolean, nullable=False, default=False)
    deceased_at = Column(DateTime(timezone=True), nullable=True)

    # Provenance
    created_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    account_links = relationship("AccountPersonLink", back_populates="person", foreign_keys="AccountPersonLink.person_id")
    professional_roles = relationship("ProfessionalRoleLink", back_populates="person")
    care_events = relationship("CareEvent", foreign_keys="CareEvent.person_id", back_populates="person")
    evidence = relationship("Evidence", foreign_keys="Evidence.person_id", back_populates="person")
    care_graph_nodes = relationship("CareGraphNode", foreign_keys="CareGraphNode.person_id", back_populates="person")
    care_graph_edges = relationship("CareGraphEdge", foreign_keys="CareGraphEdge.person_id", back_populates="person")

    __table_args__ = (
        Index("ix_persons_type_active", "person_type", "is_active"),
    )


class AccountPersonLink(Base):
    """Links an Account to a Person with a specific relationship type.

    This is the authorization bridge - defines what an account can do
    for a specific person.
    """
    __tablename__ = "account_person_links"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    link_type = Column(SQLEnum(IdentityLinkType), nullable=False)

    # Authorization scope for this link
    role = Column(SQLEnum(CaregiverRole), nullable=True)
    permissions = Column(JSONList, nullable=True)  # Explicit permissions granted
    restrictions = Column(JSONList, nullable=True)  # Explicit restrictions

    # Verification
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    verification_method = Column(String, nullable=True)

    # Lifecycle
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    revocation_reason = Column(Text, nullable=True)

    # Relationships
    account = relationship("Account", back_populates="person_links", foreign_keys="AccountPersonLink.account_id")
    person = relationship("Person", back_populates="account_links", foreign_keys="AccountPersonLink.person_id")

    __table_args__ = (
        UniqueConstraint("account_id", "person_id", "link_type", name="uq_account_person_link"),
        Index("ix_account_person_link_active", "account_id", "person_id", "is_active"),
    )


class ProfessionalProfile(Base):
    """Verified professional identity linked to an Account.

    Separate from Account - a professional has an Account for authentication
    and this profile for their verified professional identity.
    """
    __tablename__ = "professional_profiles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)

    # Professional identity
    full_name = Column(String, nullable=False)
    professional_role = Column(SQLEnum(ProfessionalRole), nullable=False, index=True)
    specialty = Column(String, nullable=True)
    license_number = Column(String, nullable=True)  # Encrypted at rest
    license_jurisdiction = Column(String, nullable=True)
    license_country = Column(String, nullable=True, default="US")
    license_state = Column(String, nullable=True)
    npi_number = Column(String, nullable=True)  # US National Provider Identifier

    # Organization
    organization_name = Column(String, nullable=True)
    organization_verified = Column(Boolean, nullable=False, default=False)
    organization_address = Column(Text, nullable=True)
    organization_phone = Column(String, nullable=True)

    # Verification
    verification_status = Column(SQLEnum(ProfessionalVerificationStatus), nullable=False, default=ProfessionalVerificationStatus.UNVERIFIED, index=True)
    verification_method = Column(SQLEnum(VerificationMethod), nullable=True)
    verification_evidence = Column(JSONDict, nullable=True)  # Encrypted sensitive docs
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    verifier_notes = Column(Text, nullable=True)
    re_verification_due = Column(DateTime(timezone=True), nullable=True)

    # Expiry/suspension
    license_expiry = Column(DateTime(timezone=True), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    suspended_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    suspension_reason = Column(Text, nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    revocation_reason = Column(Text, nullable=True)

    # Public profile
    public_bio = Column(Text, nullable=True)
    public_profile_enabled = Column(Boolean, nullable=False, default=False)
    years_experience = Column(Integer, nullable=True)
    languages = Column(JSONList, nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    account = relationship("Account", back_populates="professional_profile", foreign_keys="ProfessionalProfile.account_id")
    role_links = relationship("ProfessionalRoleLink", back_populates="professional_profile")

    __table_args__ = (
        Index("ix_professional_profiles_role_status", "professional_role", "verification_status"),
        Index("ix_professional_profiles_license", "license_number"),
    )


class ProfessionalRoleLink(Base):
    """Links a ProfessionalProfile to a Person (care recipient) with a specific role.

    This defines the professional's authorization within a specific care context.
    """
    __tablename__ = "professional_role_links"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    professional_profile_id = Column(String, ForeignKey("professional_profiles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Role in this care context
    role = Column(String, nullable=False)  # e.g., "primary_physician", "consulting_neurologist"
    permissions = Column(JSONList, nullable=True)  # Explicit permissions
    restrictions = Column(JSONList, nullable=True)

    # Authorization
    authorized_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who granted access
    authorized_at = Column(DateTime(timezone=True), nullable=True)
    consent_record_id = Column(String, nullable=True)  # Link to consent record

    # Lifecycle
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    revocation_reason = Column(Text, nullable=True)

    # Relationships
    professional_profile = relationship("ProfessionalProfile", back_populates="role_links")
    person = relationship("Person", back_populates="professional_roles")

    __table_args__ = (
        UniqueConstraint("professional_profile_id", "person_id", "role", name="uq_professional_role_link"),
        Index("ix_professional_role_link_active", "professional_profile_id", "person_id", "is_active"),
    )


class StaffProfile(Base):
    """Internal SolenOS staff profile linked to an Account.

    Separate security domain from caregivers and professionals.
    """
    __tablename__ = "staff_profiles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)

    # Staff identity
    employee_id = Column(String, nullable=True, unique=True)
    display_name = Column(String, nullable=False)
    department = Column(String, nullable=True)
    role = Column(String, nullable=False)  # support, moderator, security_admin, privacy_admin, engineering, operations, founder

    # Permissions (least privilege)
    permissions = Column(JSONList, nullable=False, default=[])
    can_access_care_data = Column(Boolean, nullable=False, default=False)
    care_data_access_reason = Column(Text, nullable=True)
    care_data_access_expires = Column(DateTime(timezone=True), nullable=True)
    care_data_access_granted_by = Column(String, ForeignKey("accounts.id"), nullable=True)

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    hired_at = Column(DateTime(timezone=True), nullable=True)
    left_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    account = relationship("Account", back_populates="staff_profile", foreign_keys="StaffProfile.account_id")

    __table_args__ = (
        Index("ix_staff_profiles_role_active", "role", "is_active"),
    )


class Session(Base):
    """Active session with trust level and authorization context.

    Supports authenticated, anonymous, and elevated sessions.
    """
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=True, index=True)  # Null for anonymous
    session_type = Column(SQLEnum(SessionType), nullable=False, default=SessionType.AUTHENTICATED, index=True)

    # Session identification
    session_token = Column(String, nullable=False, unique=True, index=True)
    session_token_hash = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True, unique=True, index=True)
    refresh_token_hash = Column(String, nullable=True)
    device_id = Column(String, ForeignKey("devices.id"), nullable=True, index=True)

    # Anonymous session support
    anonymous_session_id = Column(String, nullable=True, unique=True, index=True)
    anonymous_record_ids = Column(JSONList, nullable=True)  # Records owned by this anonymous session

    # Authorization context (cached for performance)
    authorized_person_ids = Column(JSONList, nullable=True)
    authorized_caregiver_roles = Column(JSONDict, nullable=True)  # person_id -> role
    authorized_professional_roles = Column(JSONDict, nullable=True)

    # Security
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    trust_level = Column(SQLEnum(DeviceTrustLevel), nullable=False, default=DeviceTrustLevel.UNKNOWN)
    mfa_verified = Column(Boolean, nullable=False, default=False)
    elevated_until = Column(DateTime(timezone=True), nullable=True)  # For break-glass
    elevation_reason = Column(Text, nullable=True)
    elevation_approved_by = Column(String, ForeignKey("accounts.id"), nullable=True)

    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_activity_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_reason = Column(Text, nullable=True)

    # Relationships
    account = relationship("Account", back_populates="sessions", foreign_keys="Session.account_id")
    device = relationship("Device", back_populates="sessions")

    __table_args__ = (
        Index("ix_sessions_account_active", "account_id", "revoked_at"),
        Index("ix_sessions_anonymous_active", "anonymous_session_id", "revoked_at"),
        Index("ix_sessions_expires", "expires_at"),
        Index("ix_sessions_session_token_hash", "session_token_hash"),
        Index("ix_sessions_refresh_token_hash", "refresh_token_hash"),
    )


class Device(Base):
    """Registered device with trust level.

    Used for device management, session binding, and suspicious activity detection.
    """
    __tablename__ = "devices"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=True, index=True)  # Null for anonymous devices
    device_fingerprint = Column(String, nullable=False, unique=True, index=True)

    # Device info
    name = Column(String, nullable=True)  # User-assigned name
    platform = Column(String, nullable=True)  # iOS, Android, Windows, macOS, Linux
    browser = Column(String, nullable=True)
    app_version = Column(String, nullable=True)

    # Trust
    trust_level = Column(SQLEnum(DeviceTrustLevel), nullable=False, default=DeviceTrustLevel.UNKNOWN)
    trusted_at = Column(DateTime(timezone=True), nullable=True)
    trusted_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    mfa_verified_at = Column(DateTime(timezone=True), nullable=True)

    # Activity
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_ip = Column(String, nullable=True)
    last_seen_location = Column(String, nullable=True)  # Approximate geo

    # Security
    is_blocked = Column(Boolean, nullable=False, default=False)
    blocked_at = Column(DateTime(timezone=True), nullable=True)
    blocked_reason = Column(Text, nullable=True)

    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    account = relationship("Account", back_populates="devices", foreign_keys="Device.account_id")
    sessions = relationship("Session", back_populates="device")

    __table_args__ = (
        Index("ix_devices_account_trust", "account_id", "trust_level"),
    )


class AnonymousSessionRecord(Base):
    """Records owned by an anonymous session.

    Tracks what records belong to an anonymous session for secure claiming.
    """
    __tablename__ = "anonymous_session_records"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    anonymous_session_id = Column(String, nullable=False, index=True)

    # Owned records
    person_id = Column(String, ForeignKey("persons.id"), nullable=True, index=True)
    evidence_ids = Column(JSONList, nullable=True)
    care_event_ids = Column(JSONList, nullable=True)
    document_ids = Column(JSONList, nullable=True)
    caregiver_profile_ids = Column(JSONList, nullable=True)

    # Claim status
    claimed = Column(Boolean, nullable=False, default=False)
    claimed_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    claim_session_id = Column(String, ForeignKey("sessions.id"), nullable=True)

    # Lifecycle
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_anon_session_records_session", "anonymous_session_id", "claimed"),
        Index("ix_anon_session_records_expires", "expires_at"),
    )


class CaregiverRoleLink(Base):
    """Links an Account to a Person (care recipient) with a caregiver role.

    This defines the caregiver's authorization within a specific care context.
    """
    __tablename__ = "caregiver_role_links"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Role in this care context
    role = Column(String, nullable=False)  # e.g., "primary", "secondary", "backup"
    permissions = Column(JSONList, nullable=True)  # Explicit permissions
    restrictions = Column(JSONList, nullable=True)  # Explicit restrictions

    # Authorization
    authorized_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)  # Who granted access
    authorized_at = Column(DateTime(timezone=True), nullable=True)
    consent_record_id = Column(String, nullable=True)  # Link to consent record

    # Lifecycle
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String, ForeignKey("accounts.id"), nullable=True)
    revocation_reason = Column(Text, nullable=True)

    # Relationships
    account = relationship("Account", foreign_keys=[account_id])
    person = relationship("Person", foreign_keys=[person_id])
    authorizer = relationship("Account", foreign_keys=[authorized_by_account_id])
    revoker = relationship("Account", foreign_keys=[revoked_by])

    __table_args__ = (
        UniqueConstraint("account_id", "person_id", "role", name="uq_caregiver_role_link"),
        Index("ix_caregiver_role_link_active", "account_id", "person_id", "is_active"),
    )
