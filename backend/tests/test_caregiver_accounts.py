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


def create_session(client):
    response = client.post("/api/v1/sessions/anonymous", json={"name": "Care recipient", "caregiver_name": "Caregiver"})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_account_sign_in_and_delete_account():
    client = TestClient(app)
    session = create_session(client)
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    created = client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"email": "Caregiver@example.com", "password": "a-long-test-password-123"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["account"]["email"] == "caregiver@example.com"
    assert created.json()["caregiver_profile_id"] == session["caregiver"]["profile_id"]

    from app.models.caregiver_intelligence import CaregiverProfile
    db = TestSessionLocal()
    try:
        linked_profile = db.query(CaregiverProfile).filter_by(
            session_caregiver_id=session["caregiver"]["id"]
        ).one()
        assert linked_profile.id == session["caregiver"]["profile_id"]
        assert linked_profile.email == "caregiver@example.com"
        assert linked_profile.person_id == session["person"]["id"]
    finally:
        db.close()

    current = client.get("/api/v1/accounts/me", headers=headers)
    assert current.status_code == 200
    assert current.json()["account"]["email"] == "caregiver@example.com"

    login = client.post(
        "/api/v1/accounts/login",
        json={"email": "CAREGIVER@example.com", "password": "a-long-test-password-123"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["person_id"] == session["person"]["id"]
    assert login.json()["caregiver_id"] == session["caregiver"]["id"]
    assert login.json()["caregiver_profile_id"] == session["caregiver"]["profile_id"]
    assert login.json()["access_token"] != session["access_token"]

    wrong_password = client.request(
        "DELETE", "/api/v1/accounts/me", headers=headers, json={"password": "wrong-password"}
    )
    assert wrong_password.status_code == 401

    deleted = client.request(
        "DELETE", "/api/v1/accounts/me", headers=headers, json={"password": "a-long-test-password-123"}
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert deleted.json()["care_record_retained"] is True
    assert deleted.json()["other_sessions_revoked"] is True
    db = TestSessionLocal()
    try:
        retained_profile = db.query(CaregiverProfile).filter_by(session_caregiver_id=session["caregiver"]["id"]).one()
        assert retained_profile.email is None
        assert retained_profile.session_caregiver_id == session["caregiver"]["id"]
    finally:
        db.close()
    assert client.get("/api/v1/accounts/me", headers=headers).json()["account"] is None
    revoked_login_session = client.get(
        f"/api/v1/evidence/{session['person']['id']}",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert revoked_login_session.status_code == 401


def test_account_cannot_be_created_for_another_care_record():
    client = TestClient(app)
    first = create_session(client)
    second = create_session(client)
    response = client.post(
        "/api/v1/accounts",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={"email": "caregiver@example.com", "password": "a-long-test-password-123", "person_id": second["person"]["id"]},
    )
    assert response.status_code == 403, response.text


def test_revoke_endpoint_revokes_only_the_current_bearer_session():
    client = TestClient(app)
    current = create_session(client)
    other = create_session(client)
    response = client.post(
        "/api/v1/sessions/revoke",
        headers={"Authorization": f"Bearer {current['access_token']}"},
        json={},
    )
    assert response.status_code == 200, response.text
    assert response.json()["revoked"] is True
    revoked_read = client.get(
        f"/api/v1/evidence/{current['person']['id']}",
        headers={"Authorization": f"Bearer {current['access_token']}"},
    )
    assert revoked_read.status_code == 401
    other_read = client.get(
        f"/api/v1/evidence/{other['person']['id']}",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert other_read.status_code == 200, other_read.text
