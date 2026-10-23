from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.action import Action as ActionModel
from app.models.care import Caregiver
from app.models.reminder import CareReminder, ReminderDelivery
from app.schemas.reminder import ReminderCreate, ReminderOut, ReminderDeliveryOut

router = APIRouter(prefix="/reminders", tags=["care-reminders"])


def _session_scope(request: Request):
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    person_id = getattr(request.state, "scoped_person_id", None)
    if not caregiver_id or not person_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    return str(caregiver_id), str(person_id)


@router.post("/actions/{action_id}", response_model=ReminderOut, status_code=201)
def create_reminder(action_id: str, payload: ReminderCreate, request: Request, db: Session = Depends(get_db)):
    caregiver_id, person_id = _session_scope(request)
    action = db.query(ActionModel).filter(ActionModel.id == action_id).first()
    if action is None:
        raise HTTPException(status_code=404, detail="Care action not found")
    if str(action.person_id) != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care action")
    if action.status in ("completed", "cancelled", "superseded", "expired"):
        raise HTTPException(status_code=409, detail="Cannot schedule a reminder for a terminal care action")
    if action.is_medication_action and action.requires_confirmation:
        raise HTTPException(status_code=409, detail="Medication action requires verified clinical source review before reminders can be scheduled")
    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id, Caregiver.person_id == person_id).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")
    try:
        zone = ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="timezone must be a valid IANA timezone") from exc
    scheduled = payload.scheduled_at.astimezone(timezone.utc)
    local = scheduled.astimezone(zone)
    if payload.recurrence != "once" and local.strftime("%H:%M") != payload.local_time:
        raise HTTPException(status_code=422, detail="For recurring reminders, scheduled_at must match local_time in the selected timezone")
    reminder = CareReminder(
        person_id=person_id,
        action_id=action.id,
        created_by_caregiver_id=caregiver_id,
        title=payload.title.strip(),
        message=payload.message.strip(),
        timezone=payload.timezone,
        recurrence=payload.recurrence,
        next_run_at=scheduled,
        local_time=payload.local_time,
        enabled=True,
        max_attempts=payload.max_attempts,
        retry_delay_seconds=payload.retry_delay_seconds,
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


@router.get("/people/{person_id}", response_model=list[ReminderOut])
def list_reminders(person_id: str, request: Request, db: Session = Depends(get_db)):
    _, scoped_person_id = _session_scope(request)
    if str(person_id) != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return db.query(CareReminder).filter(CareReminder.person_id == scoped_person_id).order_by(CareReminder.next_run_at.asc()).all()


@router.get("/{reminder_id}/deliveries", response_model=list[ReminderDeliveryOut])
def list_reminder_deliveries(reminder_id: str, request: Request, db: Session = Depends(get_db)):
    _, person_id = _session_scope(request)
    reminder = db.query(CareReminder).filter(CareReminder.id == reminder_id).first()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    if str(reminder.person_id) != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that reminder")
    return db.query(ReminderDelivery).filter(ReminderDelivery.reminder_id == reminder_id, ReminderDelivery.person_id == person_id).order_by(ReminderDelivery.scheduled_for.desc()).all()
