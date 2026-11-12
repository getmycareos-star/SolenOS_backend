"""Deliver an already-generated due-reminder notification for Web Push E2E.

This helper is called only by the isolated PostgreSQL/Chromium CI workflow.
It never prints subscription endpoints, encryption keys, VAPID keys, or care text.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription
from app.models.reminder import CareReminder
from app.services.reminder_delivery import deliver_pending_web_pushes


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: create_and_deliver_web_push_test_notification.py PERSON_ID CAREGIVER_ID REMINDER_ID")

    person_id, caregiver_id, reminder_id = sys.argv[1], sys.argv[2], sys.argv[3]
    with SessionLocal() as db:
        reminder = db.query(CareReminder).filter(
            CareReminder.id == reminder_id,
            CareReminder.person_id == person_id,
            CareReminder.created_by_caregiver_id == caregiver_id,
        ).one_or_none()
        if reminder is None:
            raise SystemExit("the requested reminder is not owned by this caregiver")

        notification = db.query(CareNotification).filter(
            CareNotification.source_event_id == reminder_id,
            CareNotification.person_id == person_id,
            CareNotification.caregiver_id == caregiver_id,
            CareNotification.category == "reminder_due",
        ).one_or_none()
        if notification is None:
            raise SystemExit("the reminder worker has not created its due notification")

        subscriptions = db.query(CarePushSubscription).filter(
            CarePushSubscription.person_id == person_id,
            CarePushSubscription.caregiver_id == caregiver_id,
            CarePushSubscription.revoked_at.is_(None),
        ).all()
        if len(subscriptions) != 1:
            raise SystemExit(f"expected exactly one active browser subscription; found {len(subscriptions)}")

        accepted = deliver_pending_web_pushes(db=db, now=datetime.now(timezone.utc) + timedelta(seconds=2))
        delivery = db.query(CarePushDelivery).filter(
            CarePushDelivery.notification_id == notification.id,
            CarePushDelivery.subscription_id == subscriptions[0].id,
        ).one_or_none()
        if delivery is None or delivery.status != "accepted" or delivery.provider_accepted_at is None:
            raise SystemExit(
                "real push provider did not accept the due-reminder notification "
                f"(accepted_now={accepted}, delivery_status={getattr(delivery, 'status', None)!r})"
            )

    print(json.dumps({
        "notification_id": notification.id,
        "delivery_status": "accepted",
        "provider_accepted": True,
        "source_reminder_id": reminder_id,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
