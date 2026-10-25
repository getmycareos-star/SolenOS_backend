import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.account import CaregiverAccount

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


def register(client, email="caregiver@example.com", password="long-enough-password"):
    return client.post(
        "/api/v1/sessions/register",
        json={
            "email": email,
            "password": password,
            "caregiver_name": "Family caregiver",
            "care_recipient_name": "Care recipient",
        },
    )


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
    finally:
        db.close()

    me = client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["authenticated"] is True
    assert me.json()["account"]["id"] == body["account"]["id"]


def test_login_reuses_existing_account_and_care_record():
    client = TestClient(app)
    created = register(client)
    assert created.status_code == 201, created.text
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
    duplicate = register(client, email="CAREGIVER@example.com")
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
