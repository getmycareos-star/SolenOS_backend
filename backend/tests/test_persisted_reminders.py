from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, SessionLocal as DefaultSessionLocal, get_db
from app.main import app

pytestmark = pytest.mark.session_security


def _valid_vapid_settings(secret_key: str):
    import base64
    from types import SimpleNamespace

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


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def create_session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_reminder(client, session, title="Check appointment notes"):
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = client.post(
        "/api/v1/reminders",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "title": title,
            "note": "Caregiver-entered note",
            "due_at": due_at,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reminder_create_list_complete_and_cancel_persist_in_database():
    client = TestClient(app)
    session = create_session(client, "Reminder test person")
    created = create_reminder(client, session)

    assert created["status"] == "open"
    assert created["created_by_caregiver_id"] == session["caregiver"]["id"]
    assert created["delivery_mode"] == "in_app_notification_feed"
    assert created["notification_delivery_status"] == "in_app_notification_created"

    listed = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created["id"]]

    completed = client.patch(
        f"/api/v1/reminders/{created['id']}/complete",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"completion_note": "Reviewed"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_by_caregiver_id"] == session["caregiver"]["id"]

    cancellable = create_reminder(client, session, "Check transport plan")
    cancelled = client.patch(
        f"/api/v1/reminders/{cancellable['id']}/cancel",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_by_caregiver_id"] == session["caregiver"]["id"]

    # Re-query using a fresh SQLAlchemy session to assert persistence beyond the request.
    from app.models.reminder import CareReminder

    db = TestSessionLocal()
    try:
        stored_completed = db.query(CareReminder).filter_by(id=created["id"]).one()
        stored_cancelled = db.query(CareReminder).filter_by(id=cancellable["id"]).one()
        assert stored_completed.status == "completed"
        assert stored_completed.completion_note == "Reviewed"
        assert stored_cancelled.status == "cancelled"
    finally:
        db.close()


def test_reminders_require_bearer_session_and_enforce_person_scope():
    client = TestClient(app)
    first = create_session(client, "First test person")
    second = create_session(client, "Second test person")
    first_reminder = create_reminder(client, first)

    no_auth = client.get(f"/api/v1/reminders/{first['person']['id']}")
    assert no_auth.status_code == 401, no_auth.text

    cross_list = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert cross_list.status_code == 403, cross_list.text

    cross_complete = client.patch(
        f"/api/v1/reminders/{first_reminder['id']}/complete",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={},
    )
    assert cross_complete.status_code == 404, cross_complete.text

    cross_cancel = client.patch(
        f"/api/v1/reminders/{first_reminder['id']}/cancel",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert cross_cancel.status_code == 404, cross_cancel.text


def test_reminder_rejects_missing_timezone_and_blank_title():
    client = TestClient(app)
    session = create_session(client, "Validation test person")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    missing_timezone = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Timezone test",
            "due_at": "2026-10-26T09:00:00",
        },
    )
    assert missing_timezone.status_code == 422, missing_timezone.text

    blank_title = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "   ",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    assert blank_title.status_code == 422, blank_title.text



def test_notification_preferences_validate_and_persist_quiet_hours_schedule():
    client = TestClient(app)
    session = create_session(client, "Quiet hours caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    saved = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={
            "quiet_hours_enabled": True,
            "quiet_hours_start": "22:30",
            "quiet_hours_end": "07:15",
            "timezone_name": "America/Los_Angeles",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["quiet_hours_enabled"] is True
    assert saved.json()["quiet_hours_start"] == "22:30"
    assert saved.json()["quiet_hours_end"] == "07:15"
    assert saved.json()["timezone_name"] == "America/Los_Angeles"

    invalid_timezone = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"timezone_name": "Not/A_Real_Zone"},
    )
    assert invalid_timezone.status_code == 422, invalid_timezone.text

    equal_window = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"quiet_hours_start": "08:00", "quiet_hours_end": "08:00"},
    )
    assert equal_window.status_code == 422, equal_window.text

    same_as_saved_end = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"quiet_hours_start": "07:15"},
    )
    assert same_as_saved_end.status_code == 422, same_as_saved_end.text

    second_session = create_session(client, "Different caregiver")
    other = client.get(
        "/api/v1/notifications/preferences",
        headers={"Authorization": f"Bearer {second_session['access_token']}"},
    )
    assert other.status_code == 200
    assert other.json()["timezone_name"] == "UTC"
    assert other.json()["quiet_hours_start"] == "21:00"



