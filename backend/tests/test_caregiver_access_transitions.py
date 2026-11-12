import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.caregiver_access import CaregiverAccessGrant, CaregiverInvitation
from app.models.anonymous_session import AnonymousSession

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


def create_primary(client):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "Margaret", "caregiver_name": "Sarah"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_new_anonymous_session_receives_explicit_primary_grant():
    client = TestClient(app)
    session = create_primary(client)
    db = TestSessionLocal()
    try:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ).one()
        assert grant.status == "active"
        assert grant.role == "primary"
        assert grant.can_manage_members is True
    finally:
        db.close()


def test_invitation_is_recipient_bound_and_acceptance_creates_separate_identity():
    client = TestClient(app)
    primary = create_primary(client)
    owner_headers = {"Authorization": f"Bearer {primary['access_token']}"}

    created = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=owner_headers,
        json={"email": "james@example.com", "expires_in_days": 7},
    )
    assert created.status_code == 201, created.text
    invite = created.json()
    assert invite["delivery_status"] == "not_sent"
    assert invite["status"] == "pending"

    wrong_recipient = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite["invitation_token"],
            "email": "attacker@example.com",
            "password": "a-long-test-password-123",
            "name": "Attacker",
        },
    )
    assert wrong_recipient.status_code == 403

    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite["invitation_token"],
            "email": "JAMES@example.com",
            "password": "a-long-test-password-123",
            "name": "James",
        },
    )
    assert accepted.status_code == 201, accepted.text
    result = accepted.json()
    assert result["accepted"] is True
    assert result["person_id"] == primary["person"]["id"]
    assert result["caregiver_id"] != primary["caregiver"]["id"]

    # The invitee can access the record only because the primary caregiver
    # explicitly authorised the invitation before acceptance.
    inherited = client.get(
        f"/api/v1/evidence/{primary['person']['id']}",
        headers={"Authorization": f"Bearer {result['access_token']}"},
    )
    assert inherited.status_code == 200, inherited.text

    replay = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite["invitation_token"],
            "email": "james@example.com",
            "password": "a-long-test-password-123",
            "name": "James",
        },
    )
    assert replay.status_code == 404

    db = TestSessionLocal()
    try:
        invitation = db.query(CaregiverInvitation).filter_by(id=invite["invitation_id"]).one()
        assert invitation.status == "accepted"
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=primary["person"]["id"],
            caregiver_id=result["caregiver_id"],
        ).one()
        assert grant.status == "active"
        assert grant.can_manage_members is False
    finally:
        db.close()


def test_revoking_member_revokes_existing_sessions_and_blocks_future_requests():
    client = TestClient(app)
    primary = create_primary(client)
    owner_headers = {"Authorization": f"Bearer {primary['access_token']}"}
    invite = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=owner_headers,
        json={"email": "james@example.com"},
    ).json()
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite["invitation_token"],
            "email": "james@example.com",
            "password": "a-long-test-password-123",
            "name": "James",
        },
    ).json()

    revoked = client.delete(
        f"/api/v1/caregiver-access/members/{accepted['caregiver_id']}",
        headers=owner_headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["sessions_revoked"] is True

    denied = client.get(
        f"/api/v1/evidence/{primary['person']['id']}",
        headers={"Authorization": f"Bearer {accepted['access_token']}"},
    )
    assert denied.status_code == 401

    db = TestSessionLocal()
    try:
        session = db.query(AnonymousSession).filter_by(id=accepted["session_id"]).one()
        assert session.revoked_at is not None
        grant = db.query(CaregiverAccessGrant).filter_by(caregiver_id=accepted["caregiver_id"]).one()
        assert grant.status == "revoked"
        assert grant.revoked_at is not None
    finally:
        db.close()


def test_non_administrator_cannot_invite_or_revoke_members():
    client = TestClient(app)
    primary = create_primary(client)
    owner_headers = {"Authorization": f"Bearer {primary['access_token']}"}
    invite = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=owner_headers,
        json={"email": "james@example.com"},
    ).json()
    accepted = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invite["invitation_token"],
            "email": "james@example.com",
            "password": "a-long-test-password-123",
            "name": "James",
        },
    ).json()
    james_headers = {"Authorization": f"Bearer {accepted['access_token']}"}

    denied_invite = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=james_headers,
        json={"email": "another@example.com"},
    )
    assert denied_invite.status_code == 403

    denied_revoke = client.delete(
        f"/api/v1/caregiver-access/members/{primary['caregiver']['id']}",
        headers=james_headers,
    )
    assert denied_revoke.status_code == 403
