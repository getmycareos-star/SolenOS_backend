"""Retryable reminder delivery orchestration.

The delivery adapter is injected by the deployment worker. It must pass the
stable idempotency key to the downstream provider so a worker crash after send
but before receipt persistence cannot create duplicate notifications.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable, Any
from sqlalchemy.orm import Session

from app.models.reminder import CareReminder, ReminderDeliveryAttempt


def process_due_reminders(
    db: Session,
    sender: Callable[[CareReminder, str], Any],
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> dict:
    """Attempt due reminders and persist a receipt for every delivery attempt.

    sender(reminder, idempotency_key) must return a mapping with optional
    provider and receipt_id keys or raise an exception on delivery failure.
    """
    now = now or datetime.now(timezone.utc)
    limit = max(1, min(int(limit), 200))
    query = db.query(CareReminder).filter(
        CareReminder.status == "scheduled",
        CareReminder.due_at <= now,
        CareReminder.next_attempt_at <= now,
        CareReminder.attempt_count < CareReminder.max_attempts,
    ).order_by(CareReminder.next_attempt_at.asc(), CareReminder.created_at.asc())
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    reminders = query.limit(limit).all()
    summary = {"selected": len(reminders), "delivered": 0, "retry_scheduled": 0, "failed": 0}

    for reminder in reminders:
        attempt_number = reminder.attempt_count + 1
        # Commit the claim before external I/O. Provider-side idempotency is
        # required to cover a crash between sending and saving the receipt.
        reminder.status = "delivering"
        reminder.attempt_count = attempt_number
        db.add(reminder)
        db.commit()
        db.refresh(reminder)
        try:
            receipt = sender(reminder, reminder.idempotency_key) or {}
            provider = receipt.get("provider") if isinstance(receipt, dict) else None
            receipt_id = receipt.get("receipt_id") if isinstance(receipt, dict) else None
            if not receipt_id:
                raise RuntimeError("Delivery adapter did not return a durable provider receipt")
            db.add(ReminderDeliveryAttempt(
                reminder_id=reminder.id,
                attempt_number=attempt_number,
                outcome="delivered",
                provider=provider,
                provider_receipt_id=str(receipt_id),
            ))
            reminder.status = "delivered"
            reminder.delivered_at = datetime.now(timezone.utc)
            reminder.next_attempt_at = None
            reminder.last_error = None
            summary["delivered"] += 1
        except Exception as exc:
            error = str(exc)[:2000] or exc.__class__.__name__
            db.add(ReminderDeliveryAttempt(
                reminder_id=reminder.id,
                attempt_number=attempt_number,
                outcome="failed",
                error_message=error,
            ))
            reminder.last_error = error
            if attempt_number >= reminder.max_attempts:
                reminder.status = "failed"
                reminder.next_attempt_at = None
                summary["failed"] += 1
            else:
                delay_seconds = min(24 * 60 * 60, 60 * (2 ** (attempt_number - 1)))
                reminder.status = "scheduled"
                reminder.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                summary["retry_scheduled"] += 1
        db.add(reminder)
        db.commit()
    return summary
