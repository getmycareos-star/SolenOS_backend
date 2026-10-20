from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.models.notification import Notification
from app.schemas.notification import NotificationCreate, NotificationUpdate

def get_notification_by_id(db: Session, notification_id: int):
    return db.query(Notification).filter(Notification.id == notification_id).first()

def get_notifications_by_recipient(
    db: Session, 
    recipient_person_id: str, 
    skip: int = 0, 
    limit: int = 100,
    unread_only: bool = False
):
    query = db.query(Notification).filter(Notification.recipient_person_id == recipient_person_id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    return query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()

def create_notification(db: Session, notification: NotificationCreate):
    db_notification = Notification(
        recipient_person_id=notification.recipient_person_id,
        type=notification.type,
        title=notification.title,
        message=notification.message,
        is_read=notification.is_read,
        related_entity_type=notification.related_entity_type,
        related_entity_id=notification.related_entity_id,
        priority=notification.priority
    )
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification

def update_notification(db: Session, notification_id: int, notification_update: NotificationUpdate):
    db_notification = get_notification_by_id(db, notification_id)
    if db_notification:
        update_data = notification_update.dict(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_notification, key, value)
        db.commit()
        db.refresh(db_notification)
    return db_notification

def get_unread_count_by_recipient(db: Session, recipient_person_id: str):
    return db.query(Notification).filter(
        and_(
            Notification.recipient_person_id == recipient_person_id,
            Notification.is_read == False
        )
    ).count()
