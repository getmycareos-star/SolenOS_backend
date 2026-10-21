import uuid

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
    email = f"new-{uuid.uuid4().hex}@example.test"
    short = client.post("/api/v1/auth/signup", json={"email": email, "password": "short"})
    assert short.status_code == 422

    created = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "correct-horse-battery-staple", "display_name": "New User"},
    )
    assert created.status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    payload = login.json()
    assert payload["access_token"]
    assert payload["account"]["email"] == email
    assert "password_hash" not in payload["account"]
