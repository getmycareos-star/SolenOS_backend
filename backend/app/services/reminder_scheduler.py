"""Optional in-process polling loop for the persistent reminder queue.

Only starts when a valid HTTPS delivery endpoint and token are configured.
The database claim is atomic, so multiple API workers do not claim the same
scheduled reminder simultaneously. Provider-side idempotency remains required.
"""
import asyncio
import logging
import os
from urllib.parse import urlparse

from app.services.reminder_delivery import run_configured_reminder_worker

logger = logging.getLogger(__name__)
_scheduler_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


def _delivery_is_configured() -> bool:
    endpoint = os.getenv("REMINDER_DELIVERY_URL", "").strip()
    token = os.getenv("REMINDER_DELIVERY_TOKEN", "").strip()
    parsed = urlparse(endpoint)
    return parsed.scheme == "https" and bool(parsed.netloc) and bool(token)


async def _poll() -> None:
    interval = max(10, min(int(os.getenv("REMINDER_POLL_INTERVAL_SECONDS", "30")), 300))
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await asyncio.to_thread(run_configured_reminder_worker, 50)
        except Exception:
            logger.exception("Reminder worker cycle failed; persistent queue remains available for retry")
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def start_reminder_scheduler() -> None:
    global _scheduler_task, _stop_event
    if not _delivery_is_configured():
        logger.info("Reminder scheduler not started: configure HTTPS REMINDER_DELIVERY_URL and REMINDER_DELIVERY_TOKEN")
        return
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_poll(), name="solenos-reminder-worker")
    logger.info("Persistent reminder scheduler started")


async def stop_reminder_scheduler() -> None:
    global _scheduler_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _scheduler_task is not None:
        try:
            await asyncio.wait_for(_scheduler_task, timeout=20)
        except asyncio.TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except asyncio.CancelledError:
                pass
    _scheduler_task = None
    _stop_event = None
