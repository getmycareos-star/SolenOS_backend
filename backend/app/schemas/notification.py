from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum as PyEnum

class NotificationType(str, PyEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"

class NotificationBase(BaseModel):
    recipient_person_id: str
    type: str
    title: str
    message: str
    is_read: bool = False
    related_entity_type: Optional[str] = None
    related_entity_id: Optional[int] = None
    priority: str = "medium"

class NotificationCreate(NotificationBase):
    pass

class NotificationUpdate(BaseModel):
    is_read: Optional[bool] = None
    archived_at: Optional[datetime] = None

class NotificationInDBBase(NotificationBase):
    id: int
    created_at: datetime
    read_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None

    class Config:
        orm_mode = True

class Notification(NotificationInDBBase):
    pass

class NotificationUnreadCount(BaseModel):
    unread_count: int
