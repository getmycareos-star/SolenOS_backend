import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class CaregiverAccount(Base):
    """Email/password account whose bearer sessions remain scoped to one care record."""
    __tablename__ = "caregiver_accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(254), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, unique=True, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, unique=True, index=True)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    mfa_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    person = relationship("Person")
    caregiver = relationship("Caregiver")
