import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.sql import func

from app.core.database import Base


class AnonymousSession(Base):
    """Server-issued bearer capability scoped to one care record.

    Only a SHA-256 digest of the opaque token is persisted. The raw token is
    returned once at creation and must be treated as a secret by the client.
    """
    __tablename__ = "anonymous_sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=True, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
