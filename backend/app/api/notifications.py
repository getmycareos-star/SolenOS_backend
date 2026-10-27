from datetime import datetime, timezone
import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.notifications import CareNotification, CareNotificationPreference
from app.models.privacy_delivery import AuditLogEvent, NotificationReadReceipt

router = APIRouter(prefix="/notifications", tags=["care notifications"])
POLICY_VERSION = "notification-access-v1"


def _scope(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _audit(db: Session, request: Request, person_id: str, caregiver_id: str,
           action: str, target_type: str, target_id: str, metadata: dict | None = None) -> None:
    db.add(AuditLogEvent(
        id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
        person_id=person_id, caregiver_id=caregiver_id, action=action,
        target_type=target_type, target_id=target_id,
        occurred_at=datetime.now(timezone.utc), outcome="allowed",
        request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
        policy_version=POLICY_VERSION, record_metadata=metadata or {},
    ))


class NotificationPreferenceUpdate(BaseModel):
    quiet_hours_enabled: bool
    reminders_enabled: bool


@router.get("")
def list_notifications(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    query = db.query(CareNotification).filter(
        CareNotification.person_id == person_id,
        CareNotification.caregiver_id == caregiver_id,
    )
    if unread_only:
        query = query.filter(CareNotification.read_at.is_(None))
    rows = query.order_by(CareNotification.created_at.desc(), CareNotification.id.asc()).limit(limit).all()
    return [{
        "id": row.id, "title": row.title, "body": row.body,
        "category": row.category, "priority": row.priority,
        "source_event_id": row.source_event_id, "created_at": row.created_at,
        "read_at": row.read_at, "is_read": row.read_at is not None,
    } for row in rows]


@router.patch("/{notification_id}/read")
def mark_notification_read(notification_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    notification = db.query(CareNotification).filter(
        CareNotification.id == notification_id,
        CareNotification.person_id == person_id,
        CareNotification.caregiver_id == caregiver_id,
    ).first()
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    now = datetime.now(timezone.utc)
    if notification.read_at is None:
        notification.read_at = now
        db.add(NotificationReadReceipt(
            id=str(uuid.uuid4()), actor_id=caregiver_id, subject_id=person_id,
            person_id=person_id, caregiver_id=caregiver_id,
            notification_id=notification.id, reader_id=caregiver_id,
            read_at=now, client=(request.headers.get("user-agent") or "")[:255],
            request_id=request.headers.get("x-request-id") or str(uuid.uuid4()),
            policy_version=POLICY_VERSION,
        ))
        _audit(db, request, person_id, caregiver_id, "notification.read", "notification", notification.id)
    db.commit()
    db.refresh(notification)
    return {"id": notification.id, "is_read": notification.read_at is not None, "read_at": notification.read_at}


@router.get("/preferences")
def get_notification_preferences(request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    preference = db.query(CareNotificationPreference).filter(
        CareNotificationPreference.person_id == person_id,
        CareNotificationPreference.caregiver_id == caregiver_id,
    ).first()
    if preference is None:
        return {"quiet_hours_enabled": False, "reminders_enabled": True, "configured": False}
    return {
        "quiet_hours_enabled": preference.quiet_hours_enabled,
        "reminders_enabled": preference.reminders_enabled,
        "configured": True,
        "updated_at": preference.updated_at,
    }


@router.put("/preferences")
def update_notification_preferences(
    payload: NotificationPreferenceUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    preference = db.query(CareNotificationPreference).filter(
        CareNotificationPreference.person_id == person_id,
        CareNotificationPreference.caregiver_id == caregiver_id,
    ).first()
    now = datetime.now(timezone.utc)
    if preference is None:
        preference = CareNotificationPreference(
            id=str(uuid.uuid4()), person_id=person_id, caregiver_id=caregiver_id,
            quiet_hours_enabled=payload.quiet_hours_enabled,
            reminders_enabled=payload.reminders_enabled, updated_at=now,
        )
        db.add(preference)
    else:
        preference.quiet_hours_enabled = payload.quiet_hours_enabled
        preference.reminders_enabled = payload.reminders_enabled
        preference.updated_at = now
    _audit(db, request, person_id, caregiver_id, "notification.preferences.update",
           "notification_preferences", caregiver_id,
           {"quiet_hours_enabled": payload.quiet_hours_enabled,
            "reminders_enabled": payload.reminders_enabled})
    db.commit()
    db.refresh(preference)
    return {
        "quiet_hours_enabled": preference.quiet_hours_enabled,
        "reminders_enabled": preference.reminders_enabled,
        "configured": True,
        "updated_at": preference.updated_at,
    }
