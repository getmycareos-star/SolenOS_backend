import uuid
from datetime import datetime, timedelta, timezone
from jose import jwt

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import hash_password, issue_access_token
from app.main import app
from app.models.auth import Account, CareMembership
from app.models.care import Caregiver, Person

client = TestClient(app)
get_settings().secret_key = "test-only-secret-key-for-auth-tests"


def create_account_with_context():
    db = SessionLocal()
    try:
        suffix = uuid.uuid4().hex
        account = Account(
            email=f"auth-{suffix}@example.com",
            password_hash=hash_password("correct-horse-battery-staple"),
            display_name="Test Caregiver",
        )
        person = Person(name="Private Test Care Record")
        db.add_all([account, person])
        db.flush()
        caregiver = Caregiver(
            name="Test Caregiver",
            email=account.email,
            relationship="family caregiver",
            person_id=person.id,
            timezone="UTC",
        )
        db.add(caregiver)
        db.flush()
        membership = CareMembership(
            account_id=account.id,
            person_id=person.id,
            caregiver_id=caregiver.id,
            role="caregiver",
            permissions='["read","write"]',
            status="active",
        )
        db.add(membership)
        db.commit()
        return account.id, account.email, person.id, caregiver.id
    finally:
        db.close()


def token_for(account_id: str) -> str:
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).one()
        token, _ = issue_access_token(account.id, account.token_version)
        return token
    finally:
        db.close()


def test_protected_person_route_rejects_anonymous_request():
    _, _, person_id, _ = create_account_with_context()
    response = client.get(f"/api/v1/persons/{person_id}", headers={"Authorization": ""})
    assert response.status_code == 401


def test_protected_person_route_requires_care_circle_membership():
    account_id, _, person_id, _ = create_account_with_context()
    other_account = Account(
        email=f"outsider-{uuid.uuid4().hex}@example.test",
        password_hash=hash_password("correct-horse-battery-staple"),
        display_name="Outsider",
    )
    db = SessionLocal()
    try:
        db.add(other_account)
        db.commit()
        token, _ = issue_access_token(other_account.id, other_account.token_version)
    finally:
        db.close()

    response = client.get(
        f"/api/v1/persons/{person_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_member_can_read_own_care_record_and_wrong_caregiver_attribution_is_rejected():
    account_id, _, person_id, caregiver_id = create_account_with_context()
    token = token_for(account_id)
    headers = {"Authorization": f"Bearer {token}"}

    read_response = client.get(f"/api/v1/persons/{person_id}", headers=headers)
    assert read_response.status_code == 200

    response = client.post(
        "/api/situation",
        headers=headers,
        json={
            "caregiver_id": "not-the-authenticated-caregiver",
            "person_id": person_id,
            "raw_input": "A test observation that must not be saved.",
        },
    )
    assert response.status_code == 403


def test_logout_revokes_previously_issued_tokens():
    account_id, _, person_id, _ = create_account_with_context()
    token = token_for(account_id)
    headers = {"Authorization": f"Bearer {token}"}

    logout_response = client.post("/api/v1/auth/logout", headers=headers, json={})
    assert logout_response.status_code == 204

    read_response = client.get(f"/api/v1/persons/{person_id}", headers=headers)
    assert read_response.status_code == 401


def test_signup_rejects_short_password_and_login_does_not_return_password_hash():
    email = f"new-{uuid.uuid4().hex}@gmail.com"
    short = client.post("/api/v1/auth/signup", json={"email": email, "password": "short"})
    assert short.status_code == 422

    created = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "correct-horse-battery-staple", "display_name": "New User"},
    )
    assert created.status_code == 201, created.text

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    payload = login.json()
    assert payload["access_token"]
    assert payload["account"]["email"] == email
    assert "password_hash" not in payload["account"]



