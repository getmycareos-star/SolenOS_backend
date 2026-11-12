import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CaregiverAccessGrant(Base):
    """Server-enforced membership for one caregiver in one care record.

    A bearer session is valid only while this grant remains active. Profile
    labels and client-selected caregiver IDs are never authorization.
    """
    __tablename__ = "caregiver_access_grants"
    __table_args__ = (
        UniqueConstraint("person_id", "caregiver_id", name="uq_caregiver_access_person_caregiver"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_profile_id = Column(String, ForeignKey("caregiver_profiles.id", ondelete="SET NULL"), nullable=True, index=True)
    role = Column(String(32), nullable=False, default="family")
    status = Column(String(16), nullable=False, default="active", index=True)
    can_manage_members = Column(Boolean, nullable=False, default=False)
    granted_by_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    granted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CaregiverInvitation(Base):
    """One-time, recipient-bound invitation pre-authorised by a record manager."""
    __tablename__ = "caregiver_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_caregiver_invitations_token_hash"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    email_normalized = Column(String(320), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False)
    invited_by_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    role = Column(String(32), nullable=False, default="family")
    status = Column(String(16), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CaregiverResponsibilityTransfer(Base):
    """Explicit, single-responsibility transfer with recipient acceptance."""
    __tablename__ = "caregiver_responsibility_transfers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    source_responsibility_id = Column(String, ForeignKey("caregiver_responsibilities.id", ondelete="RESTRICT"), nullable=False, index=True)
    from_caregiver_profile_id = Column(String, ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"), nullable=False)
    to_caregiver_profile_id = Column(String, ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"), nullable=False)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    related_record_type = Column(String(40), nullable=True)
    related_record_id = Column(String(160), nullable=True)
    reason = Column(String(1000), nullable=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)


class CaregiverPrimaryTransfer(Base):
    """Primary caregiver authority transfer; does not move other responsibilities."""
    __tablename__ = "caregiver_primary_transfers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    from_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="RESTRICT"), nullable=False)
    to_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="RESTRICT"), nullable=False)
    reason = Column(String(1000), nullable=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_by_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
