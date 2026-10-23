from sqlalchemy import Column, String, DateTime, Boolean, Integer, Text, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class CareReminder(Base):
    __tablename__ = "care_reminders"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    action_id = Column(String, ForeignKey("actions.id"), nullable=True, index=True)
    handoff_id = Column(String, ForeignKey("care_handoffs.id"), nullable=True, index=True, unique=True)
    recipient_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True, index=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    timezone = Column(String, nullable=False)
    recurrence = Column(String, nullable=False, default="once")  # once, daily, weekly
    next_run_at = Column(DateTime(timezone=True), nullable=False, index=True)
    local_time = Column(String, nullable=False)  # HH:MM wall-clock time for recurring schedules
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    max_attempts = Column(Integer, nullable=False, default=5)
    retry_delay_seconds = Column(Integer, nullable=False, default=60)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ReminderDelivery(Base):
    __tablename__ = "reminder_deliveries"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    reminder_id = Column(String, ForeignKey("care_reminders.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    idempotency_key = Column(String, nullable=False, unique=True)
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)  # pending, retry, delivered, failed
    attempt_count = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, index=True)
    last_error = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("reminder_id", "scheduled_for", name="uq_reminder_delivery_occurrence"),
        Index("ix_reminder_delivery_due", "status", "next_attempt_at"),
    )



class ReminderDeliveryAttempt(Base):
    __tablename__ = "reminder_delivery_attempts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    delivery_id = Column(String, ForeignKey("reminder_deliveries.id"), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    channel = Column(String, nullable=False, default="in_app")
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String, nullable=False, default="pending")  # pending, delivered, retry, failed
    error_type = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=False, unique=True)
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_number", name="uq_reminder_attempt_number"),
    )