def test_invitation_is_email_bound_and_single_use():
    inviter_id, _, person_id, _ = create_account_with_context()
    invitee_email = f"invitee-{uuid.uuid4().hex}@gmail.com"
    invitee = Account(
        email=invitee_email,
        password_hash=hash_password("correct-horse-battery-staple"),
        display_name="Invited Caregiver",
    )
    caregiver_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(invitee)
        db.flush()
        db.add(Caregiver(
            id=caregiver_id,
            name="Invited Caregiver",
            email=invitee_email,
            relationship="family caregiver",
            person_id=person_id,
            timezone="UTC",
        ))
        db.commit()
        invitee_id = invitee.id
    finally:
        db.close()

    client = TestClient(app)
    invitation_response = client.post(
        "/api/v1/auth/caregiver-invitations",
        headers={"Authorization": f"Bearer {token_for(inviter_id)}"},
        json={"person_id": person_id, "caregiver_id": caregiver_id, "permissions": ["read", "write"]},
    )
    assert invitation_response.status_code == 201, invitation_response.text
    token = invitation_response.json()["invitation_token"]

    wrong_account = Account(
        email=f"wrong-{uuid.uuid4().hex}@gmail.com",
        password_hash=hash_password("correct-horse-battery-staple"),
        display_name="Wrong Recipient",
    )
    db = SessionLocal()
    try:
        db.add(wrong_account)
        db.commit()
        wrong_id = wrong_account.id
    finally:
        db.close()

    wrong_accept = client.post(
        "/api/v1/auth/caregiver-invitations/accept",
        headers={"Authorization": f"Bearer {token_for(wrong_id)}"},
        json={"token": token},
    )
    assert wrong_accept.status_code == 403

    accepted = client.post(
        "/api/v1/auth/caregiver-invitations/accept",
        headers={"Authorization": f"Bearer {token_for(invitee_id)}"},
        json={"token": token},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["person_id"] == person_id
    assert accepted.json()["caregiver_id"] == caregiver_id

    replay = client.post(
        "/api/v1/auth/caregiver-invitations/accept",
        headers={"Authorization": f"Bearer {token_for(invitee_id)}"},
        json={"token": token},
    )
    assert replay.status_code == 404



def test_read_only_membership_cannot_write_care_data():
    account_id, _, person_id, caregiver_id = create_account_with_context()
    db = SessionLocal()
    try:
        membership = db.query(CareMembership).filter(
            CareMembership.account_id == account_id,
            CareMembership.person_id == person_id,
            CareMembership.caregiver_id == caregiver_id,
        ).one()
        membership.permissions = '["read"]'
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/situation",
        headers={"Authorization": f"Bearer {token_for(account_id)}"},
        json={
            "caregiver_id": caregiver_id,
            "person_id": person_id,
            "raw_input": "This write must be denied for a read-only member.",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Your care-circle membership does not permit this action."


def test_account_can_create_and_select_multiple_care_contexts():
    account = Account(
        email=f"multi-context-{uuid.uuid4().hex}@gmail.com",
        password_hash=hash_password("correct-horse-battery-staple"),
        display_name="Multi Context User",
    )
    db = SessionLocal()
    try:
        db.add(account)
        db.commit()
        account_id = account.id
    finally:
        db.close()
    headers = {"Authorization": f"Bearer {token_for(account_id)}"}

    first = client.post("/api/v1/auth/care-contexts", headers=headers, json={"name": "First care record"})
    second = client.post("/api/v1/auth/care-contexts", headers=headers, json={"name": "Second care record"})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    contexts = client.get("/api/v1/auth/care-contexts", headers=headers)
    assert contexts.status_code == 200
    assert {item["person_id"] for item in contexts.json()} == {
        first.json()["person_id"],
        second.json()["person_id"],
    }



def test_cors_preflight_and_unauthorized_response_keep_cors_headers():
    preflight = client.options(
        "/api/situation",
        headers={
            "Origin": "https://solenosai.netlify.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "https://solenosai.netlify.app"

    denied = client.get(
        "/api/v1/persons",
        headers={"Authorization": "", "Origin": "https://solenosai.netlify.app"},
    )
    assert denied.status_code == 401
    assert denied.headers.get("access-control-allow-origin") == "https://solenosai.netlify.app"



def test_expired_bearer_token_is_rejected():
    account_id, _, person_id, _ = create_account_with_context()
    expired_token = jwt.encode(
        {
            "sub": account_id,
            "ver": "0",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
        get_settings().secret_key,
        algorithm="HS256",
    )
    response = client.get(
        f"/api/v1/persons/{person_id}",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401



def test_stateless_safety_validation_requires_authentication_but_not_a_person_scope():
    query = {
        "candidate_response": "The blood pressure was 145/90.",
        "risk_class": "informational",
    }
    body = {"claims": ["blood pressure was 145/90"], "evidence_ids": ["evidence-1"]}

    authenticated = client.post("/api/v1/validate-response", params=query, json=body)
    assert authenticated.status_code == 200, authenticated.text

    anonymous = client.post(
        "/api/v1/validate-response",
        params=query,
        json=body,
        headers={"Authorization": ""},
    )
    assert anonymous.status_code == 401
