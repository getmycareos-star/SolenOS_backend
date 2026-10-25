import uuid
from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.sql import func
from app.core.database import Base

class ProfessionalAccount(Base):
    """Professional identity, separate from caregiver care-record accounts."""
    __tablename__ = "professional_accounts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(254), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(160), nullable=False)
    professional_title = Column(String(160), nullable=False)
    organization = Column(String(200), nullable=True)
    credentials = Column(String(300), nullable=True)
    specialties = Column(Text, nullable=True)
    biography = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="pending_review", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class ProfessionalSession(Base):
    """Bearer session accepted only by professional-auth endpoints."""
    __tablename__ = "professional_sessions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    professional_id = Column(String, ForeignKey("professional_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