def test_web_push_capability_reports_unavailable_without_vapid_configuration(monkeypatch):
    from types import SimpleNamespace
    import app.api.push_notifications as push_api

    settings = SimpleNamespace(
        vapid_public_key="",
        vapid_private_key="",
        vapid_subject="",
        secret_key="test-secret-key",
    )
    monkeypatch.setattr(push_api, "get_settings", lambda: settings)
    client = TestClient(app)
    session = create_session(client, "Push unavailable caregiver")

    response = client.get(
        "/api/v1/notifications/push/public-key",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "available": False,
        "public_key": None,
        "reason": "not_configured",
    }


def test_web_push_registration_is_encrypted_scoped_and_revocable(monkeypatch):
    from types import SimpleNamespace
    import app.api.push_notifications as push_api
    from app.models.notifications import CarePushSubscription

    settings = _valid_vapid_settings("test-secret-key")
    monkeypatch.setattr(push_api, "get_settings", lambda: settings)
    client = TestClient(app)
    first = create_session(client, "Push owner")
    second = create_session(client, "Different push owner")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    endpoint = "https://fcm.googleapis.com/fcm/send/unique-endpoint"
    payload = {
        "endpoint": endpoint,
        "keys": {"p256dh": "test-public-key-material", "auth": "test-auth-secret"},
    }

    public_key = client.get("/api/v1/notifications/push/public-key", headers=first_headers)
    assert public_key.status_code == 200
    assert public_key.json()["public_key"] == settings.vapid_public_key

    registered = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=first_headers,
        json=payload,
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["registered"] is True

    db = TestSessionLocal()
    try:
        stored = db.query(CarePushSubscription).one()
        assert stored.person_id == first["person"]["id"]
        assert stored.caregiver_id == first["caregiver"]["id"]
        assert endpoint not in stored.encrypted_subscription
        assert "test-auth-secret" not in stored.encrypted_subscription
    finally:
        db.close()

    cross_record = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=second_headers,
        json=payload,
    )
    assert cross_record.status_code == 409

    revoked = client.request(
        "DELETE",
        "/api/v1/notifications/push/subscriptions",
        headers=first_headers,
        json={"endpoint": endpoint},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True



def test_web_push_worker_records_provider_acceptance_without_exposing_care_details(monkeypatch):
    from types import SimpleNamespace
    import sys
    import app.api.push_notifications as push_api
    import app.services.reminder_delivery as delivery_service
    from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription
    from app.models.open_loop import OpenLoopRecord

    settings = _valid_vapid_settings("stable-test-secret")
    monkeypatch.setattr(push_api, "get_settings", lambda: settings)
    monkeypatch.setattr(delivery_service, "get_settings", lambda: settings)
    sent_payloads = []

    class FakeWebPushException(Exception):
        response = None

    def fake_webpush(**kwargs):
        sent_payloads.append(kwargs)
        return SimpleNamespace(status_code=201)

    monkeypatch.setitem(
        sys.modules,
        "pywebpush",
        SimpleNamespace(WebPushException=FakeWebPushException, webpush=fake_webpush),
    )

    client = TestClient(app)
    session = create_session(client, "Push delivery owner")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    endpoint = "https://fcm.googleapis.com/fcm/send/delivery"
    registered = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=headers,
        json={"endpoint": endpoint, "keys": {"p256dh": "public-key", "auth": "auth-secret"}},
    )
    assert registered.status_code == 201, registered.text

    db = TestSessionLocal()
    try:
        notification = CareNotification(
            id="push-test-notification",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="Private reminder title",
            body="Private reminder details that must not appear on the lock screen.",
            category="reminder_due",
            priority="normal",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        in_app_change = CareNotification(
            id="push-test-in-app-change",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="A care change may be worth reviewing",
            body="This belongs in the authenticated in-app inbox.",
            category="care_change",
            priority="normal",
            source_event_id="meaningful-change",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        subscription = db.query(CarePushSubscription).one()
        due_soon = datetime.now(timezone.utc) + timedelta(hours=4)
        due_later = datetime.now(timezone.utc) + timedelta(hours=18)
        high_loop = OpenLoopRecord(
            id="push-high-priority-loop",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            loop_type="follow_up",
            title="Call the clinic",
            description="A recorded follow-up with a near deadline.",
            status="open",
            outcome_status="outcome_unknown",
            owner_role="unassigned",
            source_type="appointment_workspace_follow_up",
            source_id="workspace:call-clinic",
            source_fingerprint="b" * 64,
            evidence=[{"record_type": "appointment_workspace", "record_id": "workspace-push"}],
            uncertainty="reported",
            deadline_at=due_soon,
            deadline_source="explicit_user",
            priority="normal",
            priority_reasons=[],
            version=1,
        )
        normal_loop = OpenLoopRecord(
            id="push-normal-priority-loop",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            loop_type="awaiting_response",
            title="Check the follow-up response",
            description="A recorded follow-up with a later deadline.",
            status="open",
            outcome_status="outcome_unknown",
            owner_role="unassigned",
            source_type="handoff_follow_up",
            source_id="handoff:response",
            source_fingerprint="c" * 64,
            evidence=[{"record_type": "handoff", "record_id": "handoff-push"}],
            uncertainty="reported",
            deadline_at=due_later,
            deadline_source="recorded_commitment",
            priority="normal",
            priority_reasons=[],
            version=1,
        )
        db.add_all([high_loop, normal_loop])
        db.flush()
        high_loop_notification = CareNotification(
            id="push-test-open-loop-high",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="A care follow-up is due soon",
            body="Private follow-up details stay in the authenticated inbox.",
            category="open_loop_due",
            priority="high",
            source_event_id=f"{high_loop.id}|{due_soon.isoformat()}",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        normal_loop_notification = CareNotification(
            id="push-test-open-loop-normal",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="A care follow-up is due soon",
            body="This should remain in the in-app inbox.",
            category="open_loop_due",
            priority="normal",
            source_event_id=f"{normal_loop.id}|{due_later.isoformat()}",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=2),
        )
        stale_delivery = CarePushDelivery(
            id="push-test-stale-in-app-delivery",
            notification_id=in_app_change.id,
            subscription_id=subscription.id,
            status="pending",
            attempt_count=0,
            next_attempt_at=datetime.now(timezone.utc),
        )
        normal_priority_delivery = CarePushDelivery(
            id="push-test-open-loop-normal-delivery",
            notification_id=normal_loop_notification.id,
            subscription_id=subscription.id,
            status="pending",
            attempt_count=0,
            next_attempt_at=datetime.now(timezone.utc),
        )
        db.add_all([
            notification, in_app_change, high_loop_notification,
            normal_loop_notification, stale_delivery, normal_priority_delivery,
        ])
        db.commit()

        accepted = delivery_service.deliver_pending_web_pushes(
            db=db,
            now=datetime.now(timezone.utc),
        )
        assert accepted == 2
        delivery = db.query(CarePushDelivery).filter_by(
            notification_id=notification.id
        ).one()
        assert delivery.status == "accepted"
        db.refresh(stale_delivery)
        assert stale_delivery.status == "cancelled"
        assert stale_delivery.last_error_code == "in_app_only_category"
        assert delivery.provider_accepted_at is not None
        open_loop_delivery = db.query(CarePushDelivery).filter_by(
            notification_id=high_loop_notification.id
        ).one()
        assert open_loop_delivery.status == "accepted"
        normal_delivery = db.query(CarePushDelivery).filter_by(
            notification_id=normal_loop_notification.id
        ).one()
        assert normal_delivery.status == "cancelled"
        assert normal_delivery.last_error_code == "notification_not_push_eligible"
        assert len(sent_payloads) == 2
        for sent in sent_payloads:
            assert "Private reminder title" not in sent["data"]
            assert "Private reminder details" not in sent["data"]
            assert "Private follow-up details" not in sent["data"]
            assert "You have a care update" in sent["data"]
    finally:
        db.close()



def test_web_push_waits_for_quiet_hours_to_end(monkeypatch):
    from types import SimpleNamespace
    import sys
    import app.api.push_notifications as push_api
    import app.services.reminder_delivery as delivery_service
    from app.models.notifications import CareNotification, CareNotificationPreference, CarePushDelivery, CarePushSubscription

    settings = _valid_vapid_settings("quiet-hours-test-secret")
    monkeypatch.setattr(push_api, "get_settings", lambda: settings)
    monkeypatch.setattr(delivery_service, "get_settings", lambda: settings)
    sent = []

    class FakeWebPushException(Exception):
        response = None

    def fake_webpush(**kwargs):
        sent.append(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "pywebpush",
        SimpleNamespace(WebPushException=FakeWebPushException, webpush=fake_webpush),
    )

    client = TestClient(app)
    session = create_session(client, "Quiet push caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    endpoint = "https://fcm.googleapis.com/fcm/send/quiet-window"
    response = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=headers,
        json={"endpoint": endpoint, "keys": {"p256dh": "public-key", "auth": "auth-secret"}},
    )
    assert response.status_code == 201, response.text

    db = TestSessionLocal()
    try:
        subscription = db.query(CarePushSubscription).one()
        subscription.created_at = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)
        db.add(CareNotificationPreference(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            reminders_enabled=True,
            quiet_hours_enabled=True,
            quiet_hours_start="21:00",
            quiet_hours_end="08:00",
            timezone_name="America/New_York",
        ))
        notification = CareNotification(
            id="quiet-push-notification",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="Sensitive title",
            body="Sensitive care detail",
            category="reminder_due",
            priority="normal",
            created_at=datetime(2026, 1, 15, 6, 30, tzinfo=timezone.utc),
        )
        db.add(notification)
        db.commit()

        assert delivery_service.deliver_pending_web_pushes(
            db=db, now=datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)
        ) == 0
        delivery = db.query(CarePushDelivery).filter_by(notification_id=notification.id).one()
        release = delivery.next_attempt_at
        release = release.replace(tzinfo=timezone.utc) if release.tzinfo is None else release
        assert delivery.status == "pending"
        assert release == datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
        assert sent == []

        assert delivery_service.deliver_pending_web_pushes(
            db=db, now=datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)
        ) == 1
        db.refresh(delivery)
        assert delivery.status == "accepted"
        assert len(sent) == 1
    finally:
        db.close()


