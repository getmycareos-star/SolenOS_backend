"""PostgreSQL integration for durable Web Push attempts, retry and recovery."""
from datetime import datetime, timedelta, timezone
import sys
from types import SimpleNamespace
import uuid
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.notifications import (
    CareNotification,
    CarePushDelivery,
    CarePushSubscription,
    DeliveryFailureEvent,
    NotificationDeliveryAttempt,
)
import app.api.push_notifications as push_api
import app.services.reminder_delivery as delivery_service

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def _valid_vapid_settings(secret_key: str):
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    public_key = base64.urlsafe_b64encode(public).rstrip(b"=").decode("ascii")
    return SimpleNamespace(
        vapid_public_key=public_key,
        vapid_private_key=pem,
        vapid_subject="mailto:support@solenosai.net",
        secret_key=secret_key,
    )


def test_web_push_failure_retry_and_provider_acceptance_are_durable_and_idempotent(monkeypatch):
    settings = _valid_vapid_settings("postgres-delivery-test-secret")
    monkeypatch.setattr(push_api, "get_settings", lambda: settings)
    monkeypatch.setattr(delivery_service, "get_settings", lambda: settings)

    calls = []

    class FakeWebPushException(Exception):
        def __init__(self, status_code: int):
            super().__init__("synthetic provider outage")
            self.response = SimpleNamespace(status_code=status_code)

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise FakeWebPushException(503)
        return SimpleNamespace(status_code=201)

    monkeypatch.setitem(
        sys.modules,
        "pywebpush",
        SimpleNamespace(WebPushException=FakeWebPushException, webpush=fake_webpush),
    )

    client = TestClient(app)
    created = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": f"Push retry recipient {uuid.uuid4().hex[:8]}", "caregiver_name": "Primary caregiver"},
    )
    assert created.status_code == 201, created.text
    session = created.json()
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4().hex}"
    registered = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=headers,
        json={"endpoint": endpoint, "keys": {"p256dh": "test-public-key", "auth": "test-auth-secret"}},
    )
    assert registered.status_code == 201, registered.text

    with Session() as db:
        subscription = db.query(CarePushSubscription).filter_by(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ).one()
        notification_time = subscription.created_at + timedelta(seconds=2)
        notification = CareNotification(
            id=str(uuid.uuid4()),
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="Private care reminder title",
            body="Private care details must never be sent to the lock screen.",
            category="reminder_due",
            priority="normal",
            created_at=notification_time,
        )
        db.add(notification)
        db.commit()
        notification_id = notification.id

    first_attempt_at = notification_time + timedelta(seconds=1)
    with Session() as db:
        accepted = delivery_service.deliver_pending_web_pushes(db=db, now=first_attempt_at)
        assert accepted == 0

    with Session() as db:
        delivery = db.query(CarePushDelivery).filter_by(notification_id=notification_id).one()
        failures = db.query(DeliveryFailureEvent).filter_by(delivery_id=delivery.id).all()
        attempts = db.query(NotificationDeliveryAttempt).filter_by(notification_id=notification_id).all()
        assert delivery.status == "retry_scheduled"
        assert delivery.attempt_count == 1
        assert delivery.next_attempt_at == first_attempt_at + timedelta(seconds=60)
        assert len(failures) == 1
        assert failures[0].failure_code == "http_503"
        assert failures[0].retryable is True
        assert failures[0].resolved_at is None
        assert len(attempts) == 1
        assert attempts[0].result == "retry_scheduled"
        assert attempts[0].attempt_number == 1

    retry_at = first_attempt_at + timedelta(seconds=61)
    with Session() as db:
        accepted = delivery_service.deliver_pending_web_pushes(db=db, now=retry_at)
        assert accepted == 1

    with Session() as db:
        delivery = db.query(CarePushDelivery).filter_by(notification_id=notification_id).one()
        failures = db.query(DeliveryFailureEvent).filter_by(delivery_id=delivery.id).all()
        attempts = db.query(NotificationDeliveryAttempt).filter_by(notification_id=notification_id).order_by(
            NotificationDeliveryAttempt.attempt_number.asc()
        ).all()
        assert delivery.status == "accepted"
        assert delivery.attempt_count == 2
        assert delivery.provider_accepted_at == retry_at
        assert delivery.last_error_code is None
        assert len(failures) == 1
        assert failures[0].resolved_at == retry_at
        assert len(attempts) == 2
        assert [item.result for item in attempts] == ["retry_scheduled", "provider_accepted"]
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert all(item.person_id == session["person"]["id"] for item in attempts)
        assert all(item.caregiver_id == session["caregiver"]["id"] for item in attempts)

    # A worker restart/replay after provider acceptance must not send again.
    with Session() as db:
        accepted = delivery_service.deliver_pending_web_pushes(db=db, now=retry_at + timedelta(minutes=10))
        assert accepted == 0
    with Session() as db:
        assert db.query(CarePushDelivery).filter_by(notification_id=notification_id).count() == 1
        assert db.query(NotificationDeliveryAttempt).filter_by(notification_id=notification_id).count() == 2

    assert len(calls) == 2
    for call in calls:
        assert "Private care reminder title" not in call["data"]
        assert "Private care details" not in call["data"]
