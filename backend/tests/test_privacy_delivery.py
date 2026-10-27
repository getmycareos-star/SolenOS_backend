import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

pytestmark = pytest.mark.session_security

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
        json={"name": name, "caregiver_name": f"{name} caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_caregiver_session(person_id, relationship="family caregiver"):
    from app.models.anonymous_session import AnonymousSession
    from app.models.care import Caregiver

    caregiver_id = str(uuid.uuid4())
    token = "test-token-" + str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db = TestSessionLocal()
    try:
        db.add(Caregiver(
            id=caregiver_id,
            name="Second caregiver",
            relationship=relationship,
            person_id=person_id,
            timezone="UTC",
        ))
        db.flush()
        db.add(AnonymousSession(
            id=str(uuid.uuid4()),
            person_id=person_id,
            caregiver_id=caregiver_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now + timedelta(hours=1),
            created_at=now,
        ))
        db.commit()
    finally:
        db.close()
    return caregiver_id, token


def test_handoff_create_accept_acknowledge_revoke_persists_and_audits():
    from app.models.privacy_delivery import AuditLogEvent, HandoffAcknowledgement, HandoffAccessLog

    client = TestClient(app)
    owner = create_session(client, "Recipient")
    recipient_id, recipient_token = add_caregiver_session(owner["person"]["id"])
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    recipient_headers = {"Authorization": f"Bearer {recipient_token}"}

    created = client.post(
        "/api/v1/handoffs",
        headers=owner_headers,
        json={
            "person_id": owner["person"]["id"],
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary", "open_loops"],
        },
    )
    assert created.status_code == 201, created.text
    handoff = created.json()
    assert handoff["status"] == "pending"
    assert handoff["scope"] == ["lcr_summary", "open_loops"]
    assert handoff["recipients"][0]["status"] == "pending"

    before_accept = client.get(
        f"/api/v1/handoffs/{handoff['id']}",
        headers=recipient_headers,
    )
    assert before_accept.status_code == 404

    accepted = client.post(
        f"/api/v1/handoffs/{handoff['id']}/accept",
        headers=recipient_headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["recipients"][0]["status"] == "accepted"

    acknowledged = client.post(
        f"/api/v1/handoffs/{handoff['id']}/acknowledge",
        headers=recipient_headers,
        json={"acknowledgement": "I have received this care handoff.", "note": "Follow up tomorrow."},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["acknowledged"] is True

    revoked = client.post(
        f"/api/v1/handoffs/{handoff['id']}/revoke",
        headers=owner_headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    blocked = client.get(
        f"/api/v1/handoffs/{handoff['id']}",
        headers=recipient_headers,
    )
    assert blocked.status_code == 404

    db = TestSessionLocal()
    try:
        assert db.query(HandoffAcknowledgement).filter_by(handoff_id=handoff["id"]).count() == 1
        assert db.query(HandoffAccessLog).filter_by(handoff_id=handoff["id"]).count() >= 5
        assert db.query(AuditLogEvent).filter_by(target_id=handoff["id"]).count() >= 5
    finally:
        db.close()


def test_handoff_rejects_recipient_from_another_person():
    client = TestClient(app)
    owner = create_session(client, "First person")
    unrelated = create_session(client, "Second person")
    response = client.post(
        "/api/v1/handoffs",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
        json={
            "person_id": owner["person"]["id"],
            "recipient_caregiver_ids": [unrelated["caregiver"]["id"]],
            "scope": ["lcr_summary"],
        },
    )
    assert response.status_code == 404


def test_handoff_rejects_spoofed_person_scope_and_invalid_expiry():
    client = TestClient(app)
    owner = create_session(client, "First person")
    other = create_session(client, "Second person")
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    wrong_person = client.post(
        "/api/v1/handoffs",
        headers=headers,
        json={
            "person_id": other["person"]["id"],
            "recipient_caregiver_ids": [other["caregiver"]["id"]],
            "scope": ["lcr_summary"],
        },
    )
    assert wrong_person.status_code == 403
    recipient_id, _ = add_caregiver_session(owner["person"]["id"])
    expired = client.post(
        "/api/v1/handoffs",
        headers=headers,
        json={
            "person_id": owner["person"]["id"],
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary"],
            "expires_at": "2020-01-01T00:00:00Z",
        },
    )
    assert expired.status_code == 422



def test_notification_read_receipt_and_preferences_are_persisted_and_scoped():
    from app.models.notifications import CareNotification, CareNotificationPreference
    from app.models.privacy_delivery import AuditLogEvent, NotificationReadReceipt

    client = TestClient(app)
    owner = create_session(client, "Notification recipient")
    person_id = owner["person"]["id"]
    caregiver_id = owner["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    db = TestSessionLocal()
    try:
        notification = CareNotification(
            id=str(uuid.uuid4()), person_id=person_id, caregiver_id=caregiver_id,
            title="Care update", body="A caregiver recorded a new care event.",
            category="care_update", priority="normal",
        )
        db.add(notification)
        db.commit()
        notification_id = notification.id
    finally:
        db.close()

    listed = client.get("/api/v1/notifications?unread_only=true", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == notification_id and item["is_read"] is False for item in listed.json())

    marked = client.patch(f"/api/v1/notifications/{notification_id}/read", headers=headers)
    assert marked.status_code == 200, marked.text
    assert marked.json()["is_read"] is True

    preferences_before = client.get("/api/v1/notifications/preferences", headers=headers)
    assert preferences_before.status_code == 200
    assert preferences_before.json()["configured"] is False
    preferences_saved = client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"quiet_hours_enabled": True, "reminders_enabled": False},
    )
    assert preferences_saved.status_code == 200, preferences_saved.text
    assert preferences_saved.json()["quiet_hours_enabled"] is True
    assert preferences_saved.json()["reminders_enabled"] is False

    db = TestSessionLocal()
    try:
        assert db.query(NotificationReadReceipt).filter_by(notification_id=notification_id, reader_id=caregiver_id).count() == 1
        assert db.query(CareNotificationPreference).filter_by(person_id=person_id, caregiver_id=caregiver_id).one().reminders_enabled is False
        assert db.query(AuditLogEvent).filter_by(target_id=notification_id, action="notification.read").count() == 1
        assert db.query(AuditLogEvent).filter_by(action="notification.preferences.update", person_id=person_id).count() == 1
    finally:
        db.close()


def test_notification_read_isolated_between_caregiver_sessions():
    from app.models.notifications import CareNotification

    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    db = TestSessionLocal()
    try:
        notification = CareNotification(
            id=str(uuid.uuid4()), person_id=first["person"]["id"],
            caregiver_id=first["caregiver"]["id"], title="Private update",
            body="Scoped notification.", category="care_update", priority="normal",
        )
        db.add(notification)
        db.commit()
        notification_id = notification.id
    finally:
        db.close()
    response = client.patch(
        f"/api/v1/notifications/{notification_id}/read",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 404
