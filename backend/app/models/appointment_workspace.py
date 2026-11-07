import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, JSON, Text, UniqueConstraint
from sqlalchemy.sql import func
from app.core.database import Base


class AppointmentWorkspaceRecord(Base):
    __tablename__ = "appointment_workspace_records"
    __table_args__ = (UniqueConstraint("person_id", "created_by_caregiver_id", "idempotency_key", name="uq_appointment_workspace_person_caregiver_idempotency"),)
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String(36), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    appointment_id = Column(String(36), ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    updated_by_caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(40), nullable=False, default="preparation")
    version_number = Column(Integer, nullable=False, default=1)
    payload = Column(JSON, nullable=False)
    last_revision_reason = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    idempotency_request_sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AppointmentWorkspaceRevision(Base):
    __tablename__ = "appointment_workspace_revisions"
    __table_args__ = (UniqueConstraint("record_id", "changed_by_caregiver_id", "idempotency_key", name="uq_appointment_workspace_revision_caregiver_idempotency"),)
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    record_id = Column(String(36), ForeignKey("appointment_workspace_records.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String(36), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    change_reason = Column(Text, nullable=True)
    changed_by_caregiver_id = Column(String(36), ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    idempotency_request_sha256 = Column(String(64), nullable=True)
    changed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