def test_web_push_failure_is_persisted_as_retry_scheduled_and_retried_once(monkeypatch):
    from types import SimpleNamespace
    import sys
    import app.api.push_notifications as push_api
    import app.services.reminder_delivery as delivery_service
    from app.models.notifications import CareNotification, CarePushDelivery, CarePushSubscription

    settings = _valid_vapid_settings("retry-state-test-secret")
    monkeypatch.setattr(push_api, "get_settings", lambda: settings)
    monkeypatch.setattr(delivery_service, "get_settings", lambda: settings)
    calls = []

    class FakeWebPushException(Exception):
        def __init__(self):
            super().__init__("provider unavailable")
            self.response = SimpleNamespace(status_code=503)

    def failing_webpush(**kwargs):
        calls.append("failed")
        raise FakeWebPushException()

    fake_provider = SimpleNamespace(
        WebPushException=FakeWebPushException,
        webpush=failing_webpush,
    )
    monkeypatch.setitem(sys.modules, "pywebpush", fake_provider)

    client = TestClient(app)
    session = create_session(client, "Retry state caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    endpoint = "https://fcm.googleapis.com/fcm/send/retry-state"
    registered = client.post(
        "/api/v1/notifications/push/subscriptions",
        headers=headers,
        json={"endpoint": endpoint, "keys": {"p256dh": "public-key", "auth": "auth-secret"}},
    )
    assert registered.status_code == 201, registered.text

    db = TestSessionLocal()
    try:
        subscription = db.query(CarePushSubscription).one()
        now = datetime.now(timezone.utc) + timedelta(seconds=3)
        notification = CareNotification(
            id="retry-state-notification",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            title="Private care update",
            body="Private care details remain in the authenticated inbox.",
            category="reminder_due",
            priority="normal",
            created_at=now,
        )
        db.add(notification)
        db.commit()

        assert delivery_service.deliver_pending_web_pushes(db=db, now=now) == 0
        delivery = db.query(CarePushDelivery).filter_by(
            notification_id=notification.id,
            subscription_id=subscription.id,
        ).one()
        from app.models.notifications import DeliveryFailureEvent
        failure = db.query(DeliveryFailureEvent).filter_by(delivery_id=delivery.id).one()
        assert failure.delivery_type == "web_push"
        assert failure.person_id == session["person"]["id"]
        assert failure.caregiver_id == session["caregiver"]["id"]
        assert failure.failure_code == "http_503"
        assert failure.retryable is True
        assert failure.resolved_at is None
        assert failure.failure_detail_redacted == "Push provider rejected the request"

        assert delivery.status == "retry_scheduled"
        assert delivery.attempt_count == 1
        assert delivery.last_error_code == "http_503"
        first_retry = delivery.next_attempt_at
        first_retry = first_retry.replace(tzinfo=timezone.utc) if first_retry.tzinfo is None else first_retry
        assert first_retry == now + timedelta(seconds=60)
        assert calls == ["failed"]

        def successful_webpush(**kwargs):
            calls.append("accepted")
            return SimpleNamespace(status_code=201)

        fake_provider.webpush = successful_webpush
        assert delivery_service.deliver_pending_web_pushes(
            db=db, now=now + timedelta(seconds=61)
        ) == 1
        db.refresh(delivery)
        assert delivery.status == "accepted"
        assert delivery.attempt_count == 2
        assert delivery.provider_accepted_at is not None
        db.refresh(failure)
        assert failure.resolved_at is not None
        assert db.query(DeliveryFailureEvent).filter_by(delivery_id=delivery.id).count() == 1
        assert calls == ["failed", "accepted"]
        assert db.query(CarePushDelivery).filter_by(
            notification_id=notification.id,
            subscription_id=subscription.id,
        ).count() == 1
    finally:
        db.close()
