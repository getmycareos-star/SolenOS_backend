import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.reminder import CareReminder
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.privacy_delivery import AuditLogEvent
from app.models.notifications import CareNotification, CarePushDelivery
from app.schemas.reminder import CareReminderAssign, CareReminderComplete, CareReminderCreate, CareReminderRead, CareReminderSnooze

router = APIRouter(tags=["care reminders"])


def _cancel_pending_reminder_pushes(db: Session, reminder_id: str) -> None:
    reminder_notification_ids = db.query(CareNotification.id).filter(
        CareNotification.source_event_id == reminder_id,
        CareNotification.category == "reminder_due",
    ).subquery()
    db.query(CarePushDelivery).filter(
        CarePushDelivery.notification_id.in_(reminder_notification_ids),
        CarePushDelivery.status == "pending",
    ).update(
        {CarePushDelivery.status: "cancelled"},
        synchronize_session=False,
    )


def _session_scope(request: Request, db: Session) -> tuple[str, str]:
    # FastAPI's request.state may not receive mutations made by the outer
    # function middleware. Resolve the bearer token again at the route boundary.
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


def _require_active_full_access(db: Session, person_id: str, caregiver_id: str) -> None:
    grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
        CaregiverAccessGrant.access_scope == "full_record",
    ).first()
    if grant is None:
        raise HTTPException(status_code=403, detail="The assigned caregiver does not have active full-record access")


def _assignment_audit(person_id: str, actor_id: str, reminder_id: str, previous_id: str | None, next_id: str):
    return AuditLogEvent(
        actor_id=actor_id,
        subject_id=person_id,
        person_id=person_id,
        caregiver_id=actor_id,
        action="care.reminder.assigned" if previous_id is None else "care.reminder.reassigned",
        target_type="care_reminder",
        target_id=reminder_id,
        occurred_at=datetime.now(timezone.utc),
        outcome="success",
        policy_version="care-reminder-assignment-v1",
        record_metadata={
            "previous_assignee_caregiver_id": previous_id,
            "assigned_to_caregiver_id": next_id,
        },
    )


def _commit_reminder_update(db: Session) -> None:
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This reminder changed concurrently. Refresh before trying again.",
        )


@router.post("/reminders", response_model=CareReminderRead, status_code=201)
def create_reminder(payload: CareReminderCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _session_scope(request, db)
    if payload.person_id != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if payload.due_at.tzinfo is None or payload.due_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="due_at must include a timezone offset")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Reminder title cannot be blank")

    assignee_id = payload.assigned_to_caregiver_id or caregiver_id
    _require_active_full_access(db, person_id, assignee_id)
    reminder = CareReminder(
        person_id=person_id,
        created_by_caregiver_id=caregiver_id,
        assigned_to_caregiver_id=assignee_id,
        title=title,
        note=payload.note.strip() if payload.note and payload.note.strip() else None,
        due_at=payload.due_at.astimezone(timezone.utc),
        status="open",
    )
    db.add(reminder)
    db.flush()
    db.add(_assignment_audit(person_id, caregiver_id, reminder.id, None, assignee_id))
    _commit_reminder_update(db)
    db.refresh(reminder)
    return reminder


@router.patch("/reminders/{reminder_id}/assignment", response_model=CareReminderRead)
def reassign_reminder(
    reminder_id: str,
    payload: CareReminderAssign,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, actor_id = _session_scope(request, db)
    reminder = db.query(CareReminder).filter(
        CareReminder.id == reminder_id,
        CareReminder.person_id == person_id,
    ).with_for_update().first()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status != "open":
        raise HTTPException(status_code=409, detail="Only an open reminder can be reassigned")
    if reminder.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="This reminder changed since it was loaded. Refresh before reassigning.")
    _require_active_full_access(db, person_id, payload.assigned_to_caregiver_id)
    previous_id = reminder.assigned_to_caregiver_id
    if previous_id == payload.assigned_to_caregiver_id:
        return reminder
    reminder.assigned_to_caregiver_id = payload.assigned_to_caregiver_id
    db.add(_assignment_audit(person_id, actor_id, reminder.id, previous_id, payload.assigned_to_caregiver_id))
    _commit_reminder_update(db)
    db.refresh(reminder)
    return reminder


