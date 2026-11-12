"""Durable reminder and Web Push delivery workers.

Quiet hours defer notification creation; they do not alter reminder due times.
Provider acceptance is recorded separately from device presentation.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, cast, exists, literal, or_, String
from sqlalchemy.exc import IntegrityError

from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.notifications import CareNotification, CareNotificationPreference, CarePushDelivery, CarePushSubscription

# Care changes and clarification requests stay in the authenticated inbox.
# Browser push is reserved for explicitly due reminders.
PUSH_NOTIFICATION_CATEGORIES = frozenset({"reminder_due"})
from app.models.reminder import CareReminder
from app.services.web_push_config import vapid_configuration_error

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
                CarePushDelivery.status == "pending",
                CareNotification.category.notin_(PUSH_NOTIFICATION_CATEGORIES),
            )
            .all()
        )
        for delivery in in_app_only_pending:
            delivery.status = "cancelled"
            delivery.last_error_code = "in_app_only_category"
            delivery.last_error_message = "This care message stays in the in-app inbox."
        if in_app_only_pending:
            session.commit()

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
            preference = (
                session.query(CareNotificationPreference)
                .filter(
                    CareNotificationPreference.person_id == reminder.person_id,
                    CareNotificationPreference.caregiver_id == reminder.created_by_caregiver_id,
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


def deliver_pending_web_pushes(db=None, now=None):
    """Send privacy-preserving Web Push payloads and persist provider receipts/retries."""
    settings = get_settings()
    configuration_error = vapid_configuration_error(settings)
    if configuration_error is not None:
        logger.error("Web Push is disabled: %s", configuration_error)
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("Web Push is configured but pywebpush is not installed")
        return 0

    owns_session = db is None
    session = db or SessionLocal()
    current = now or datetime.now(timezone.utc)
    accepted = 0
    secret = settings.secret_key or "local-development-only-not-for-production"
    cipher_key = base64.urlsafe_b64encode(
        hashlib.sha256((secret + ":solenos-web-push-subscriptions:v1").encode("utf-8")).digest()
    )
    cipher = Fernet(cipher_key)
    try:
        # A delivery can become stale before its retry time arrives. Cancel it
        # even when it is no longer eligible for the due query below; otherwise
        # superseded/in-app-only messages remain pending forever.
        in_app_only_pending = (
            session.query(CarePushDelivery)
            .join(CareNotification, CareNotification.id == CarePushDelivery.notification_id)
            .filter(
                CarePushDelivery.status == "pending",
                CareNotification.status == "active",
                CareNotification.category.notin_(PUSH_NOTIFICATION_CATEGORIES),
            )
            .all()
        )
        for delivery in in_app_only_pending:
            delivery.status = "cancelled"
            delivery.last_error_code = "in_app_only_category"
            delivery.last_error_message = "This care message stays in the in-app inbox."
        if in_app_only_pending:
            session.commit()

        stale_pending = (
            session.query(CarePushDelivery)
            .join(CareNotification, CareNotification.id == CarePushDelivery.notification_id)
            .join(CarePushSubscription, CarePushSubscription.id == CarePushDelivery.subscription_id)
            .filter(
                CarePushDelivery.status == "pending",
                or_(
                    CareNotification.status != "active",
                    CarePushSubscription.revoked_at.is_not(None),
                ),
            )
            .all()
        )
        for delivery in stale_pending:
            delivery.status = "cancelled"
            delivery.last_error_code = "notification_no_longer_deliverable"
            delivery.last_error_message = "The source is no longer active or this device is no longer subscribed."
        if stale_pending:
            session.commit()

        # Only notify a device about events created after that device was linked.
        subscriptions = session.query(CarePushSubscription).filter(
            CarePushSubscription.revoked_at.is_(None)
        ).all()
        for subscription in subscriptions:
            notifications = session.query(CareNotification).filter(
                CareNotification.person_id == subscription.person_id,
                CareNotification.caregiver_id == subscription.caregiver_id,
                CareNotification.created_at >= subscription.created_at,
                CareNotification.status == "active",
                CareNotification.category.in_(PUSH_NOTIFICATION_CATEGORIES),
            ).all()
            for notification in notifications:
                existing = session.query(CarePushDelivery.id).filter(
                    CarePushDelivery.notification_id == notification.id,
                    CarePushDelivery.subscription_id == subscription.id,
                ).first()
                if existing is None:
                    session.add(CarePushDelivery(
                        id=str(uuid.uuid4()),
                        notification_id=notification.id,
                        subscription_id=subscription.id,
                        status="pending",
                        attempt_count=0,
                        next_attempt_at=current,
                    ))
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()

        due = (
            session.query(CarePushDelivery, CareNotification, CarePushSubscription)
            .join(CareNotification, CareNotification.id == CarePushDelivery.notification_id)
            .join(CarePushSubscription, CarePushSubscription.id == CarePushDelivery.subscription_id)
            .filter(
                CarePushDelivery.status == "pending",
                CareNotification.status == "active",
                CareNotification.category.in_(PUSH_NOTIFICATION_CATEGORIES),
                CarePushDelivery.next_attempt_at <= current,
                CarePushDelivery.attempt_count < 5,
                CarePushSubscription.revoked_at.is_(None),
            )
            .order_by(CarePushDelivery.next_attempt_at.asc())
            .with_for_update(skip_locked=True)
            .limit(100)
            .all()
        )
        retry_delays = (60, 300, 1800, 7200, 21600)
        for delivery, notification, subscription in due:
            # Re-read lifecycle state immediately before contacting the push
            # provider. A source may have been resolved after the due query.
            session.refresh(notification)
            if notification.status != "active":
                delivery.status = "cancelled"
                delivery.last_error_code = "notification_no_longer_active"
                delivery.last_error_message = "The care source no longer requires this notification."
                session.commit()
                continue
            preference = session.query(CareNotificationPreference).filter(
                CareNotificationPreference.person_id == notification.person_id,
                CareNotificationPreference.caregiver_id == notification.caregiver_id,
            ).first()
            if preference is not None and not preference.reminders_enabled:
                delivery.status = "cancelled"
                delivery.last_error_code = "reminders_disabled"
                delivery.last_error_message = "Caregiver disabled reminder notifications"
                session.commit()
                continue
            release_at = quiet_hours_release_at(preference, current)
            if release_at is not None and notification.priority not in {"emergency", "critical", "urgent"}:
                delivery.next_attempt_at = release_at
                session.commit()
                continue
            delivery.attempt_count += 1
            delivery.last_attempt_at = current
            try:
                serialized = cipher.decrypt(
                    subscription.encrypted_subscription.encode("ascii")
                ).decode("utf-8")
                subscription_info = json.loads(serialized)
                # Never put care details on a lock screen. The caregiver opens
                # SolenOS to inspect the authenticated notification feed.
                payload = json.dumps({
                    "title": "SolenOS",
                    "body": "You have a care update. Open SolenOS to review it.",
                    "url": "/workspace",
                    "notification_id": notification.id,
                })
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=settings.vapid_private_key.replace("\\n", "\n"),
                    vapid_claims={"sub": settings.vapid_subject},
                    ttl=60 * 60,
                )
                delivery.status = "accepted"
                delivery.provider_accepted_at = current
                delivery.last_error_code = None
                delivery.last_error_message = None
                accepted += 1
                session.commit()
            except InvalidToken:
                # SECRET_KEY rotation invalidates the encrypted subscription.
                # Revoke it so the caregiver can register this browser again.
                subscription.revoked_at = current
                delivery.status = "expired"
                delivery.last_error_code = "subscription_decryption_failed"
                delivery.last_error_message = "Browser subscription could not be decrypted; re-registration is required"
                session.commit()
            except WebPushException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                delivery.last_error_code = f"http_{status_code}" if status_code else "provider_error"
                delivery.last_error_message = "Push provider rejected the request"
                if status_code in (404, 410):
                    subscription.revoked_at = current
                    delivery.status = "expired"
                elif delivery.attempt_count >= 5:
                    delivery.status = "failed"
                else:
                    delivery.next_attempt_at = current + timedelta(seconds=retry_delays[delivery.attempt_count - 1])
                session.commit()
            except Exception as exc:
                delivery.last_error_code = type(exc).__name__[:64]
                delivery.last_error_message = "Push delivery could not be completed"
                if delivery.attempt_count >= 5:
                    delivery.status = "failed"
                else:
                    delivery.next_attempt_at = current + timedelta(seconds=retry_delays[delivery.attempt_count - 1])
                session.commit()
        return accepted
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
