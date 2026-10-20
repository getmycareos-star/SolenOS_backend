"""Persisted daily capture payloads and uploaded evidence bytes."""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, JSON, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class DailyCapture(Base):
    __tablename__ = "daily_captures"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    recorded_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    medications = Column(JSON, nullable=False, default=list)
    vitals = Column(JSON, nullable=False, default=list)
    scales = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=False, default="")
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvidenceFile(Base):
    __tablename__ = "evidence_files"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=False, unique=True, index=True)
    uploaded_by_account_id = Column(String, ForeignKey("auth_accounts.id"), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    content = Column(LargeBinary, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
