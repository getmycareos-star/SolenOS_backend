import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class CareReminder(Base):
    __tablename__ = "care_reminders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    action_id = Column(String, ForeignKey("actions.id"), nullable=True, unique=True, index=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    title = Column(String(180), nullable=False)
    note = Column(Text, nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="open", server_default="open", index=True)
    snoozed_at = Column(DateTime(timezone=True), nullable=True)
    snoozed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    snooze_count = Column(Integer, nullable=False, default=0, server_default="0")
    delivery_generation = Column(Integer, nullable=False, default=0, server_default="0")
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    completion_note = Column(Text, nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
