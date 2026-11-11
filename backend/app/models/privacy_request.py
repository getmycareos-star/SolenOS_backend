"""ORM mapping for the existing privacy request lifecycle table."""
import uuid

from sqlalchemy import Column, DateTime, JSON, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class DataSubjectRequest(Base):
    __tablename__ = "data_subject_requests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    request_type = Column(Text, nullable=False)
    request_status = Column(Text, nullable=False)
    requested_at = Column(DateTime(timezone=True), nullable=False, index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_code = Column(Text, nullable=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(255), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
