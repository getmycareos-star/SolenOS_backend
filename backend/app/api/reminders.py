from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.reminder import CareReminder
from app.schemas.reminder import CareReminderComplete, CareReminderCreate, CareReminderRead

router = APIRouter(tags=["care reminders"])


def _session_scope(request: Request) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A valid SolenOS care session is required")
    return str(person_id), str(caregiver_id)


@router.post("/reminders", response_model=CareReminderRead, status_code=201)
def create_reminder(payload: CareReminderCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _session_scope(request)
    if payload.person_id != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if payload.due_at.tzinfo is None or payload.due_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="due_at must include a timezone offset")
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Reminder title cannot be blank")

    reminder = CareReminder(
        person_id=person_id,
        created_by_caregiver_id=caregiver_id,
        title=title,
        note=payload.note.strip() if payload.note and payload.note.strip() else None,
        due_at=payload.due_at.astimezone(timezone.utc),
        status="open",
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


@router.get("/reminders/{person_id}", response_model=list[CareReminderRead])
def list_reminders(
    person_id: str,
    request: Request,
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    scoped_person_id, _ = _session_scope(request)
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
    person_id, caregiver_id = _session_scope(request)
    reminder = (
        db.query(CareReminder)
        .filter(CareReminder.id == reminder_id, CareReminder.person_id == person_id)
        .first()
    )
    if reminder is None:
        # Do not reveal whether another care record owns this reminder.
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status == "cancelled":
        raise HTTPException(status_code=409, detail="A cancelled reminder cannot be completed")
    if reminder.status == "open":
        reminder.status = "completed"
        reminder.completed_at = datetime.now(timezone.utc)
        reminder.completed_by_caregiver_id = caregiver_id
    if payload.completion_note is not None:
        reminder.completion_note = payload.completion_note.strip() or None
    db.commit()
    db.refresh(reminder)
    return reminder


@router.patch("/reminders/{reminder_id}/cancel", response_model=CareReminderRead)
def cancel_reminder(reminder_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _session_scope(request)
    reminder = (
        db.query(CareReminder)
        .filter(CareReminder.id == reminder_id, CareReminder.person_id == person_id)
        .first()
    )
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if reminder.status == "completed":
        raise HTTPException(status_code=409, detail="A completed reminder cannot be cancelled")
    if reminder.status == "open":
        reminder.status = "cancelled"
        reminder.cancelled_at = datetime.now(timezone.utc)
        reminder.cancelled_by_caregiver_id = caregiver_id
    db.commit()
    db.refresh(reminder)
    return reminder
