"""PostgreSQL lifecycle acceptance for notification preferences and read receipts.

Uses the migrated PostgreSQL schema and real authenticated HTTP routes. No
SQLite, mocked database session, or external push-provider call is involved.
"""
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires migrated PostgreSQL DATABASE_URL",
)

from app.core.database import get_db
from app.main import app
from app.models.notifications import CareNotification, CarePushSubscription
from app.models.privacy_delivery import NotificationReadReceipt

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def test_notification_preferences_read_receipts_and_push_subscriptions_survive_new_session_and_enforce_scope(monkeypatch):
    suffix = uuid.uuid4().hex[:12]
    client = TestClient(app)
    created_ids = []

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        first = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Notification recipient {suffix}", "caregiver_name": f"Notification caregiver {suffix}"},
        )
        assert first.status_code == 201, first.text
        first_session = first.json()
        first_headers = {"Authorization": f"Bearer {first_session['access_token']}"}

        account = client.post(
            "/api/v1/accounts",
            headers=first_headers,
            json={"email": f"notification-{suffix}@example.test", "password": f"Notification-password-{suffix}"},
        )
        assert account.status_code == 201, account.text

        initial = client.get("/api/v1/notifications/preferences", headers=first_headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["quiet_hours_enabled"] is False
        assert initial.json()["reminders_enabled"] is True

        saved = client.put(
            "/api/v1/notifications/preferences",
            headers=first_headers,
            json={
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:15",
                "quiet_hours_end": "07:30",
                "timezone_name": "Africa/Lagos",
                "reminders_enabled": False,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["quiet_hours_enabled"] is True
        assert saved.json()["quiet_hours_start"] == "22:15"
        assert saved.json()["quiet_hours_end"] == "07:30"
        assert saved.json()["timezone_name"] == "Africa/Lagos"
        assert saved.json()["reminders_enabled"] is False

        # New bearer session after account login must retrieve the same rows.
        login = client.post(
            "/api/v1/accounts/login",
            json={"email": f"notification-{suffix}@example.test", "password": f"Notification-password-{suffix}"},
        )
        assert login.status_code == 200, login.text
        restored_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        restored = client.get("/api/v1/notifications/preferences", headers=restored_headers)
        assert restored.status_code == 200, restored.text
        assert restored.json()["quiet_hours_enabled"] is True
        assert restored.json()["quiet_hours_start"] == "22:15"
        assert restored.json()["quiet_hours_end"] == "07:30"
        assert restored.json()["timezone_name"] == "Africa/Lagos"
        assert restored.json()["reminders_enabled"] is False

        # Exercise durable encrypted subscription ownership without calling an
        # external push provider. The provider is deliberately not part of this
        # database persistence test.
        import app.api.push_notifications as push_api
        monkeypatch.setattr(push_api, "vapid_configuration_error", lambda _settings: None)
        monkeypatch.setattr(
            push_api,
            "get_settings",
            lambda: SimpleNamespace(secret_key=f"postgres-push-test-secret-{suffix}"),
        )
        push_payload = {
            "endpoint": f"https://fcm.googleapis.com/fcm/send/solenos-test-{suffix}",
            "keys": {"p256dh": f"p256dh-{suffix}", "auth": f"auth-{suffix}"},
        }
        registered = client.post(
            "/api/v1/notifications/push/subscriptions",
            headers=restored_headers,
            json=push_payload,
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["registered"] is True
        with Session() as db:
            subscription = db.query(CarePushSubscription).filter_by(
                person_id=first_session["person"]["id"],
                caregiver_id=first_session["caregiver"]["id"],
            ).one()
            subscription_id = subscription.id
            assert subscription.revoked_at is None
            assert subscription.last_seen_at is not None
            assert push_payload["endpoint"] not in subscription.encrypted_subscription
            assert push_payload["keys"]["p256dh"] not in subscription.encrypted_subscription
            assert push_payload["keys"]["auth"] not in subscription.encrypted_subscription

        # Replaying the same endpoint under a new session updates one row.
        replayed = client.post(
            "/api/v1/notifications/push/subscriptions",
            headers=restored_headers,
            json=push_payload,
        )
        assert replayed.status_code == 201, replayed.text
        with Session() as db:
            assert db.query(CarePushSubscription).filter_by(
                endpoint_hash=push_api._endpoint_hash(push_payload["endpoint"])
            ).count() == 1

        notification = CareNotification(
            person_id=first_session["person"]["id"],
            caregiver_id=first_session["caregiver"]["id"],
            title=f"PostgreSQL notification {suffix}",
            body="Synthetic persistence acceptance notification.",
            category="care_update",
            priority="normal",
        )
        with Session() as db:
            db.add(notification)
            db.commit()
            db.refresh(notification)
            notification_id = notification.id
        created_ids.append(notification_id)

        feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=restored_headers)
        assert feed.status_code == 200, feed.text
        saved_notification = next(item for item in feed.json()["items"] if item["id"] == notification_id)
        assert saved_notification["is_read"] is False

        marked = client.patch(
            f"/api/v1/notifications/{notification_id}/read",
            headers=restored_headers,
            json={},
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["is_read"] is True

        with Session() as db:
            receipt = db.query(NotificationReadReceipt).filter_by(
                notification_id=notification_id,
                reader_id=first_session["caregiver"]["id"],
            ).one()
            assert receipt.person_id == first_session["person"]["id"]
            assert receipt.caregiver_id == first_session["caregiver"]["id"]
            assert receipt.read_at is not None

        second = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": f"Other notification recipient {suffix}", "caregiver_name": f"Other caregiver {suffix}"},
        )
        assert second.status_code == 201, second.text
        second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
        foreign_feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=second_headers)
        assert foreign_feed.status_code == 200, foreign_feed.text
        assert all(item["id"] != notification_id for item in foreign_feed.json()["items"])
        denied = client.patch(
            f"/api/v1/notifications/{notification_id}/read",
            headers=second_headers,
            json={},
        )
        assert denied.status_code == 404, denied.text

        foreign_subscription = client.post(
            "/api/v1/notifications/push/subscriptions",
            headers=second_headers,
            json=push_payload,
        )
        assert foreign_subscription.status_code == 409, foreign_subscription.text

        revoked = client.request(
            "DELETE",
            "/api/v1/notifications/push/subscriptions",
            headers=restored_headers,
            json={"endpoint": push_payload["endpoint"]},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] is True
        with Session() as db:
            subscription = db.query(CarePushSubscription).filter_by(id=subscription_id).one()
            assert subscription.revoked_at is not None
    finally:
        app.dependency_overrides.clear()
        if Session is not None and created_ids:
            with Session() as db:
                db.query(NotificationReadReceipt).filter(NotificationReadReceipt.notification_id.in_(created_ids)).delete(synchronize_session=False)
                db.query(CareNotification).filter(CareNotification.id.in_(created_ids)).delete(synchronize_session=False)
                db.commit()
