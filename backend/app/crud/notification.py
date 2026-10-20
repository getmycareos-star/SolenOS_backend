"""CRUD operations for persisted notifications."""
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.notification_enums import (
    NotificationCategory,
    NotificationPriority,
    NotificationType,
)
from app.models.notification import Notification
from app.schemas.notification import NotificationCreate, NotificationUpdate


def get_notification_by_id(db: Session, notification_id: str):
    return db.query(Notification).filter(Notification.id == str(notification_id)).first()


def get_notifications_by_recipient(
    db: Session,
    recipient_person_id: str,
    skip: int = 0,
    limit: int = 100,
    unread_only: bool = False,
):
    query = db.query(Notification).filter(
        Notification.recipient_person_id == recipient_person_id
    )
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    return query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()


def _coerce_enum(enum_type, value, default):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return default


def _category_for(notification_type: NotificationType) -> NotificationCategory:
    mapping = {
        NotificationType.MEDICATION_REMINDER: NotificationCategory.MEDICATION,
        NotificationType.APPOINTMENT_REMINDER: NotificationCategory.APPOINTMENT,
        NotificationType.EMERGENCY_ALERT: NotificationCategory.EMERGENCY,
        NotificationType.WELLBEING_CHECKIN: NotificationCategory.WELLBEING,
        NotificationType.SHARE_REQUEST: NotificationCategory.SHARE,
        NotificationType.SHARE_RESPONSE: NotificationCategory.SHARE,
        NotificationType.SUMMARY_READY: NotificationCategory.SUMMARY,
        NotificationType.CARE_UPDATE: NotificationCategory.CLINICAL,
    }
    return mapping.get(notification_type, NotificationCategory.ADMINISTRATIVE)


def create_notification(db: Session, notification: NotificationCreate):
    raw_type = notification.type
    notification_type = _coerce_enum(
        NotificationType, raw_type, NotificationType.OTHER
    )
    category = (
        _coerce_enum(NotificationCategory, notification.category, None)
        if notification.category
        else None
    ) or _category_for(notification_type)
    priority = _coerce_enum(
        NotificationPriority, notification.priority, NotificationPriority.MEDIUM
    )
    metadata = dict(notification.notification_metadata or {})
    if notification_type == NotificationType.OTHER and raw_type != NotificationType.OTHER.value:
        metadata.setdefault("original_type", raw_type)

    db_notification = Notification(
        recipient_person_id=notification.recipient_person_id,
        notification_type=notification_type,
        category=category,
        title=notification.title,
        message=notification.message,
        is_read=notification.is_read,
        related_entity_type=notification.related_entity_type,
        related_entity_id=(
            str(notification.related_entity_id)
            if notification.related_entity_id is not None
            else None
        ),
        priority=priority,
        notification_metadata=metadata or None,
    )
    db.add(db_notification)
    db.commit()
    db.refresh(db_notification)
    return db_notification


def update_notification(db: Session, notification_id: str, notification_update: NotificationUpdate):
    db_notification = get_notification_by_id(db, notification_id)
    if db_notification:
        update_data = notification_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(db_notification, key, value)
        db.commit()
        db.refresh(db_notification)
    return db_notification


def get_unread_count_by_recipient(db: Session, recipient_person_id: str):
    return db.query(Notification).filter(
        and_(
            Notification.recipient_person_id == recipient_person_id,
            Notification.is_read.is_(False),
        )
    ).count()
