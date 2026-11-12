"""Durable in-app delivery for due care reminders.

This worker writes into the existing caregiver notification feed. It does not
claim to send Web Push or native device notifications.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.notifications import CareNotification, CareNotificationPreference
from app.models.reminder import CareReminder

logger = logging.getLogger(__name__)
POLL_SECONDS = 30
BATCH_SIZE = 100


def deliver_due_reminders(db=None, now=None):
    """Create each due reminder notification once, using its deterministic ID."""
    owns_session = db is None
    session = db or SessionLocal()
    current = now or datetime.now(timezone.utc)
    delivered = 0
    try:
        due = (
            session.query(CareReminder)
            .filter(CareReminder.status == "open", CareReminder.due_at <= current)
            .order_by(CareReminder.due_at.asc(), CareReminder.created_at.asc())
            .limit(BATCH_SIZE)
            .all()
        )
        for reminder in due:
            notification_id = f"reminder-due:{reminder.id}"
            if session.get(CareNotification, notification_id) is not None:
                continue
            preference = (
                session.query(CareNotificationPreference)
                .filter(
                    CareNotificationPreference.person_id == reminder.person_id,
                    CareNotificationPreference.caregiver_id == reminder.created_by_caregiver_id,
                )
                .first()
            )
            if preference is not None and (
                not preference.reminders_enabled or preference.quiet_hours_enabled
            ):
                continue
            body = reminder.note or "This care reminder is due."
            session.add(CareNotification(
                id=notification_id,
                person_id=reminder.person_id,
                caregiver_id=reminder.created_by_caregiver_id,
                title=reminder.title,
                body=body,
                category="reminder_due",
                priority="normal",
                source_event_id=reminder.id,
            ))
            try:
                session.commit()
                delivered += 1
            except IntegrityError:
                # Another worker may have inserted the deterministic notification.
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
