import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CareNotification(Base):
    __tablename__ = "care_notifications"

    id = Column(String, primary_key=True)
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
    quiet_hours_start = Column(String(5), nullable=False, default="21:00", server_default="21:00")
    quiet_hours_end = Column(String(5), nullable=False, default="08:00", server_default="08:00")
    timezone_name = Column(String(64), nullable=False, default="UTC", server_default="UTC")
    reminders_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class CarePushSubscription(Base):
    __tablename__ = "care_push_subscriptions"
    __table_args__ = (
        UniqueConstraint("endpoint_hash", name="uq_care_push_subscriptions_endpoint_hash"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint_hash = Column(String(64), nullable=False, index=True)
    encrypted_subscription = Column(Text, nullable=False)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class CarePushDelivery(Base):
    __tablename__ = "care_push_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", "subscription_id", name="uq_care_push_delivery_target"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_id = Column(String, ForeignKey("care_notifications.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(String, ForeignKey("care_push_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending", server_default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    provider_accepted_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)
