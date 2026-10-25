import base64
import os
import re
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.api.account_security as account_security
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.account import CaregiverAccount

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
sent_messages = []
_original_send_email = account_security._send_email


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    sent_messages.clear()
    os.environ["ACCOUNT_SECURITY_KEY"] = "test-key-that-is-at-least-32-bytes-long"
    os.environ["APP_PUBLIC_URL"] = "https://solenos.test"
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS account_security_tokens (
            id VARCHAR(36) PRIMARY KEY, account_id VARCHAR NOT NULL, purpose VARCHAR(32) NOT NULL,
            token_hash VARCHAR(64) NOT NULL UNIQUE, expires_at DATETIME NOT NULL,
            consumed_at DATETIME NULL, created_at DATETIME NOT NULL, request_ip_hash VARCHAR(64) NULL)""")
        conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS account_mfa_secrets (
            account_id VARCHAR PRIMARY KEY, secret_ciphertext TEXT NOT NULL,
            confirmed_at DATETIME NULL, updated_at DATETIME NOT NULL)""")
        conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS account_mfa_recovery_codes (
            id VARCHAR(36) PRIMARY KEY, account_id VARCHAR NOT NULL, code_hash VARCHAR(64) NOT NULL,
            used_at DATETIME NULL, created_at DATETIME NOT NULL)""")
        conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS account_login_attempts (
            id VARCHAR(36) PRIMARY KEY, email_hash VARCHAR(64) NOT NULL, ip_hash VARCHAR(64) NOT NULL,
            attempted_at DATETIME NOT NULL, succeeded BOOLEAN NOT NULL)""")
        conn.exec_driver_sql("""CREATE TABLE IF NOT EXISTS account_security_requests (
            id VARCHAR(36) PRIMARY KEY, email_hash VARCHAR(64) NOT NULL, ip_hash VARCHAR(64) NOT NULL,
            purpose VARCHAR(32) NOT NULL, requested_at DATETIME NOT NULL)""")
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    account_security._send_email = lambda to, subject, body: sent_messages.append((to, subject, body))


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    account_security._send_email = _original_send_email
    Base.metadata.drop_all(bind=engine)


def _verify_from_email(client, email):
    message = next(message for message in reversed(sent_messages) if message[0] == email and "Verify" in message[1])
    token = re.search(r"token=([A-Za-z0-9_-]+)", message[2]).group(1)
    return client.post("/api/v1/account-security/verification/confirm", json={"token": token})


def _set_verified_for_test(email):
    db = TestSessionLocal()
    try:
        account = db.query(CaregiverAccount).filter_by(email=email.lower()).one()
        account.email_verified_at = account_security._now()
        db.commit()
    finally:
        db.close()


def _complete_registration(client, response, email, password):
    body = response.json()
    _verify_from_email(client, email)
    logged = client.post("/api/v1/sessions/login", json={"email": email, "password": password})
    assert logged.status_code == 200, logged.text
    return SimpleNamespace(status_code=201, text=response.text, json=lambda: {**body, **logged.json()})


def register(client, email="caregiver@example.com", password="long-enough-password"):
    response = client.post(
        "/api/v1/sessions/register",
        json={
            "email": email,
            "password": password,
            "caregiver_name": "Family caregiver",
            "care_recipient_name": "Care recipient",
        },
    )
    if response.status_code == 201:
        return _complete_registration(client, response, email, password)
    return response


def test_register_requires_email_verification_and_only_then_issues_session():
    client = TestClient(app)
    raw = client.post("/api/v1/sessions/register", json={
        "email": "caregiver@example.com", "password": "long-enough-password",
        "caregiver_name": "Family caregiver", "care_recipient_name": "Care recipient",
    })
    assert raw.status_code == 201, raw.text
    assert raw.json()["email_verification_required"] is True
    assert "access_token" not in raw.json()
    denied = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "long-enough-password"})
    assert denied.status_code == 403
    verified = _verify_from_email(client, "caregiver@example.com")
    assert verified.status_code == 200, verified.text
    token = re.search(r"token=([A-Za-z0-9_-]+)", sent_messages[-1][2]).group(1)
    replay = client.post("/api/v1/account-security/verification/confirm", json={"token": token})
    assert replay.status_code == 400
    login = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "long-enough-password"})
    assert login.status_code == 200, login.text
    assert login.json()["access_token"]


def test_register_persists_account_with_hashed_password_and_scoped_session():
    client = TestClient(app)
    response = register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["account"]["email"] == "caregiver@example.com"
    assert body["person"]["id"] and body["caregiver"]["id"] and body["access_token"]
    db = TestSessionLocal()
    try:
        account = db.query(CaregiverAccount).filter_by(email="caregiver@example.com").one()
        assert account.password_hash != "long-enough-password"
        assert account.password_hash.startswith("scrypt$")
        assert account.person_id == body["person"]["id"]
        assert account.caregiver_id == body["caregiver"]["id"]
        assert account.email_verified_at is not None
    finally:
        db.close()
    me = client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["authenticated"] is True
    assert me.json()["account"]["id"] == body["account"]["id"]


