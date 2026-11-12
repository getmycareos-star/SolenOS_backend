from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription
from app.models.privacy_delivery import NotificationReadReceipt


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


def setup_client(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    import app.main as main_module
    monkeypatch.setattr(main_module, "SessionLocal", TestSession)
    client = TestClient(app)
    first = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Family caregiver"})
    second = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Another caregiver"})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    return client, first.json(), second.json()


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_notification_preferences_are_saved_and_scoped(monkeypatch):
    client, first, second = setup_client(monkeypatch)
    try:
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}

        initial = client.get("/api/v1/notifications/preferences", headers=first_headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["quiet_hours_enabled"] is False
        assert initial.json()["reminders_enabled"] is True

        saved = client.put(
            "/api/v1/notifications/preferences",
            headers=first_headers,
            json={"quiet_hours_enabled": True, "reminders_enabled": False},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["quiet_hours_enabled"] is True
        assert saved.json()["reminders_enabled"] is False

        other = client.get("/api/v1/notifications/preferences", headers=second_headers)
        assert other.status_code == 200, other.text
        assert other.json()["quiet_hours_enabled"] is False
        assert other.json()["reminders_enabled"] is True
    finally:
        teardown()


def test_notifications_feed_unread_count_and_read_are_scoped(monkeypatch):
    client, first, second = setup_client(monkeypatch)
    try:
        first_headers = {"Authorization": f"Bearer {first['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        person_id = first["person"]["id"]
        caregiver_id = first["caregiver"]["id"]

        db = TestSession()
        try:
            item = CareNotification(
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Care update",
                body="A saved care update is available.",
                category="care_update",
                priority="normal",
            )
            resolved_item = CareNotification(
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Previously resolved care change",
                body="This change was superseded by newer saved information.",
                category="care_change",
                priority="normal",
                source_event_id="resolved-source",
                status="resolved",
                resolved_at=datetime.now(timezone.utc),
                resolution_reason="source_superseded",
            )
            db.add_all([item, resolved_item])
            db.commit()
            db.refresh(item)
            item_id = item.id
        finally:
            db.close()

        feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=first_headers)
        assert feed.status_code == 200, feed.text
        assert len(feed.json()["items"]) == 2
        active_item = next(row for row in feed.json()["items"] if row["id"] == item_id)
        resolved_feed_item = next(row for row in feed.json()["items"] if row["category"] == "care_change")
        assert active_item["is_read"] is False
        assert active_item["status"] == "active"
        assert resolved_feed_item["status"] == "resolved"
        assert resolved_feed_item["resolution_reason"] == "source_superseded"

        unread = client.get("/api/v1/notifications/unread-count", headers=first_headers)
        assert unread.status_code == 200, unread.text
        assert unread.json()["unread_count"] == 1

        other_feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=second_headers)
        assert other_feed.status_code == 200, other_feed.text
        assert other_feed.json()["items"] == []

        denied_read = client.patch(f"/api/v1/notifications/{item_id}/read", headers=second_headers, json={})
        assert denied_read.status_code == 404, denied_read.text

        marked = client.patch(f"/api/v1/notifications/{item_id}/read", headers=first_headers, json={})
        assert marked.status_code == 200, marked.text
        assert marked.json()["is_read"] is True
        db = TestSession()
        try:
            assert db.query(NotificationReadReceipt).filter_by(
                notification_id=item_id,
                reader_id=caregiver_id,
            ).count() == 1
        finally:
            db.close()
    finally:
        teardown()

def test_notification_feed_exposes_failed_push_delivery_truthfully(monkeypatch):
    client, first, second = setup_client(monkeypatch)
    try:
        db = TestSession()
        try:
            subscription = CarePushSubscription(
                id="failed-push-subscription",
                person_id=first["person"]["id"],
                caregiver_id=first["caregiver"]["id"],
                endpoint_hash="f" * 64,
                encrypted_subscription="encrypted-test-subscription",
            )
            notification = CareNotification(
                id="failed-push-notification",
                person_id=first["person"]["id"],
                caregiver_id=first["caregiver"]["id"],
                title="Call the clinic",
                body="Ask about the appointment.",
                category="reminder_due",
                priority="normal",
                source_event_id="appointment-follow-up-1",
            )
            db.add_all([subscription, notification])
            db.flush()
            db.add(CarePushDelivery(
                id="failed-push-delivery",
                notification_id=notification.id,
                subscription_id=subscription.id,
                status="failed",
                attempt_count=5,
                last_error_code="provider_error",
                last_error_message="Push delivery could not be completed",
            ))
            db.commit()
        finally:
            db.close()

        response = client.get(
            "/api/v1/notifications?limit=100&offset=0",
            headers={"Authorization": f"Bearer {first['access_token']}"},
        )
        assert response.status_code == 200, response.text
        item = next(row for row in response.json()["items"] if row["id"] == "failed-push-notification")
        assert item["push_delivery"]["status"] == "failed"
        assert item["push_delivery"]["failed_count"] == 1
        assert "remains available in your SolenOS inbox" in item["push_delivery"]["message"]
        assert "provider_error" not in item["push_delivery"]["message"]
        assert "delivered" not in item["push_delivery"]["message"].lower()

        # Delivery outcomes remain scoped to the caregiver who owns the notification.
        foreign = client.get(
            "/api/v1/notifications?limit=100&offset=0",
            headers={"Authorization": f"Bearer {second['access_token']}"},
        )
        assert foreign.status_code == 200, foreign.text
        assert all(row["id"] != "failed-push-notification" for row in foreign.json()["items"])
    finally:
        teardown()


def test_web_push_provider_failure_is_never_recorded_as_accepted(monkeypatch):
    import base64
    import hashlib
    import json
    import sys
    from datetime import timedelta
    from types import SimpleNamespace

    from cryptography.fernet import Fernet
    from app.services import reminder_delivery

    client, first, _second = setup_client(monkeypatch)
    try:
        person_id = first["person"]["id"]
        caregiver_id = first["caregiver"]["id"]
        secret = "test-secret-for-web-push-delivery"
        settings = SimpleNamespace(
            secret_key=secret,
            vapid_private_key="test-vapid-private-key",
            vapid_subject="mailto:tests@example.invalid",
        )
        monkeypatch.setattr(reminder_delivery, "get_settings", lambda: settings)
        monkeypatch.setattr(reminder_delivery, "vapid_configuration_error", lambda _settings: None)

        class ProviderRejected(Exception):
            response = None

        def reject_delivery(**_kwargs):
            raise ProviderRejected("simulated provider outage")

        monkeypatch.setitem(
            sys.modules,
            "pywebpush",
            SimpleNamespace(WebPushException=ProviderRejected, webpush=reject_delivery),
        )

        cipher_key = base64.urlsafe_b64encode(
            hashlib.sha256((secret + ":solenos-web-push-subscriptions:v1").encode("utf-8")).digest()
        )
        encrypted = Fernet(cipher_key).encrypt(
            json.dumps({
                "endpoint": "https://push.example.invalid/subscription",
                "keys": {"p256dh": "test-p256dh", "auth": "test-auth"},
            }).encode("utf-8")
        ).decode("ascii")

        now = datetime.now(timezone.utc) + timedelta(minutes=1)
        db = TestSession()
        try:
            subscription = CarePushSubscription(
                id="provider-failure-subscription",
                person_id=person_id,
                caregiver_id=caregiver_id,
                endpoint_hash="p" * 64,
                encrypted_subscription=encrypted,
                created_at=now - timedelta(minutes=2),
            )
            notification = CareNotification(
                id="provider-failure-notification",
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Call the clinic",
                body="Ask about the appointment.",
                category="reminder_due",
                priority="normal",
                status="active",
                created_at=now - timedelta(minutes=1),
            )
            db.add_all([subscription, notification])
            db.flush()
            delivery = CarePushDelivery(
                id="provider-failure-delivery",
                notification_id=notification.id,
                subscription_id=subscription.id,
                status="pending",
                attempt_count=0,
                next_attempt_at=now,
            )
            db.add(delivery)
            db.commit()

            current = now
            for delay in (0, 60, 300, 1800, 7200):
                current = now + timedelta(seconds=delay) if delay == 0 else current + timedelta(seconds=delay)
                accepted = reminder_delivery.deliver_pending_web_pushes(db=db, now=current)
                assert accepted == 0
                db.refresh(delivery)

            assert delivery.status == "failed"
            assert delivery.attempt_count == 5
            assert delivery.provider_accepted_at is None
            assert delivery.last_error_code == "provider_error"
            assert delivery.last_error_message == "Push provider rejected the request"
        finally:
            db.close()
    finally:
        teardown()
