"""Encrypted-at-rest-by-provider privacy export artifact metadata and archive bytes.

The database stores the short-lived archive so delivery is authenticated and
independent of a public object URL. Database/provider encryption remains required.
"""
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.sql import func

from app.core.database import Base


class PrivacyExportArtifact(Base):
    __tablename__ = "privacy_export_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    request_id = Column(
        String(36),
        ForeignKey("data_subject_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    person_id = Column(String(255), nullable=False, index=True)
    caregiver_id = Column(String(255), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content = Column(LargeBinary, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    downloaded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
