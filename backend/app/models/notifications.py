import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CareNotification(Base):
    __tablename__ = "care_notifications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(180), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(40), nullable=False, default="care_update")
    priority = Column(String(20), nullable=False, default="normal")
    source_event_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)


class CareNotificationPreference(Base):
    __tablename__ = "care_notification_preferences"
    __table_args__ = (
        UniqueConstraint("person_id", "caregiver_id", name="uq_care_notification_preferences_actor"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    quiet_hours_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    reminders_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
