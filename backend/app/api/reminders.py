from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import Caregiver, Person
from app.models.action import Action
from app.models.reminder import CareReminder, ReminderDeliveryAttempt
from app.schemas.reminder import ReminderCreate, ReminderResponse

router = APIRouter()


@router.post("", response_model=ReminderResponse, status_code=201)
def create_reminder(payload: ReminderCreate, request: Request, db: Session = Depends(get_db)):
    session_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not session_caregiver_id or payload.caregiver_id != session_caregiver_id:
        raise HTTPException(status_code=403, detail="Reminder attribution must match the authenticated caregiver session")
    person = db.query(Person).filter(Person.id == payload.person_id).first()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == payload.caregiver_id,
        Caregiver.person_id == payload.person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care recipient")
    if payload.action_id:
        action = db.query(Action).filter(
            Action.id == payload.action_id,
            Action.person_id == payload.person_id,
        ).first()
        if action is None:
            raise HTTPException(status_code=422, detail="Reminder action must belong to the same care recipient")

    existing = db.query(CareReminder).filter(
        CareReminder.person_id == payload.person_id,
        CareReminder.idempotency_key == payload.idempotency_key,
    ).first()
    if existing:
        same_request = (
            existing.title == payload.title
            and existing.message == payload.message
            and existing.due_at == payload.due_at
            and existing.action_id == payload.action_id
            and existing.recurrence_rule == payload.recurrence_rule
            and existing.timezone == payload.timezone
            and existing.max_attempts == payload.max_attempts
            and existing.created_by_caregiver_id == payload.caregiver_id
        )
        if not same_request:
            raise HTTPException(status_code=409, detail="Idempotency key already used for a different reminder")
        return existing

    reminder = CareReminder(
        person_id=payload.person_id,
        action_id=payload.action_id,
        created_by_caregiver_id=payload.caregiver_id,
        title=payload.title,
        message=payload.message,
        due_at=payload.due_at.astimezone(timezone.utc),
        timezone=payload.timezone,
        recurrence_rule=payload.recurrence_rule,
        recurrence_anchor_day=(payload.due_at.astimezone(ZoneInfo(payload.timezone)).day if payload.recurrence_rule == "monthly" else None),
        idempotency_key=payload.idempotency_key,
        max_attempts=payload.max_attempts,
        status="scheduled",
        next_attempt_at=payload.due_at.astimezone(timezone.utc),
    )
    db.add(reminder)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(CareReminder).filter(
            CareReminder.person_id == payload.person_id,
            CareReminder.idempotency_key == payload.idempotency_key,
        ).first()
        if existing:
            return existing
        raise HTTPException(status_code=409, detail="Reminder could not be saved due to a concurrent update")
    db.refresh(reminder)
    return reminder


@router.get("/{person_id}", response_model=list[ReminderResponse])
def list_reminders(person_id: str, caregiver_id: str, request: Request, status: str | None = None, db: Session = Depends(get_db)):
    session_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not session_caregiver_id or caregiver_id != session_caregiver_id:
        raise HTTPException(status_code=403, detail="Reminder access must match the authenticated caregiver session")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_id,
        Caregiver.person_id == person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care recipient")
    query = db.query(CareReminder).filter(CareReminder.person_id == person_id)
    if status:
        if status not in {"scheduled", "pending", "delivering", "delivered", "failed", "cancelled"}:
            raise HTTPException(status_code=422, detail="Invalid reminder status")
        query = query.filter(CareReminder.status == status)
    return query.order_by(CareReminder.due_at.asc(), CareReminder.created_at.asc()).all()


@router.get("/{person_id}/{reminder_id}/attempts")
def list_delivery_attempts(person_id: str, reminder_id: str, caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    session_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not session_caregiver_id or caregiver_id != session_caregiver_id:
        raise HTTPException(status_code=403, detail="Delivery history access must match the authenticated caregiver session")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_id,
        Caregiver.person_id == person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care recipient")
    reminder = db.query(CareReminder).filter(
        CareReminder.id == reminder_id,
        CareReminder.person_id == person_id,
    ).first()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    attempts = db.query(ReminderDeliveryAttempt).filter(
        ReminderDeliveryAttempt.reminder_id == reminder.id,
    ).order_by(ReminderDeliveryAttempt.attempt_number.asc()).all()
    return [
        {
            "id": item.id,
            "reminder_id": item.reminder_id,
            "attempt_number": item.attempt_number,
            "outcome": item.outcome,
            "provider": item.provider,
            "provider_receipt_id": item.provider_receipt_id,
            "error_message": item.error_message,
            "attempted_at": item.attempted_at,
        }
        for item in attempts
    ]


@router.post("/{person_id}/{reminder_id}/cancel", response_model=ReminderResponse)
def cancel_reminder(person_id: str, reminder_id: str, caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    session_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not session_caregiver_id or caregiver_id != session_caregiver_id:
        raise HTTPException(status_code=403, detail="Cancellation attribution must match the authenticated caregiver session")
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_id,
        Caregiver.person_id == person_id,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care recipient")
    reminder = db.query(CareReminder).filter(
        CareReminder.id == reminder_id,
        CareReminder.person_id == person_id,
    ).first()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status == "delivered":
        raise HTTPException(status_code=409, detail="A delivered reminder cannot be cancelled")
    if reminder.status == "cancelled":
        return reminder
    reminder.status = "cancelled"
    reminder.cancelled_at = datetime.now(timezone.utc)
    reminder.next_attempt_at = None
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder
