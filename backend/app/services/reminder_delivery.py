"""Durable in-app reminder notification worker and legacy browser-push cleanup.

Quiet hours defer notification creation; they do not alter reminder due times.
All caregiver notifications remain inside the authenticated SolenOS inbox.
"""
import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import String, case, cast, exists, literal, or_
from sqlalchemy.exc import IntegrityError


from app.core.database import SessionLocal
from app.models.notifications import CareNotification, CareNotificationPreference, CarePushDelivery, CarePushSubscription
from app.models.reminder import CareReminder
from app.models.caregiver_access import CaregiverAccessGrant

# All care notifications stay in the authenticated SolenOS inbox. No external channel is enabled.

logger = logging.getLogger(__name__)
POLL_SECONDS = 30
BATCH_SIZE = 100


def _notification_id(reminder):
    generation = int(reminder.delivery_generation or 0)
    if generation == 0:
        return f"reminder-due:{reminder.id}"
    return f"reminder-due:{reminder.id}:{generation}"


def _quiet_hours_end_utc(local_date, end_time, zone):
    """Resolve a local quiet-hours end conservatively across DST transitions."""
    local_end = datetime.combine(local_date, end_time)
    candidates = [
        local_end.replace(tzinfo=zone, fold=0).astimezone(timezone.utc),
        local_end.replace(tzinfo=zone, fold=1).astimezone(timezone.utc),
    ]
    valid_candidates = [
        candidate for candidate in candidates
        if candidate.astimezone(zone).replace(tzinfo=None) == local_end
    ]
    # Ambiguous times have two valid instants: use the later one. Nonexistent
    # times have none: use the later UTC candidate, after the forward clock jump.
    return max(valid_candidates or candidates)


def _quiet_hours_state(preference, now):
    """Return (is_quiet, release_at) in UTC; fail closed on invalid settings."""
    if preference is None or not preference.quiet_hours_enabled:
        return False, None
    try:
        zone = ZoneInfo(preference.timezone_name or "UTC")
        local_now = now.astimezone(zone)
        start = time.fromisoformat(preference.quiet_hours_start or "21:00")
        end = time.fromisoformat(preference.quiet_hours_end or "08:00")
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return True, now + timedelta(hours=1)

    current = local_now.timetz().replace(tzinfo=None)
    if start == end:
        release_date = local_now.date() + timedelta(days=1)
        return True, _quiet_hours_end_utc(release_date, end, zone)

    if start < end:
        release_date = local_now.date()
        release_at = _quiet_hours_end_utc(release_date, end, zone)
        quiet = start <= current < end or (current >= end and now.astimezone(timezone.utc) < release_at)
        return quiet, release_at if quiet else None

    # Overnight interval. During a fall-back repeated end hour, local wall time
    # alone would incorrectly release notifications at the first occurrence.
    release_date = local_now.date() + (timedelta(days=1) if current >= start else timedelta())
    release_at = _quiet_hours_end_utc(release_date, end, zone)
    quiet = current >= start or current < end or (
        current >= end and now.astimezone(timezone.utc) < release_at
    )
    return quiet, release_at if quiet else None


def is_quiet_hours(preference, now):
    return _quiet_hours_state(preference, now)[0]


def quiet_hours_release_at(preference, now):
    """Return the next local quiet-hours end as UTC, or None when not quiet."""
    return _quiet_hours_state(preference, now)[1]

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
        # Cancel stale pending deliveries for categories that are now in-app only.
        # They must never be retried to caregiver devices.
        in_app_only_pending = (
            session.query(CarePushDelivery)
            .join(CareNotification, CareNotification.id == CarePushDelivery.notification_id)
            .filter(
                CarePushDelivery.status.in_(("pending", "retry_scheduled")),
            )
            .all()
        )
        for delivery in in_app_only_pending:
            delivery.status = "cancelled"
            delivery.last_error_code = "in_app_only_category"
            delivery.last_error_message = "This care message stays in the in-app inbox."
        if in_app_only_pending:
            session.commit()

        try:
            from app.services.proactive_care_communications import sync_open_loop_due_notifications
            if sync_open_loop_due_notifications(session, now=current):
                session.commit()
        except Exception:
            logger.exception("Open-loop deadline notification sync failed")
            session.rollback()

        due = (
            session.query(CareReminder)
            .filter(
                CareReminder.status == "open",
                CareReminder.due_at <= current,
                or_(
                    CareReminder.notification_deferred_until.is_(None),
                    CareReminder.notification_deferred_until <= current,
                ),
                ~exists().where(CareNotification.id == generated_id),
            )
            .order_by(CareReminder.due_at.asc(), CareReminder.created_at.asc())
            .limit(BATCH_SIZE)
            .all()
        )
        for reminder in due:
            recipient_caregiver_id = reminder.assigned_to_caregiver_id or reminder.created_by_caregiver_id
            recipient_grant = session.query(CaregiverAccessGrant.id).filter(
                CaregiverAccessGrant.person_id == reminder.person_id,
                CaregiverAccessGrant.caregiver_id == recipient_caregiver_id,
                CaregiverAccessGrant.status == "active",
                CaregiverAccessGrant.revoked_at.is_(None),
                CaregiverAccessGrant.access_scope == "full_record",
            ).first()
            if recipient_grant is None:
                # Do not disclose care reminders to a caregiver whose access has
                # expired or been revoked. Keep the reminder open and retry after
                # a bounded delay; never silently reassign responsibility.
                reminder.notification_deferred_until = current + timedelta(hours=1)
                session.commit()
                continue
            preference = (
                session.query(CareNotificationPreference)
                .filter(
                    CareNotificationPreference.person_id == reminder.person_id,
                    CareNotificationPreference.caregiver_id == recipient_caregiver_id,
                )
                .first()
            )
            if preference is not None and not preference.reminders_enabled:
                reminder.notification_deferred_until = datetime(9999, 12, 31, tzinfo=timezone.utc)
                session.commit()
                continue
            release_at = quiet_hours_release_at(preference, current)
            if release_at is not None:
                reminder.notification_deferred_until = release_at
                session.commit()
                continue
            session.add(CareNotification(
                id=_notification_id(reminder),
                person_id=reminder.person_id,
                caregiver_id=recipient_caregiver_id,
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




def deliver_pending_web_pushes(db=None, now=None):
    """Cancel legacy browser-push work and revoke subscriptions; never contact a provider."""
    owns_session = db is None
    session = db or SessionLocal()
    current = now or datetime.now(timezone.utc)
    try:
        active_subscriptions = session.query(CarePushSubscription).filter(
            CarePushSubscription.revoked_at.is_(None)
        ).all()
        for subscription in active_subscriptions:
            subscription.revoked_at = current

        pending_deliveries = session.query(CarePushDelivery).filter(
            CarePushDelivery.status.in_(("pending", "retry_scheduled"))
        ).all()
        for delivery in pending_deliveries:
            delivery.status = "cancelled"
            delivery.last_error_code = "in_app_only_policy"
            delivery.last_error_message = "Notifications are available only in the SolenOS inbox."

        if active_subscriptions or pending_deliveries:
            session.commit()
        return 0
    except Exception:
        session.rollback()
        logger.exception("Could not close legacy browser-push subscriptions under the in-app-only policy")
        raise
    finally:
        if owns_session:
            session.close()


async def reminder_delivery_loop():
    """Poll persisted reminders so due notifications recover after process restarts."""
    while True:
        try:
            await asyncio.to_thread(deliver_due_reminders)
            await asyncio.to_thread(deliver_pending_web_pushes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Due reminder notification pass failed")
        await asyncio.sleep(POLL_SECONDS)
