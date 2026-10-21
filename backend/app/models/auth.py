from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    token_version = Column(String, nullable=False, default="0", server_default="'0'")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CareMembership(Base):
    __tablename__ = "care_memberships"
    __table_args__ = (
        UniqueConstraint("account_id", "person_id", "caregiver_id", name="uq_care_membership_account_person_caregiver"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    role = Column(String, nullable=False, default="caregiver", server_default="caregiver")
    permissions = Column(Text, nullable=False, default='["read","write"]', server_default='["read","write"]')
    status = Column(String, nullable=False, default="active", server_default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CareInvitation(Base):
    __tablename__ = "care_invitations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    token_hash = Column(String, nullable=False, unique=True, index=True)
    email = Column(String, nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    invited_by_account_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    permissions = Column(Text, nullable=False, default='["read"]', server_default='["read"]')
    status = Column(String, nullable=False, default="pending", server_default="pending")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
