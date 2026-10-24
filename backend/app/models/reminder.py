import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class CareReminder(Base):
    __tablename__ = "care_reminders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    title = Column(String(180), nullable=False)
    note = Column(Text, nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="open", index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    completion_note = Column(Text, nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
