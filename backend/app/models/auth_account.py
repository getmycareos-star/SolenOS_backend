import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class AuthAccount(Base):
    __tablename__ = "auth_accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(254), nullable=False, unique=True, index=True)
    password_hash = Column(Text, nullable=False)
    display_name = Column(String(120), nullable=False)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, unique=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    token_digest = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
