"""Durable in-app delivery for due care reminders.

Quiet hours defer notification creation; they do not alter the reminder due time.
This worker does not claim to send Web Push or native device notifications.
"""
import asyncio
import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, cast, exists, literal, String
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.notifications import CareNotification, CareNotificationPreference
from app.models.reminder import CareReminder

logger = logging.getLogger(__name__)
POLL_SECONDS = 30
BATCH_SIZE = 100


def _notification_id(reminder):
    generation = int(reminder.delivery_generation or 0)
    if generation == 0:
        return f"reminder-due:{reminder.id}"
    return f"reminder-due:{reminder.id}:{generation}"


def is_quiet_hours(preference, now):
    if preference is None or not preference.quiet_hours_enabled:
        return False
    try:
        local_now = now.astimezone(ZoneInfo(preference.timezone_name or "UTC"))
        start = time.fromisoformat(preference.quiet_hours_start or "21:00")
        end = time.fromisoformat(preference.quiet_hours_end or "08:00")
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return True
    current = local_now.timetz().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def deliver_due_reminders(db=None, now=None):
    """Create each due reminder notification once per due-time generation."""
    owns_session = db is None
    session = db or SessionLocal()
    current = now or datetime.now(timezone.utc)
    delivered = 0
    try:
        generated_id = case(
            (CareReminder.delivery_generation == 0, literal("reminder-due:") + CareReminder.id),
            else_=(literal("reminder-due:") + CareReminder.id + literal(":") + cast(CareReminder.delivery_generation, String)),
        )
        due = (
            session.query(CareReminder)
            .filter(
                CareReminder.status == "open",
                CareReminder.due_at <= current,
                ~exists().where(CareNotification.id == generated_id),
            )
            .order_by(CareReminder.due_at.asc(), CareReminder.created_at.asc())
            .limit(BATCH_SIZE)
            .all()
        )
        for reminder in due:
            preference = (
                session.query(CareNotificationPreference)
                .filter(
                    CareNotificationPreference.person_id == reminder.person_id,
                    CareNotificationPreference.caregiver_id == reminder.created_by_caregiver_id,
                )
                .first()
            )
            if preference is not None and not preference.reminders_enabled:
                continue
            if is_quiet_hours(preference, current):
                continue
            session.add(CareNotification(
                id=_notification_id(reminder),
                person_id=reminder.person_id,
                caregiver_id=reminder.created_by_caregiver_id,
                title=reminder.title,
                body=reminder.note or "This care reminder is due.",
                category="reminder_due",
                priority="normal",
                source_event_id=reminder.id,
            ))
            try:
                session.commit()
                delivered += 1
            except IntegrityError:
                session.rollback()
        return delivered
    finally:
        if owns_session:
            session.close()


async def reminder_delivery_loop():
    """Poll persisted reminders so due notifications recover after process restarts."""
    while True:
        try:
            await asyncio.to_thread(deliver_due_reminders)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Due reminder notification pass failed")
        await asyncio.sleep(POLL_SECONDS)
