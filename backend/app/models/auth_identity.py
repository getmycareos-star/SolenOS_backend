"""Authentication identity tables separate from existing care-recipient models."""
import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base

class AuthAccount(Base):
    __tablename__ = "auth_accounts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_staff = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    token_digest = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

class AccountPersonAccess(Base):
    """Explicit account-to-person authorization; never inferred from request IDs."""
    __tablename__ = "auth_account_person_access"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    role = Column(String, nullable=False)
    permissions = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True)
    granted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    granted_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=True)
    __table_args__ = (
        UniqueConstraint("account_id", "person_id", name="uq_auth_account_person_access"),
        Index("ix_auth_access_active_person", "person_id", "is_active"),
    )


class CaregiverAccountLink(Base):
    """Verified account-to-caregiver identity link created by accepting an owner invitation."""
    __tablename__ = "auth_caregiver_account_links"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, unique=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    linked_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("account_id", "caregiver_id", name="uq_auth_caregiver_account_link"),
    )


class CaregiverInvitation(Base):
    """Single-use, expiring invitation proving control of a caregiver invitation token."""
    __tablename__ = "auth_caregiver_invitations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    token_digest = Column(String(64), nullable=False, unique=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    invited_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False)
    accepted_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=True)
    permissions = Column(JSON, nullable=False, default=lambda: ["read", "write"])
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CaregiverProfileAccountLink(Base):
    """Verified account-to-caregiver-intelligence-profile identity link."""
    __tablename__ = "auth_caregiver_profile_account_links"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    caregiver_profile_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, unique=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    linked_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("account_id", "caregiver_profile_id", name="uq_auth_caregiver_profile_account_link"),
    )


class CaregiverProfileInvitation(Base):
    """Single-use invitation for a caregiver-intelligence profile identity."""
    __tablename__ = "auth_caregiver_profile_invitations"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    token_digest = Column(String(64), nullable=False, unique=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_profile_id = Column(String, ForeignKey("caregiver_profiles.id"), nullable=False, index=True)
    invited_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False)
    accepted_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=True)
    permissions = Column(JSON, nullable=False, default=lambda: ["read", "write"])
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
