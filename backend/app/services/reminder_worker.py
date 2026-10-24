import asyncio
import logging
import os
from app.core.database import SessionLocal
from app.services.reminder_scheduler import materialize_due_reminders, deliver_pending_receipts

logger = logging.getLogger(__name__)
_STOP = object()


def run_reminder_scheduler_once() -> dict:
    db = SessionLocal()
    try:
        created = materialize_due_reminders(db)
        delivered = deliver_pending_receipts(db)
        return {"occurrences_created": created, "receipts_delivered": delivered}
    finally:
        db.close()


async def reminder_scheduler_loop():
    interval = max(5, int(os.getenv("REMINDER_SCHEDULER_INTERVAL_SECONDS", "15")))
    while True:
        try:
            result = await asyncio.to_thread(run_reminder_scheduler_once)
            if result["occurrences_created"] or result["receipts_delivered"]:
                logger.info("Reminder scheduler processed %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Persistent due rows remain in the database and will be retried on
            # the next poll, including after process restarts.
            logger.exception("Reminder scheduler iteration failed; due work remains persisted")
        await asyncio.sleep(interval)
