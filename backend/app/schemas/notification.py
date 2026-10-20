"""Notification request and response schemas aligned with the mapped model."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class NotificationBase(BaseModel):
    recipient_person_id: str
    type: str
    title: str
    message: str
    is_read: bool = False
    related_entity_type: Optional[str] = None
    # Related entity IDs may be UUID strings or legacy numeric IDs.
    related_entity_id: Optional[str] = None
    priority: str = "medium"
    category: Optional[str] = None
    notification_metadata: Optional[dict[str, Any]] = None


class NotificationCreate(NotificationBase):
    pass


class NotificationUpdate(BaseModel):
    is_read: Optional[bool] = None
    archived_at: Optional[datetime] = None


class NotificationInDBBase(NotificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    read_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None


class Notification(NotificationInDBBase):
    pass


class NotificationUnreadCount(BaseModel):
    unread_count: int
