"""Retryable reminder delivery orchestration.

The delivery adapter is injected by the deployment worker. It must pass the
stable idempotency key to the downstream provider so a worker crash after send
but before receipt persistence cannot create duplicate notifications.
"""
from datetime import datetime, timedelta, timezone
from calendar import monthrange
from zoneinfo import ZoneInfo
from typing import Callable, Any
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from sqlalchemy.orm import Session

from app.models.reminder import CareReminder, ReminderDeliveryAttempt


def _next_occurrence(reminder: CareReminder):
    if reminder.recurrence_rule not in {"daily", "weekly", "monthly"}:
        return None
    zone = ZoneInfo(reminder.timezone or "UTC")
    local_due = reminder.due_at.astimezone(zone)
    if reminder.recurrence_rule == "daily":
        next_local = local_due + timedelta(days=1)
    elif reminder.recurrence_rule == "weekly":
        next_local = local_due + timedelta(weeks=1)
    else:
        year = local_due.year + (1 if local_due.month == 12 else 0)
        month = 1 if local_due.month == 12 else local_due.month + 1
        anchor_day = reminder.recurrence_anchor_day or local_due.day
        day = min(anchor_day, monthrange(year, month)[1])
        next_local = local_due.replace(year=year, month=month, day=day)
    return next_local, next_local.astimezone(timezone.utc)


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
    # Recover claims abandoned by a worker crash. The configured webhook timeout
    # is 15 seconds, so a ten-minute lease safely distinguishes stale claims.
    stale_before = now - timedelta(minutes=10)
    stale_claims = db.query(CareReminder).filter(
        CareReminder.status == "delivering",
        CareReminder.updated_at.is_not(None),
        CareReminder.updated_at < stale_before,
    ).all()
    for stale in stale_claims:
        stale.last_error = "Recovered stale delivery claim after worker interruption"
        if stale.attempt_count >= stale.max_attempts:
            stale.status = "failed"
            stale.next_attempt_at = None
        else:
            stale.status = "scheduled"
            stale.next_attempt_at = now
        db.add(stale)
    if stale_claims:
        db.commit()

    query = db.query(CareReminder.id).filter(
        CareReminder.status == "scheduled",
        CareReminder.due_at <= now,
        CareReminder.next_attempt_at <= now,
        CareReminder.attempt_count < CareReminder.max_attempts,
    ).order_by(CareReminder.next_attempt_at.asc(), CareReminder.created_at.asc())
    candidate_ids = [row[0] for row in query.limit(limit).all()]
    summary = {"selected": 0, "delivered": 0, "retry_scheduled": 0, "failed": 0}

    for reminder_id in candidate_ids:
        # Atomic compare-and-set claim prevents two workers from delivering the
        # same reminder, including on SQLite where SKIP LOCKED is unavailable.
        claimed = db.query(CareReminder).filter(
            CareReminder.id == reminder_id,
            CareReminder.status == "scheduled",
            CareReminder.due_at <= now,
            CareReminder.next_attempt_at <= now,
            CareReminder.attempt_count < CareReminder.max_attempts,
        ).update({
            "status": "delivering",
            "attempt_count": CareReminder.attempt_count + 1,
            "updated_at": now,
        }, synchronize_session=False)
        db.commit()
        if claimed != 1:
            continue
        reminder = db.query(CareReminder).filter(CareReminder.id == reminder_id).first()
        if reminder is None:
            continue
        summary["selected"] += 1
        attempt_number = reminder.attempt_count
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
            occurrence = _next_occurrence(reminder)
            if occurrence:
                next_local, next_utc = occurrence
                next_key = f"{reminder.idempotency_key}:{next_local.isoformat()}"
                next_exists = db.query(CareReminder.id).filter(
                    CareReminder.person_id == reminder.person_id,
                    CareReminder.idempotency_key == next_key,
                ).first()
                if not next_exists:
                    db.add(CareReminder(
                        person_id=reminder.person_id,
                        action_id=reminder.action_id,
                        created_by_caregiver_id=reminder.created_by_caregiver_id,
                        title=reminder.title,
                        message=reminder.message,
                        due_at=next_utc,
                        timezone=reminder.timezone,
                        recurrence_rule=reminder.recurrence_rule,
                        recurrence_anchor_day=reminder.recurrence_anchor_day or (local_due.day if reminder.recurrence_rule == "monthly" else None),
                        status="scheduled",
                        idempotency_key=next_key,
                        attempt_count=0,
                        max_attempts=reminder.max_attempts,
                        next_attempt_at=next_utc,
                    ))
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


def configured_webhook_sender(reminder: CareReminder, idempotency_key: str) -> dict:
    """Deliver through an explicitly configured HTTPS webhook.

    The receiving provider must honor the Idempotency-Key header and return
    JSON containing a durable receipt_id. No successful delivery is inferred
    from HTTP status alone.
    """
    endpoint = os.getenv("REMINDER_DELIVERY_URL", "").strip()
    token = os.getenv("REMINDER_DELIVERY_TOKEN", "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("REMINDER_DELIVERY_URL must be a configured HTTPS endpoint")
    if not token:
        raise RuntimeError("REMINDER_DELIVERY_TOKEN is not configured")
    payload = {
        "reminder_id": reminder.id,
        "person_id": reminder.person_id,
        "action_id": reminder.action_id,
        "title": reminder.title,
        "message": reminder.message,
        "due_at": reminder.due_at.isoformat(),
        "timezone": reminder.timezone,
        "idempotency_key": idempotency_key,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Reminder provider returned HTTP {response.status}")
            data = json.loads(response.read(65536).decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Reminder provider returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Reminder provider request failed: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict) or not data.get("receipt_id"):
        raise RuntimeError("Reminder provider response must include receipt_id")
    return {"provider": data.get("provider") or parsed.hostname, "receipt_id": str(data["receipt_id"])}


def run_configured_reminder_worker(limit: int = 50) -> dict:
    """One-shot worker entry point suitable for a managed cron/scheduler."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        return process_due_reminders(db, configured_webhook_sender, limit=limit)
    finally:
        db.close()


if __name__ == "__main__":
    result = run_configured_reminder_worker()
    print(json.dumps(result, sort_keys=True))
