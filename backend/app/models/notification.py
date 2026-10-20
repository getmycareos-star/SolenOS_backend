"""
Notification Models for SolenOS.

Represents notifications that can be sent to caregivers, professionals,
or other system users to inform them about important events, updates,
or actions required.
"""

from sqlalchemy import Column, String, DateTime, Boolean, Text, ForeignKey, Integer, Enum as SQLEnum, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.core.database import Base
from app.core.types import JSONList, JSONDict
from app.core.notification_enums import (
    NotificationType,
    NotificationPriority,
    NotificationStatus,
    NotificationCategory,
    DeliveryMethod,
    NotificationActionType
)


class Notification(Base):
    """Notification sent to inform users about events, updates, or required actions."""
    __tablename__ = "notification"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    recipient_person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    
    # Notification identification and routing
    notification_type = Column(SQLEnum(NotificationType), nullable=False, index=True)
    category = Column(SQLEnum(NotificationCategory), nullable=False, index=True)
    priority = Column(SQLEnum(NotificationPriority), nullable=False, default=NotificationPriority.MEDIUM, index=True)
    
    # Content
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    message_template = Column(String(50), nullable=True)  # Template used if applicable
    template_variables = Column(JSONDict, nullable=True)  # Variables used in template
    
    # Status and lifecycle
    status = Column(SQLEnum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING, index=True)
    is_read = Column(Boolean, default=False, nullable=False, index=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # When notification expires/no longer relevant
    
    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)  # When actually sent/delivered
    delivered_at = Column(DateTime(timezone=True), nullable=True)  # When confirmed delivered
    
    # Related entities (what this notification is about)
    related_entity_type = Column(String(50), nullable=True, index=True)  # e.g., "care_event", "situation", "medication"
    related_entity_id = Column(String, nullable=True, index=True)  # Flexible ID for various entity types
    related_entity_secondary_type = Column(String(50), nullable=True)  # For secondary relationships
    related_entity_secondary_id = Column(String, nullable=True)
    
    # Sender and routing
    sender_type = Column(String(50), nullable=True)  # e.g., "system", "caregiver", "professional"
    sender_id = Column(String, nullable=True)  # ID of the sender
    sender_name = Column(String, nullable=True)  # Display name of sender
    
    # Delivery information
    delivery_method = Column(SQLEnum(DeliveryMethod), nullable=True)  # How it was/will be delivered
    delivery_status = Column(String(50), nullable=True)  # Status of delivery attempt
    delivery_attempts = Column(Integer, nullable=False, default=0)
    last_delivery_attempt = Column(DateTime(timezone=True), nullable=True)
    
    # Actions and responses
    requires_action = Column(Boolean, nullable=False, default=False)
    action_type = Column(SQLEnum(NotificationActionType), nullable=True)  # Type of action required
    action_deadline = Column(DateTime(timezone=True), nullable=True)  # When action should be completed by
    action_taken = Column(Boolean, nullable=False, default=False)
    action_taken_at = Column(DateTime(timezone=True), nullable=True)
    action_taken_by = Column(String, ForeignKey("persons.id"), nullable=True)  # Who took the action
    
    # Escalation and routing
    escalation_level = Column(Integer, nullable=False, default=0)  # 0 = no escalation, 1+ = escalated
    escalation_timeout = Column(Integer, nullable=True)  # Minutes before escalating
    escalated_to = Column(String, ForeignKey("persons.id"), nullable=True)  # Who it was escalated to
    escalation_reason = Column(Text, nullable=True)
    
    # Grouping and threading
    notification_group_id = Column(String, nullable=True, index=True)  # For grouping related notifications
    thread_id = Column(String, nullable=True, index=True)  # For conversation threads
    is_thread_starter = Column(Boolean, nullable=False, default=False)
    reply_to_notification_id = Column(String, ForeignKey("notification.id"), nullable=True)
    
    # Context and metadata
    context_json = Column(JSONDict, nullable=True)  # Additional context information
    notification_metadata = Column(JSONDict, nullable=True)  # Flexible metadata field
    tags = Column(JSONList, nullable=True)  # Tags for categorization and filtering
    
    # Privacy and security
    sensitivity_level = Column(String(20), nullable=True)  # e.g., "low", "medium", "high", "confidential"
    access_restrictions = Column(JSONList, nullable=True)  # Specific access restrictions
    
    # Acknowledgement and feedback
    requires_acknowledgement = Column(Boolean, nullable=False, default=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by = Column(String, ForeignKey("persons.id"), nullable=True)
    feedback_rating = Column(Integer, nullable=True)  # 1-5 rating if feedback requested
    feedback_comment = Column(Text, nullable=True)
    
    # Relationships
    recipient = relationship("Person", foreign_keys=[recipient_person_id])
    action_taker = relationship("Person", foreign_keys=[action_taken_by])
    escalated_to_person = relationship("Person", foreign_keys=[escalated_to])
    replied_to = relationship("Notification", foreign_keys=[reply_to_notification_id])
    
    __table_args__ = (
        Index("ix_notification_recipient_status", "recipient_person_id", "status"),
        Index("ix_notification_priority_created", "priority", "created_at"),
        Index("ix_notification_type_category", "notification_type", "category"),
        Index("ix_notification_related_entity", "related_entity_type", "related_entity_id"),
        Index("ix_notification_thread", "thread_id"),
        Index("ix_notification_group", "notification_group_id"),
    )


class NotificationTemplate(Base):
    """Reusable templates for notifications."""
    __tablename__ = "notification_templates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_type = Column(SQLEnum(NotificationType), nullable=False, index=True)
    category = Column(SQLEnum(NotificationCategory), nullable=False, index=True)
    title_template = Column(String(255), nullable=False)
    message_template = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    variables = Column(JSONList, nullable=True)  # List of variable names used in template
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    __table_args__ = (
        Index("ix_notification_template_type_category", "notification_type", "category"),
    )


class NotificationDeliveryLog(Base):
    """Log of notification delivery attempts."""
    __tablename__ = "notification_delivery_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_id = Column(String, ForeignKey("notification.id"), nullable=False, index=True)
    
    attempt_number = Column(Integer, nullable=False)
    delivery_method = Column(SQLEnum(DeliveryMethod), nullable=False)
    status = Column(String(50), nullable=False)  # success, failed, pending
    error_message = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Delivery details
    recipient_address = Column(String, nullable=True)  # Email, phone number, etc.
    delivery_provider = Column(String, nullable=True)  # e.g., "twilio", "sendgrid", "fcm"
    provider_message_id = Column(String, nullable=True)  # ID from delivery provider
    provider_response = Column(Text, nullable=True)  # Raw response from provider
    
    # Relationships
    notification = relationship("Notification", foreign_keys=[notification_id])
    
    __table_args__ = (
        Index("ix_notification_delivery_notification", "notification_id"),
        Index("ix_notification_delivery_attempted", "attempted_at"),
    )


# Update Person model to add back-reference
# In app/models/identity.py, Person class should have:
# notifications_received = relationship("Notification", foreign_keys="Notification.recipient_person_id")
