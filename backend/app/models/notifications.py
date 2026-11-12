import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.sql import func

from app.core.database import Base


class CareNotification(Base):
    __tablename__ = "care_notifications"
    __table_args__ = (
        Index(
            "uq_care_notifications_active_source",
            "person_id", "caregiver_id", "category", "source_event_id",
            unique=True,
            postgresql_where=text("status = 'active' AND category IN ('care_change', 'care_attention') AND source_event_id IS NOT NULL"),
            sqlite_where=text("status = 'active' AND category IN ('care_change', 'care_attention') AND source_event_id IS NOT NULL"),
        ),
        Index("ix_care_notifications_scope_status_created", "person_id", "caregiver_id", "status", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(180), nullable=False)
    body = Column(Text, nullable=False)
    category = Column(String(40), nullable=False, default="care_update")
    priority = Column(String(20), nullable=False, default="normal")
    source_event_id = Column(String, nullable=True)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_reason = Column(String(80), nullable=True)
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


class NotificationDeliveryAttempt(Base):
    """Append-only record of each provider delivery attempt."""

    __tablename__ = "notification_delivery_attempts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    notification_id = Column(String(255), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    channel = Column(Text, nullable=False)
    attempted_at = Column(DateTime(timezone=True), nullable=False, index=True)
    result = Column(Text, nullable=False)
    failure_code = Column(Text, nullable=True)
    provider_reference = Column(Text, nullable=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class DeliveryFailureEvent(Base):
    """Durable, redacted record of one failed delivery attempt."""
    __tablename__ = "delivery_failure_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id = Column(String(255), nullable=True, index=True)
    subject_id = Column(String(255), nullable=True, index=True)
    person_id = Column(String(255), nullable=True, index=True)
    caregiver_id = Column(String(255), nullable=True, index=True)
    delivery_type = Column(Text, nullable=False)
    delivery_id = Column(String(255), nullable=False, index=True)
    recipient_id = Column(String(255), nullable=True, index=True)
    failure_code = Column(Text, nullable=False)
    failure_detail_redacted = Column(Text, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    retryable = Column(Boolean, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True, index=True)
    request_id = Column(String(255), nullable=True, index=True)
    correlation_id = Column(String(255), nullable=True, index=True)
    policy_version = Column(String(100), nullable=True)
    record_metadata = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