def test_login_reuses_existing_account_and_care_record():
    client = TestClient(app)
    created = register(client)
    logged_in = client.post(
        "/api/v1/sessions/login",
        json={"email": "CAREGIVER@example.com", "password": "long-enough-password"},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()["account"]["id"] == created.json()["account"]["id"]
    assert logged_in.json()["person"]["id"] == created.json()["person"]["id"]
    assert logged_in.json()["access_token"] != created.json()["access_token"]


def test_login_rejects_wrong_password_and_unknown_email_with_same_error():
    client = TestClient(app)
    register(client)
    wrong_password = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "wrong-password"})
    unknown_email = client.post("/api/v1/sessions/login", json={"email": "unknown@example.com", "password": "wrong-password"})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["detail"] == unknown_email.json()["detail"]


def test_duplicate_account_email_is_rejected_case_insensitively():
    client = TestClient(app)
    assert register(client).status_code == 201
    duplicate = client.post("/api/v1/sessions/register", json={
        "email": "CAREGIVER@example.com", "password": "long-enough-password",
        "caregiver_name": "Family caregiver", "care_recipient_name": "Care recipient",
    })
    assert duplicate.status_code == 409


def test_anonymous_entry_remains_available_without_account():
    client = TestClient(app)
    response = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Anonymous caregiver"})
    assert response.status_code == 201, response.text
    me = client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json() == {"authenticated": False, "account": None, "session_type": "anonymous"}


def test_account_token_cannot_access_another_care_record():
    client = TestClient(app)
    account = register(client).json()
    other = client.post("/api/v1/sessions/anonymous", json={"name": "Other person"}).json()
    response = client.get(
        f"/api/v1/evidence/{account['person']['id']}",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert response.status_code == 403, response.text


def test_account_session_can_be_revoked_server_side():
    client = TestClient(app)
    created = register(client)
    token = created.json()["access_token"]
    revoked = client.post("/api/v1/sessions/revoke", headers={"Authorization": f"Bearer {token}"}, json={})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True
    current = client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {token}"})
    assert current.status_code == 401, current.text


def test_signup_safely_claims_existing_anonymous_record_and_revokes_old_capability():
    client = TestClient(app)
    anonymous = client.post("/api/v1/sessions/anonymous", json={"name": "Mum", "caregiver_name": "David"})
    assert anonymous.status_code == 201, anonymous.text
    old = anonymous.json()
    registered = client.post("/api/v1/sessions/register", json={
        "email": "claim@example.com", "password": "claim-long-password",
        "caregiver_name": "David", "care_recipient_name": "Mum",
        "anonymous_access_token": old["access_token"],
    })
    assert registered.status_code == 201, registered.text
    assert registered.json()["claimed_existing_record"] is True
    assert registered.json()["person"]["id"] == old["person"]["id"]
    assert registered.json()["caregiver"]["id"] == old["caregiver"]["id"]
    assert client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {old['access_token']}"}).status_code == 401
    verified = _verify_from_email(client, "claim@example.com")
    assert verified.status_code == 200, verified.text
    login = client.post("/api/v1/sessions/login", json={"email": "claim@example.com", "password": "claim-long-password"})
    assert login.status_code == 200, login.text
    assert client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}).json()["account"]["email"] == "claim@example.com"


def test_signup_does_not_claim_record_without_valid_anonymous_ownership_proof():
    client = TestClient(app)
    response = client.post("/api/v1/sessions/register", json={
        "email": "unproven-claim@example.com", "password": "claim-long-password",
        "caregiver_name": "David", "care_recipient_name": "Mum",
        "anonymous_access_token": "not-a-real-session-token",
    })
    assert response.status_code == 401, response.text
    db = TestSessionLocal()
    try:
        assert db.query(CaregiverAccount).filter_by(email="unproven-claim@example.com").first() is None
    finally:
        db.close()


def test_password_reset_revokes_existing_sessions():
    client = TestClient(app)
    created = register(client)
    old_token = created.json()["access_token"]
    requested = client.post("/api/v1/account-security/password-reset/request", json={"email": "caregiver@example.com"})
    assert requested.status_code == 202, requested.text
    message = next(message for message in reversed(sent_messages) if "Reset" in message[1])
    token = re.search(r"token=([A-Za-z0-9_-]+)", message[2]).group(1)
    reset = client.post("/api/v1/account-security/password-reset/confirm", json={"token": token, "new_password": "a-different-long-password"})
    assert reset.status_code == 200, reset.text
    assert client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    login = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "a-different-long-password"})
    assert login.status_code == 200, login.text


def test_mfa_is_enforced_during_login_and_recovery_codes_are_single_use():
    client = TestClient(app)
    created = register(client)
    headers = {"Authorization": f"Bearer {created.json()['access_token']}"}
    enrolled = client.post("/api/v1/account-security/mfa/enroll", headers=headers)
    assert enrolled.status_code == 200, enrolled.text
    secret = base64.b32decode(enrolled.json()["secret"] + "=" * ((8 - len(enrolled.json()["secret"]) % 8) % 8))
    code = account_security._totp(secret, time.time())
    confirmed = client.post("/api/v1/account-security/mfa/confirm", headers=headers, json={"code": code})
    assert confirmed.status_code == 200, confirmed.text
    recovery_code = confirmed.json()["recovery_codes"][0]
    no_factor = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "long-enough-password"})
    assert no_factor.status_code == 401
    login = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "long-enough-password", "mfa_code": recovery_code})
    assert login.status_code == 200, login.text
    replay = client.post("/api/v1/sessions/login", json={"email": "caregiver@example.com", "password": "long-enough-password", "mfa_code": recovery_code})
    assert replay.status_code == 401
