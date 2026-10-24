from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.action import Action as ActionModel
from app.models.care import Caregiver
from app.models.reminder import CareReminder, ReminderDelivery, ReminderDeliveryAttempt
from app.services.medication_authority import action_has_verified_medication_authority
from app.services.medication_authority import medication_instruction_requires_authority, get_verified_medication_authority
from app.schemas.reminder import ReminderCreate, ReminderOut, ReminderDeliveryOut, ReminderDeliveryAttemptOut

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
    if action.status not in ("active", "conditional"):
        raise HTTPException(status_code=409, detail="Reminders require an active or conditional care action that is no longer awaiting confirmation")
    medication_text = " ".join((action.original_text or "", action.source_passage or "", action.normalized_action or "", action.action_object or ""))
    if medication_instruction_requires_authority(medication_text, action.is_medication_action):
        verification = get_verified_medication_authority(
            db, person_id=person_id, evidence_id=action.source_evidence_id,
            source_authority=action.source_authority,
        )
        if verification is None:
            raise HTTPException(status_code=409, detail="Medication reminders require a current trusted prescriber-source verification")
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



@router.get("/{reminder_id}/deliveries/{delivery_id}/attempts", response_model=list[ReminderDeliveryAttemptOut])
def list_delivery_attempts(reminder_id: str, delivery_id: str, request: Request, db: Session = Depends(get_db)):
    _, person_id = _session_scope(request)
    reminder = db.query(CareReminder).filter(
        CareReminder.id == reminder_id,
        CareReminder.person_id == person_id,
    ).first()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    delivery = db.query(ReminderDelivery).filter(
        ReminderDelivery.id == delivery_id,
        ReminderDelivery.reminder_id == reminder_id,
        ReminderDelivery.person_id == person_id,
    ).first()
    if delivery is None:
        raise HTTPException(status_code=404, detail="Reminder delivery receipt not found")
    return db.query(ReminderDeliveryAttempt).filter(
        ReminderDeliveryAttempt.delivery_id == delivery_id,
    ).order_by(ReminderDeliveryAttempt.attempt_number.asc()).all()


@router.get("/people/{person_id}/deliveries", response_model=list[ReminderDeliveryOut])
def list_person_delivery_inbox(person_id: str, request: Request, db: Session = Depends(get_db)):
    _, scoped_person_id = _session_scope(request)
    if str(person_id) != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return db.query(ReminderDelivery).filter(
        ReminderDelivery.person_id == scoped_person_id,
    ).order_by(ReminderDelivery.scheduled_for.desc()).limit(200).all()



@router.delete("/{reminder_id}", response_model=ReminderOut)
def cancel_reminder(reminder_id: str, request: Request, db: Session = Depends(get_db)):
    _, person_id = _session_scope(request)
    reminder = db.query(CareReminder).filter(
        CareReminder.id == reminder_id,
        CareReminder.person_id == person_id,
    ).first()
    if reminder is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    reminder.enabled = False
    pending = db.query(ReminderDelivery).filter(
        ReminderDelivery.reminder_id == reminder_id,
        ReminderDelivery.person_id == person_id,
        ReminderDelivery.status.in_(("pending", "retry")),
    ).all()
    for receipt in pending:
        receipt.status = "cancelled"
        receipt.last_error = "Reminder cancelled by authorised caregiver"
    db.commit()
    db.refresh(reminder)
    return reminder
