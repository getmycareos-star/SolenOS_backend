"""PostgreSQL acceptance for the in-app-only notification policy.

Legacy browser-push subscriptions must be revoked and queued deliveries cancelled.
No external provider is called and no new browser subscription is persisted.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires migrated PostgreSQL DATABASE_URL",
)

from app.core.database import get_db
from app.main import app
from app.models.notifications import (
    CareNotification,
    CarePushDelivery,
    CarePushSubscription,
    NotificationDeliveryAttempt,
)
import app.services.reminder_delivery as delivery_service

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def test_legacy_browser_push_is_disabled_and_existing_deliveries_are_cancelled():
    notification_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("notification_delivery_attempts")
    }
    notification_id_length = getattr(notification_columns["notification_id"]["type"], "length", None)
    assert notification_id_length is None or notification_id_length >= 255, (
        "notification_delivery_attempts.notification_id must preserve opaque "
        "notification identifiers up to the 255-character model contract"
    )

    client = TestClient(app)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    notification_id = str(uuid.uuid4())
    subscription_id = str(uuid.uuid4())
    delivery_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    person_id = caregiver_id = None
    try:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={
                "name": f"In-app-only notification recipient {uuid.uuid4().hex[:8]}",
                "caregiver_name": "Primary caregiver",
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        headers = {"Authorization": f"Bearer {session['access_token']}"}

        capability = client.get("/api/v1/notifications/push/public-key", headers=headers)
        assert capability.status_code == 200, capability.text
        assert capability.json() == {"available": False, "public_key": None, "reason": "in_app_only"}

        rejected = client.post(
            "/api/v1/notifications/push/subscriptions",
            headers=headers,
            json={
                "endpoint": f"https://fcm.googleapis.com/fcm/send/legacy-{uuid.uuid4().hex}",
                "keys": {"p256dh": "legacy-public-key", "auth": "legacy-auth-key"},
            },
        )
        assert rejected.status_code == 410, rejected.text
        with Session() as db:
            assert db.query(CarePushSubscription).filter_by(
                person_id=person_id,
                caregiver_id=caregiver_id,
            ).count() == 0

        with Session() as db:
            notification = CareNotification(
                id=notification_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Private care reminder",
                body="Private care details stay in the SolenOS inbox.",
                category="reminder_due",
                priority="normal",
                created_at=now,
            )
            subscription = CarePushSubscription(
                id=subscription_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                endpoint_hash="a" * 64,
                encrypted_subscription="legacy-encrypted-subscription",
                user_agent="legacy browser",
                created_at=now,
                last_seen_at=now,
            )
            delivery = CarePushDelivery(
                id=delivery_id,
                notification_id=notification_id,
                subscription_id=subscription_id,
                status="retry_scheduled",
                attempt_count=1,
                next_attempt_at=now,
                last_error_code="http_503",
                last_error_message="Legacy provider retry must be cancelled.",
            )
            db.add_all([notification, subscription])
            db.flush()
            assert db.query(CarePushSubscription).filter_by(id=subscription_id).one()
            db.add(delivery)
            db.commit()

        with Session() as db:
            assert delivery_service.deliver_pending_web_pushes(db=db, now=now) == 0
            subscription = db.query(CarePushSubscription).filter_by(id=subscription_id).one()
            delivery = db.query(CarePushDelivery).filter_by(id=delivery_id).one()
            assert subscription.revoked_at == now
            assert delivery.status == "cancelled"
            assert delivery.last_error_code == "in_app_only_policy"
            assert "SolenOS inbox" in (delivery.last_error_message or "")
            assert db.query(NotificationDeliveryAttempt).filter_by(notification_id=notification_id).count() == 0

        # Re-running the worker is safe and must not create a provider attempt.
        with Session() as db:
            assert delivery_service.deliver_pending_web_pushes(db=db, now=now) == 0
            assert db.query(NotificationDeliveryAttempt).filter_by(notification_id=notification_id).count() == 0
    finally:
        app.dependency_overrides.clear()
        if Session is not None:
            with Session() as db:
                db.query(CarePushDelivery).filter_by(id=delivery_id).delete(synchronize_session=False)
                db.query(CarePushSubscription).filter_by(id=subscription_id).delete(synchronize_session=False)
                db.query(CareNotification).filter_by(id=notification_id).delete(synchronize_session=False)
                db.commit()
