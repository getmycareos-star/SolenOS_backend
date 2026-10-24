from datetime import datetime, timezone, timedelta, time
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.models.reminder import CareReminder, ReminderDelivery, ReminderDeliveryAttempt
from app.models.action import Action as ActionModel
from app.services.medication_authority import action_has_verified_medication_authority
from app.services.medication_authority import medication_instruction_requires_authority, get_verified_medication_authority


def _action_has_current_medication_authority(db: Session, action: ActionModel) -> bool:
    text = " ".join((action.original_text or "", action.source_passage or "", action.normalized_action or "", action.action_object or ""))
    if not medication_instruction_requires_authority(text, action.is_medication_action):
        return True
    return get_verified_medication_authority(
        db,
        person_id=str(action.person_id),
        evidence_id=str(action.source_evidence_id or ""),
        source_authority=action.source_authority or "unknown",
    ) is not None

logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        # Naive timestamps are rejected at API boundaries; this fallback is for
        # older persisted rows and treats them as UTC, never server-local time.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_local_occurrence(current_utc: datetime, timezone_name: str, local_time: str, recurrence: str) -> datetime | None:
    if recurrence == "once":
        return None
    try:
        zone = ZoneInfo(timezone_name)
        wall_time = time.fromisoformat(local_time)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("A valid IANA timezone and HH:MM local time are required") from exc
    local_current = _aware_utc(current_utc).astimezone(zone)
    days = 1 if recurrence == "daily" else 7 if recurrence == "weekly" else None
    if days is None:
        raise ValueError("Recurrence must be once, daily, or weekly")
    target_date = local_current.date() + timedelta(days=days)
    candidate = datetime.combine(target_date, wall_time).replace(tzinfo=zone, fold=0)
    # A local wall time can be skipped by a DST jump. Move forward to the first
    # valid local minute rather than silently shifting by the UTC offset.
    for _ in range(181):
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return candidate.astimezone(timezone.utc)
        candidate += timedelta(minutes=1)
    raise ValueError("Could not resolve the recurring time across the timezone transition")


def materialize_due_reminders(db: Session, now: datetime | None = None, batch_size: int = 100) -> int:
    """Persist one idempotent inbox receipt per due occurrence; safe to rerun after restarts."""
    now = _aware_utc(now or utc_now())
    created = 0
    due = (
        db.query(CareReminder)
        .filter(CareReminder.enabled.is_(True), CareReminder.next_run_at <= now)
        .order_by(CareReminder.next_run_at.asc())
        .with_for_update(skip_locked=True)
        .limit(batch_size)
        .all()
    )
    for reminder in due:
        action = db.query(ActionModel).filter_by(id=reminder.action_id, person_id=reminder.person_id).first()
        if action is None or action.status not in ("active", "conditional") or not _action_has_current_medication_authority(db, action):
            reminder.enabled = False
            db.commit()
            continue
        scheduled_for = _aware_utc(reminder.next_run_at)
        key = f"{reminder.id}:{scheduled_for.isoformat(timespec='seconds')}"
        receipt = ReminderDelivery(
            reminder_id=reminder.id,
            person_id=reminder.person_id,
            idempotency_key=key,
            scheduled_for=scheduled_for,
            status="pending",
            attempt_count=0,
            next_attempt_at=now,
        )
        try:
            db.add(receipt)
            db.flush()
        except IntegrityError:
            db.rollback()
            # A different worker may have already created this occurrence.
            # Re-fetch and advance only if the receipt exists.
            existing = db.query(ReminderDelivery).filter_by(idempotency_key=key).first()
            if existing is None:
                continue
            reminder = db.query(CareReminder).filter_by(id=reminder.id).with_for_update().first()
            if reminder is None:
                continue
        next_run = next_local_occurrence(scheduled_for, reminder.timezone, reminder.local_time, reminder.recurrence)
        if next_run is None:
            reminder.enabled = False
        else:
            reminder.next_run_at = next_run
        db.commit()
        created += 1
    return created


def deliver_pending_receipts(db: Session, now: datetime | None = None, batch_size: int = 100) -> int:
    """Deliver through the durable in-app inbox and persist each completed attempt.

    The delivery and attempt record commit atomically while the receipt row lock
    is held. A process crash before commit leaves the receipt retryable; concurrent
    workers cannot both claim the same pending receipt.
    """
    now = _aware_utc(now or utc_now())
    receipts = (
        db.query(ReminderDelivery)
        .filter(
            ReminderDelivery.status.in_(("pending", "retry")),
            ReminderDelivery.next_attempt_at <= now,
        )
        .order_by(ReminderDelivery.next_attempt_at.asc())
        .with_for_update(skip_locked=True)
        .limit(batch_size)
        .all()
    )
    delivered = 0
    for receipt in receipts:
        receipt_id = receipt.id
        reminder = db.query(CareReminder).filter_by(id=receipt.reminder_id).first()
        if reminder is None:
            receipt.status = "failed"
            receipt.last_error = "Reminder definition no longer exists"
            db.commit()
            continue

        action = db.query(ActionModel).filter_by(id=reminder.action_id, person_id=receipt.person_id).first()
        if action is None or action.status not in ("active", "conditional") or not action_has_verified_medication_authority(db, action):
            reminder.enabled = False
            receipt.status = "cancelled"
            receipt.last_error = "Associated care action is no longer active or its medication source is no longer verified"
            db.commit()
            continue
        if not _action_has_current_medication_authority(db, action):
            reminder.enabled = False
            receipt.status = "cancelled"
            receipt.last_error = "Medication source verification is missing, expired, or no longer matches the stored evidence"
            db.commit()
            continue

        attempt_number = receipt.attempt_count + 1
        attempt = ReminderDeliveryAttempt(
            delivery_id=receipt_id,
            attempt_number=attempt_number,
            channel="in_app",
            started_at=now,
            finished_at=now,
            outcome="delivered",
            idempotency_key=f"{receipt_id}:{attempt_number}",
        )
        receipt.attempt_count = attempt_number
        receipt.status = "delivered"
        receipt.delivered_at = now
        receipt.last_error = None
        db.add(attempt)
        try:
            # The inbox receipt itself is the in-app notification visible to the
            # authenticated caregiver. Receipt state and attempt history commit
            # together, preventing concurrent duplicate delivery processing.
            db.commit()
            delivered += 1
        except SQLAlchemyError as exc:
            db.rollback()
            try:
                receipt = db.query(ReminderDelivery).filter_by(id=receipt_id).with_for_update().first()
                reminder = db.query(CareReminder).filter_by(id=receipt.reminder_id).first() if receipt else None
                if receipt is None or reminder is None:
                    continue
                attempt_number = receipt.attempt_count + 1
                terminal = attempt_number >= reminder.max_attempts
                attempt = ReminderDeliveryAttempt(
                    delivery_id=receipt_id,
                    attempt_number=attempt_number,
                    channel="in_app",
                    started_at=now,
                    finished_at=now,
                    outcome="failed" if terminal else "retry",
                    error_type=type(exc).__name__,
                    idempotency_key=f"{receipt_id}:{attempt_number}",
                )
                receipt.attempt_count = attempt_number
                receipt.last_error = type(exc).__name__
                if terminal:
                    receipt.status = "failed"
                else:
                    receipt.status = "retry"
                    receipt.next_attempt_at = now + timedelta(
                        seconds=min(3600, reminder.retry_delay_seconds * (2 ** max(0, attempt_number - 1)))
                    )
                db.add(attempt)
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                # Receipt remains pending/retryable when persistence itself is unavailable.
                logger.exception("Could not persist reminder retry state")
    return delivered