@router.get("/reminders/{person_id}", response_model=list[CareReminderRead])
def list_reminders(
    person_id: str,
    request: Request,
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    scoped_person_id, _ = _session_scope(request, db)
    if person_id != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if status and status not in {"open", "completed", "cancelled"}:
        raise HTTPException(status_code=400, detail="Invalid reminder status")
    query = db.query(CareReminder).filter(CareReminder.person_id == scoped_person_id)
    if status:
        query = query.filter(CareReminder.status == status)
    return query.order_by(CareReminder.due_at.asc(), CareReminder.created_at.asc()).all()


@router.patch("/reminders/{reminder_id}/complete", response_model=CareReminderRead)
def complete_reminder(
    reminder_id: str,
    payload: CareReminderComplete,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _session_scope(request, db)
    reminder = (
        db.query(CareReminder)
        .filter(CareReminder.id == reminder_id, CareReminder.person_id == person_id)
        .with_for_update()
        .first()
    )
    if reminder is None:
        # Do not reveal whether another care record owns this reminder.
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status == "cancelled":
        raise HTTPException(status_code=409, detail="A cancelled reminder cannot be completed")
    if reminder.status == "open":
        reminder.status = "completed"
        _cancel_pending_reminder_pushes(db, reminder.id)
        reminder.completed_at = datetime.now(timezone.utc)
        reminder.completed_by_caregiver_id = caregiver_id
    if payload.completion_note is not None:
        reminder.completion_note = payload.completion_note.strip() or None
    _commit_reminder_update(db)
    db.refresh(reminder)
    return reminder


@router.patch("/reminders/{reminder_id}/snooze", response_model=CareReminderRead)
def snooze_reminder(
    reminder_id: str,
    payload: CareReminderSnooze,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _session_scope(request, db)
    reminder = (
        db.query(CareReminder)
        .filter(CareReminder.id == reminder_id, CareReminder.person_id == person_id)
        .with_for_update()
        .first()
    )
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status != "open":
        raise HTTPException(status_code=409, detail="Only an open reminder can be snoozed")
    if payload.snooze_until.tzinfo is None or payload.snooze_until.utcoffset() is None:
        raise HTTPException(status_code=422, detail="snooze_until must include a timezone offset")
    snooze_until = payload.snooze_until.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if snooze_until <= now:
        raise HTTPException(status_code=422, detail="Choose a future snooze time")
    from datetime import timedelta
    if snooze_until > now + timedelta(days=30):
        raise HTTPException(status_code=422, detail="Snooze time cannot be more than 30 days away")
    _cancel_pending_reminder_pushes(db, reminder.id)
    reminder.due_at = snooze_until
    reminder.notification_deferred_until = None
    reminder.snoozed_at = now
    reminder.snoozed_by_caregiver_id = caregiver_id
    reminder.snooze_count = (reminder.snooze_count or 0) + 1
    reminder.delivery_generation = (reminder.delivery_generation or 0) + 1
    _commit_reminder_update(db)
    db.refresh(reminder)
    return reminder


@router.patch("/reminders/{reminder_id}/cancel", response_model=CareReminderRead)
def cancel_reminder(reminder_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _session_scope(request, db)
    reminder = (
        db.query(CareReminder)
        .filter(CareReminder.id == reminder_id, CareReminder.person_id == person_id)
        .with_for_update()
        .first()
    )
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status == "completed":
        raise HTTPException(status_code=409, detail="A completed reminder cannot be cancelled")
    if reminder.status == "open":
        _cancel_pending_reminder_pushes(db, reminder.id)
        reminder.status = "cancelled"
        reminder.cancelled_at = datetime.now(timezone.utc)
        reminder.cancelled_by_caregiver_id = caregiver_id
    _commit_reminder_update(db)
    db.refresh(reminder)
    return reminder
