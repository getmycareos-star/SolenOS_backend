from sqlalchemy.orm import Session
from app.crud.notification import (
    get_notification_by_id,
    get_notifications_by_recipient,
    create_notification,
    update_notification,
    get_unread_count_by_recipient
)
from app.schemas.notification import NotificationCreate, NotificationUpdate

def get_notification(db: Session, notification_id: int):
    return get_notification_by_id(db, notification_id)

def get_notifications(
    db: Session, 
    recipient_person_id: str, 
    skip: int = 0, 
    limit: int = 100,
    unread_only: bool = False
):
    return get_notifications_by_recipient(db, recipient_person_id, skip, limit, unread_only)

def create_notification_service(db: Session, notification: NotificationCreate):
    return create_notification(db, notification)

def update_notification_service(db: Session, notification_id: int, notification_update: NotificationUpdate):
    return update_notification(db, notification_id, notification_update)

def get_unread_count(db: Session, recipient_person_id: str):
    return get_unread_count_by_recipient(db, recipient_person_id)
