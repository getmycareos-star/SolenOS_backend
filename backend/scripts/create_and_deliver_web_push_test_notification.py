"""Create and deliver one synthetic notification for real-browser Web Push E2E.

This script is called only by the isolated PostgreSQL/Chromium CI workflow.
It never prints subscription endpoints, encryption keys, VAPID keys, or care text.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import SessionLocal
from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription
from app.services.reminder_delivery import deliver_pending_web_pushes


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: create_and_deliver_web_push_test_notification.py PERSON_ID CAREGIVER_ID")

    person_id, caregiver_id = sys.argv[1], sys.argv[2]
    notification_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)

    with SessionLocal() as db:
        subscriptions = db.query(CarePushSubscription).filter(
            CarePushSubscription.person_id == person_id,
            CarePushSubscription.caregiver_id == caregiver_id,
            CarePushSubscription.revoked_at.is_(None),
        ).all()
        if len(subscriptions) != 1:
            raise SystemExit(f"expected exactly one active browser subscription; found {len(subscriptions)}")

        db.add(CareNotification(
            id=notification_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            title="Synthetic private reminder title",
            body="Synthetic private care details must never reach the lock screen.",
            category="reminder_due",
            priority="normal",
            created_at=created_at,
        ))
        db.commit()

        accepted = deliver_pending_web_pushes(
            db=db,
            now=created_at + timedelta(seconds=2),
        )
        delivery = db.query(CarePushDelivery).filter(
            CarePushDelivery.notification_id == notification_id,
            CarePushDelivery.subscription_id == subscriptions[0].id,
        ).one_or_none()
        if accepted != 1 or delivery is None or delivery.status != "accepted" or delivery.provider_accepted_at is None:
            raise SystemExit(
                "real push provider did not accept the synthetic browser notification "
                f"(accepted={accepted}, delivery_status={getattr(delivery, 'status', None)!r})"
            )

    print(json.dumps({
        "notification_id": notification_id,
        "delivery_status": "accepted",
        "provider_accepted": True,
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
