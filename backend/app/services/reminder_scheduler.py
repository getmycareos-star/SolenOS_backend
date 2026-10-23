from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from app.models.reminder import CareReminder, ReminderDelivery, ReminderDeliveryAttempt


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
    """Persist each attempt and deliver through the durable in-app inbox."""
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
        reminder = db.query(CareReminder).filter_by(id=receipt.reminder_id).first()
        if reminder is None:
            receipt.status = "failed"
            receipt.last_error = "Reminder definition no longer exists"
            db.commit()
            continue

        attempt_number = receipt.attempt_count + 1
        attempt_key = f"{receipt.id}:{attempt_number}"
        attempt = ReminderDeliveryAttempt(
            delivery_id=receipt.id,
            attempt_number=attempt_number,
            channel="in_app",
            started_at=now,
            outcome="pending",
            idempotency_key=attempt_key,
        )
        receipt.attempt_count = attempt_number
        db.add(attempt)
        try:
            # Commit the attempt before processing so a process crash leaves a
            # durable record of the in-flight attempt for recovery/audit.
            db.commit()

            # In-app delivery is the implemented channel. A durable receipt
            # becomes visible to authenticated clients via the scoped API.
            receipt = db.query(ReminderDelivery).filter_by(id=receipt.id).with_for_update().first()
            attempt = db.query(ReminderDeliveryAttempt).filter_by(id=attempt.id).first()
            if receipt is None or attempt is None:
                continue
            receipt.status = "delivered"
            receipt.delivered_at = now
            receipt.last_error = None
            attempt.outcome = "delivered"
            attempt.finished_at = now
            db.commit()
            delivered += 1
        except SQLAlchemyError as exc:
            db.rollback()
            # If the first commit failed, no attempt is durable. The scheduler
            # will revisit the still-pending receipt next cycle.
            try:
                receipt = db.query(ReminderDelivery).filter_by(id=receipt.id).first()
                attempt = db.query(ReminderDeliveryAttempt).filter_by(id=attempt.id).first()
                if receipt is None:
                    continue
                receipt.last_error = type(exc).__name__
                if receipt.attempt_count >= reminder.max_attempts:
                    receipt.status = "failed"
                else:
                    receipt.status = "retry"
                    receipt.next_attempt_at = now + timedelta(
                        seconds=min(3600, reminder.retry_delay_seconds * (2 ** max(0, receipt.attempt_count - 1)))
                    )
                if attempt is not None:
                    attempt.outcome = receipt.status
                    attempt.error_type = type(exc).__name__
                    attempt.finished_at = now
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                # Keep the persisted pending receipt recoverable; next poll retries.
                logger.exception("Could not persist reminder retry state")
    return delivered
