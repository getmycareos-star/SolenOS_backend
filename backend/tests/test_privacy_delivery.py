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


def accept_required_consent(person_id, caregiver_id):
    from app.models.collaboration import ConsentRecord

    db = TestSessionLocal()
    try:
        consent = db.query(ConsentRecord).filter_by(
            person_id=person_id, caregiver_id=caregiver_id
        ).first()
        if consent is None:
            consent = ConsentRecord(person_id=person_id, caregiver_id=caregiver_id)
            db.add(consent)
        consent.medical_disclaimer_acknowledged = True
        consent.privacy_model_acknowledged = True
        consent.multi_caregiver_acknowledged = True
        consent.no_advertising_acknowledged = True
        consent.accepted_terms_version = "2026-10"
        consent.accepted_at = datetime.now(timezone.utc)
        consent.revoked_at = None
        db.commit()
    finally:
        db.close()


def revoke_required_consent(person_id, caregiver_id):
    from app.models.collaboration import ConsentRecord

    db = TestSessionLocal()
    try:
        consent = db.query(ConsentRecord).filter_by(
            person_id=person_id, caregiver_id=caregiver_id
        ).one()
        consent.revoked_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def test_handoff_create_accept_acknowledge_revoke_persists_and_audits():
    from app.models.privacy_delivery import AuditLogEvent, HandoffAcknowledgement, HandoffAccessLog

    client = TestClient(app)
    owner = create_session(client, "Recipient")
    recipient_id, recipient_token = add_caregiver_session(owner["person"]["id"])
    accept_required_consent(owner["person"]["id"], owner["caregiver"]["id"])
    accept_required_consent(owner["person"]["id"], recipient_id)
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

    out_of_scope = client.put(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=owner_headers,
        json={
            "sections": {"appointments": "This section was not approved for sharing."},
            "source_record_ids": ["record-1"],
            "caregiver_reviewed": True,
        },
    )
    assert out_of_scope.status_code == 403

    unreviewed = client.put(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=owner_headers,
        json={
            "sections": {"lcr_summary": "Draft not reviewed."},
            "source_record_ids": ["record-1"],
            "caregiver_reviewed": False,
        },
    )
    assert unreviewed.status_code == 422

    attached = client.put(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=owner_headers,
        json={
            "sections": {
                "lcr_summary": "Caregiver-reported changes.",
                "open_loops": "Question to confirm at the next visit.",
            },
            "source_record_ids": ["evidence-1", "care-event-1"],
            "caregiver_reviewed": True,
        },
    )
    assert attached.status_code == 200, attached.text
    assert len(attached.json()["content_sha256"]) == 64

    duplicate_attach = client.put(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=owner_headers,
        json={
            "sections": {"lcr_summary": "A later replacement."},
            "source_record_ids": ["evidence-2"],
            "caregiver_reviewed": True,
        },
    )
    assert duplicate_attach.status_code == 409

    before_accept = client.get(
        f"/api/v1/handoffs/{handoff['id']}",
        headers=recipient_headers,
    )
    assert before_accept.status_code == 404
    pending_summary = client.get(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=recipient_headers,
    )
    assert pending_summary.status_code == 200, pending_summary.text
    assert pending_summary.json()["summary"]["sections"]["lcr_summary"] == "Caregiver-reported changes."

    accepted = client.post(
        f"/api/v1/handoffs/{handoff['id']}/accept",
        headers=recipient_headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["recipients"][0]["status"] == "accepted"

    recipient_summary = client.get(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=recipient_headers,
    )
    assert recipient_summary.status_code == 200, recipient_summary.text
    assert recipient_summary.json()["summary"]["sections"]["lcr_summary"] == "Caregiver-reported changes."
    assert recipient_summary.json()["summary"]["review_attestation"] == "client_declared_not_server_verified"
    assert recipient_summary.json()["summary"]["source_id_validation"] == "not_server_verified"

    from app.models.privacy_delivery import HandoffRecord
    db = TestSessionLocal()
    try:
        stored_handoff = db.query(HandoffRecord).filter_by(id=handoff["id"]).one()
        tampered_snapshot = dict(stored_handoff.summary_snapshot)
        tampered_sections = dict(tampered_snapshot["sections"])
        tampered_sections["lcr_summary"] = "Changed without a new caregiver review."
        tampered_snapshot["sections"] = tampered_sections
        stored_handoff.summary_snapshot = tampered_snapshot
        db.commit()
    finally:
        db.close()
    tampered_read = client.get(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=recipient_headers,
    )
    assert tampered_read.status_code == 409

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
    blocked_summary = client.get(
        f"/api/v1/handoffs/{handoff['id']}/summary",
        headers=recipient_headers,
    )
    assert blocked_summary.status_code == 404

    db = TestSessionLocal()
    try:
        assert db.query(HandoffAcknowledgement).filter_by(handoff_id=handoff["id"]).count() == 1
        assert db.query(HandoffAccessLog).filter_by(handoff_id=handoff["id"]).count() >= 5
        assert db.query(AuditLogEvent).filter_by(target_id=handoff["id"]).count() >= 5
    finally:
        db.close()


def test_handoff_requires_current_consent_for_creator_and_recipient():
    from app.models.privacy_delivery import AuditLogEvent

    client = TestClient(app)
    owner = create_session(client, "Consent owner")
    recipient_id, recipient_token = add_caregiver_session(owner["person"]["id"])
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    recipient_headers = {"Authorization": f"Bearer {recipient_token}"}
    payload = {
        "person_id": owner["person"]["id"],
        "recipient_caregiver_ids": [recipient_id],
        "scope": ["lcr_summary"],
    }

    denied_create = client.post("/api/v1/handoffs", headers=owner_headers, json=payload)
    assert denied_create.status_code == 403
    db = TestSessionLocal()
    try:
        denied_event = db.query(AuditLogEvent).filter_by(
            person_id=owner["person"]["id"], action="handoff.create", outcome="denied"
        ).one()
        assert denied_event.record_metadata["reason_code"] == "required_consent_missing"
    finally:
        db.close()

    accept_required_consent(owner["person"]["id"], owner["caregiver"]["id"])
    created = client.post("/api/v1/handoffs", headers=owner_headers, json=payload)
    assert created.status_code == 201, created.text
    handoff_id = created.json()["id"]

    denied_accept = client.post(f"/api/v1/handoffs/{handoff_id}/accept", headers=recipient_headers)
    assert denied_accept.status_code == 404

    accept_required_consent(owner["person"]["id"], recipient_id)
    missing_summary_accept = client.post(f"/api/v1/handoffs/{handoff_id}/accept", headers=recipient_headers)
    assert missing_summary_accept.status_code == 409

    attached = client.put(
        f"/api/v1/handoffs/{handoff_id}/summary",
        headers=owner_headers,
        json={
            "sections": {"lcr_summary": "Reviewed handoff summary."},
            "source_record_ids": [],
            "caregiver_reviewed": True,
        },
    )
    assert attached.status_code == 200, attached.text
    accepted = client.post(f"/api/v1/handoffs/{handoff_id}/accept", headers=recipient_headers)
    assert accepted.status_code == 200, accepted.text

    revoke_required_consent(owner["person"]["id"], recipient_id)
    denied_read = client.get(f"/api/v1/handoffs/{handoff_id}", headers=recipient_headers)
    assert denied_read.status_code == 404

    second_recipient_id, second_recipient_token = add_caregiver_session(owner["person"]["id"])
    accept_required_consent(owner["person"]["id"], second_recipient_id)
    second_handoff = client.post(
        "/api/v1/handoffs",
        headers=owner_headers,
        json={
            "person_id": owner["person"]["id"],
            "recipient_caregiver_ids": [second_recipient_id],
            "scope": ["lcr_summary"],
        },
    )
    assert second_handoff.status_code == 201, second_handoff.text
    revoke_required_consent(owner["person"]["id"], owner["caregiver"]["id"])
    creator_revoked_accept = client.post(
        f"/api/v1/handoffs/{second_handoff.json()['id']}/accept",
        headers={"Authorization": f"Bearer {second_recipient_token}"},
    )
    assert creator_revoked_accept.status_code == 404


def test_handoff_recipient_directory_is_scoped_and_consent_aware():
    from app.models.privacy_delivery import AuditLogEvent

    client = TestClient(app)
    owner = create_session(client, "Directory owner")
    recipient_id, _ = add_caregiver_session(owner["person"]["id"])
    unrelated = create_session(client, "Unrelated person")
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    denied = client.get("/api/v1/handoffs/directory/recipients", headers=headers)
    assert denied.status_code == 403

    accept_required_consent(owner["person"]["id"], owner["caregiver"]["id"])
    listed = client.get("/api/v1/handoffs/directory/recipients", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["count"] == 1
    assert listed.json()["items"][0]["caregiver_id"] == recipient_id
    assert listed.json()["items"][0]["can_receive_handoff"] is False
    assert all(item["caregiver_id"] != unrelated["caregiver"]["id"] for item in listed.json()["items"])

    accept_required_consent(owner["person"]["id"], recipient_id)
    listed_after_consent = client.get("/api/v1/handoffs/directory/recipients", headers=headers)
    assert listed_after_consent.status_code == 200
    assert listed_after_consent.json()["items"][0]["can_receive_handoff"] is True

    db = TestSessionLocal()
    try:
        assert db.query(AuditLogEvent).filter_by(
            person_id=owner["person"]["id"], action="handoff.recipients.list"
        ).count() == 3
    finally:
        db.close()


def test_handoff_rejects_recipient_from_another_person():
    client = TestClient(app)
    owner = create_session(client, "First person")
    accept_required_consent(owner["person"]["id"], owner["caregiver"]["id"])
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
    accept_required_consent(owner["person"]["id"], owner["caregiver"]["id"])
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



def test_required_consent_acceptance_and_revocation_are_audited():
    from app.models.privacy_delivery import AuditLogEvent

    client = TestClient(app)
    owner = create_session(client, "Consent audit recipient")
    person_id = owner["person"]["id"]
    caregiver_id = owner["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    accepted = client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "medical_disclaimer_acknowledged": True,
            "privacy_model_acknowledged": True,
            "multi_caregiver_acknowledged": True,
            "no_advertising_acknowledged": True,
            "data_improvement_consent": False,
            "accepted_terms_version": "2026-10",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["verified"] is True

    revoked = client.post(
        "/api/v1/consent",
        headers=headers,
        json={"person_id": person_id, "caregiver_id": caregiver_id, "action": "revoke"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["verified"] is False

    db = TestSessionLocal()
    try:
        assert db.query(AuditLogEvent).filter_by(
            person_id=person_id, action="consent.accepted"
        ).count() == 1
        assert db.query(AuditLogEvent).filter_by(
            person_id=person_id, action="consent.revoked"
        ).count() == 1
        accepted_event = db.query(AuditLogEvent).filter_by(
            person_id=person_id, action="consent.accepted"
        ).one()
        assert accepted_event.target_id is not None
    finally:
        db.close()


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
    listed_payload = listed.json()
    assert set(listed_payload) == {"items", "total", "limit", "offset", "unread_only"}
    assert listed_payload["total"] == len(listed_payload["items"])
    assert listed_payload["limit"] == 50
    assert listed_payload["offset"] == 0
    assert listed_payload["unread_only"] is True
    assert any(item["id"] == notification_id and item["is_read"] is False for item in listed_payload["items"])

    marked = client.patch(f"/api/v1/notifications/{notification_id}/read", headers=headers)
    assert marked.status_code == 200, marked.text
    assert marked.json()["is_read"] is True

    # Marking the same notification read again must be idempotent.
    marked_again = client.patch(f"/api/v1/notifications/{notification_id}/read", headers=headers)
    assert marked_again.status_code == 200, marked_again.text
    unread_after_read = client.get("/api/v1/notifications?unread_only=true", headers=headers)
    assert unread_after_read.status_code == 200, unread_after_read.text
    assert unread_after_read.json()["items"] == []
    assert unread_after_read.json()["total"] == 0

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
        assert db.query(AuditLogEvent).filter_by(action="notification.list", person_id=person_id).count() >= 2
        assert db.query(AuditLogEvent).filter_by(action="notification.preferences.update", person_id=person_id).count() == 1
        audit_events = db.query(AuditLogEvent).filter_by(person_id=person_id).all()
        assert all("A caregiver recorded a new care event." not in str(event.record_metadata) for event in audit_events)
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
