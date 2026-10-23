"""Durable, idempotent reminder queue and append-only delivery attempts."""
import uuid
from sqlalchemy import Column, String, DateTime, Text, Integer, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.core.database import Base


class CareReminder(Base):
    __tablename__ = "care_reminders"
    __table_args__ = (
        UniqueConstraint("person_id", "idempotency_key", name="uq_care_reminders_person_idempotency"),
        Index("ix_care_reminders_due_status", "status", "next_attempt_at"),
        Index("ix_care_reminders_person_due", "person_id", "due_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    action_id = Column(String, ForeignKey("actions.id"), nullable=True, index=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String, nullable=False, default="UTC")
    status = Column(String, nullable=False, default="scheduled")
    idempotency_key = Column(String, nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ReminderDeliveryAttempt(Base):
    __tablename__ = "reminder_delivery_attempts"
    __table_args__ = (
        Index("ix_reminder_delivery_attempts_reminder_created", "reminder_id", "attempted_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    reminder_id = Column(String, ForeignKey("care_reminders.id"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    outcome = Column(String, nullable=False)
    provider = Column(String, nullable=True)
    provider_receipt_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
