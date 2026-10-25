import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.auth_account import AuthAccount, AuthSession

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


def signup(client, email="caregiver@example.com"):
    return client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": "a-long-caregiver-password-2026",
            "display_name": "Family caregiver",
            "care_recipient_name": "Care recipient",
        },
    )


def test_signup_persists_account_and_both_revocable_session_capabilities():
    client = TestClient(app)
    response = signup(client, "  Caregiver@Example.com ")
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["account"]["email"] == "caregiver@example.com"
    assert data["person"]["name"] == "Care recipient"
    assert data["caregiver"]["name"] == "Family caregiver"
    assert data["auth_mode"] == "account"
    assert data["access_token"] != data["account_token"]
    assert "password" not in data
    assert "password_hash" not in data

    db = TestSessionLocal()
    try:
        account = db.query(AuthAccount).filter_by(email="caregiver@example.com").one()
        assert account.person_id == data["person"]["id"]
        assert account.caregiver_id == data["caregiver"]["id"]
        assert account.password_hash != "a-long-caregiver-password-2026"
        assert account.password_hash.startswith("scrypt:")
        account_session = db.query(AuthSession).filter_by(account_id=account.id).one()
        assert account_session.token_digest == hashlib.sha256(data["account_token"].encode()).hexdigest()
        care_session = db.query(AnonymousSession).filter_by(id=data["session_id"]).one()
        assert care_session.account_id == account.id
        assert care_session.token_hash == hashlib.sha256(data["access_token"].encode()).hexdigest()
    finally:
        db.close()


def test_login_reuses_the_existing_care_record_and_rejects_bad_credentials():
    client = TestClient(app)
    created = signup(client)
    assert created.status_code == 201, created.text
    original = created.json()

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "CAREGIVER@example.com", "password": "a-long-caregiver-password-2026"},
    )
    assert login.status_code == 200, login.text
    data = login.json()
    assert data["person"]["id"] == original["person"]["id"]
    assert data["caregiver"]["id"] == original["caregiver"]["id"]
    assert data["account"]["id"] == original["account"]["id"]
    assert data["access_token"] != original["access_token"]
    assert data["account_token"] != original["account_token"]

    bad = client.post(
        "/api/v1/auth/login",
        json={"email": "caregiver@example.com", "password": "incorrect-password"},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Email or password is incorrect"


def test_duplicate_signup_and_weak_password_are_rejected_without_extra_accounts():
    client = TestClient(app)
    first = signup(client)
    assert first.status_code == 201, first.text
    duplicate = signup(client)
    assert duplicate.status_code == 409, duplicate.text

    weak = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "other@example.com",
            "password": "short",
            "display_name": "Caregiver",
            "care_recipient_name": "Care recipient",
        },
    )
    assert weak.status_code == 422, weak.text
    db = TestSessionLocal()
    try:
        assert db.query(AuthAccount).count() == 1
    finally:
        db.close()


def test_account_logout_revokes_account_and_care_sessions():
    client = TestClient(app)
    created = signup(client)
    assert created.status_code == 201, created.text
    data = created.json()

    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['account_token']}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["person_id"] == data["person"]["id"]

    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {data['account_token']}"},
    )
    assert logout.status_code == 200, logout.text
    assert logout.json()["revoked"] is True

    after_logout = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['account_token']}"},
    )
    assert after_logout.status_code == 401

    care_refresh = client.post(
        "/api/v1/sessions/refresh",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert care_refresh.status_code == 401

def test_anonymous_session_can_be_revoked_without_creating_a_replacement():
    client = TestClient(app)
    created = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "Care recipient", "caregiver_name": "Caregiver"},
    )
    assert created.status_code == 201, created.text
    session = created.json()

    revoked = client.post(
        "/api/v1/sessions/revoke",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True

    refresh = client.post(
        "/api/v1/sessions/refresh",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert refresh.status_code == 401

def test_signup_links_existing_anonymous_care_record_without_forking_it():
    client = TestClient(app)
    anonymous = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "Existing care record", "caregiver_name": "Existing caregiver"},
    )
    assert anonymous.status_code == 201, anonymous.text
    original = anonymous.json()

    signup_response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "upgrade@example.com",
            "password": "a-long-caregiver-password-2026",
            "display_name": "Account holder",
            "care_recipient_name": "A different name must not create another record",
        },
        headers={"Authorization": f"Bearer {original['access_token']}"},
    )
    assert signup_response.status_code == 201, signup_response.text
    linked = signup_response.json()
    assert linked["person"]["id"] == original["person"]["id"]
    assert linked["person"]["name"] == "Existing care record"
    assert linked["caregiver"]["id"] == original["caregiver"]["id"]
    assert linked["session_id"] == original["session_id"]
    assert linked["access_token"] == original["access_token"]

    db = TestSessionLocal()
    try:
        account = db.query(AuthAccount).filter_by(email="upgrade@example.com").one()
        assert account.person_id == original["person"]["id"]
        assert db.query(AuthAccount).count() == 1
        assert db.query(AnonymousSession).count() == 1
    finally:
        db.close()


def test_signup_rejects_expired_or_invalid_existing_session_without_creating_a_record():
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "invalid-session@example.com",
            "password": "a-long-caregiver-password-2026",
            "display_name": "Account holder",
            "care_recipient_name": "Care recipient",
        },
        headers={"Authorization": "Bearer not-a-real-session-token"},
    )
    assert response.status_code == 401, response.text
    db = TestSessionLocal()
    try:
        assert db.query(AuthAccount).count() == 0
        assert db.query(AnonymousSession).count() == 0
    finally:
        db.close()
