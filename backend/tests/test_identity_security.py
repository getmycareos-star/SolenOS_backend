import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.caregiver_account import CaregiverAccount
from app.models.identity_security import AccountRecoveryRequest, CaregiverDevice, DeviceTrustRecord

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
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_device_registration_is_scoped_and_never_trusted_by_itself():
    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    first_auth = {"Authorization": f"Bearer {first['access_token']}"}
    second_auth = {"Authorization": f"Bearer {second['access_token']}"}

    registered = client.post(
        "/api/v1/identity/devices",
        headers=first_auth,
        json={"installation_key": "random-installation-key-123456789", "label": "This browser/device", "platform": "browser"},
    )
    assert registered.status_code == 201, registered.text
    device = registered.json()["device"]
    assert device["trust_status"] == "unverified"
    assert device["recovery_can_use_device_alone"] is False

    listed = client.get("/api/v1/identity/devices", headers=first_auth)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["devices"]] == [device["id"]]

    hidden = client.get("/api/v1/identity/devices", headers=second_auth)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["devices"] == []

    forbidden = client.delete(f"/api/v1/identity/devices/{device['id']}", headers=second_auth)
    assert forbidden.status_code == 404, forbidden.text

    db = TestSessionLocal()
    try:
        row = db.query(CaregiverDevice).filter_by(id=device["id"]).one()
        trust = db.query(DeviceTrustRecord).filter_by(device_id=device["id"]).one()
        assert row.installation_key_hash != "random-installation-key-123456789"
        assert trust.trust_status == "unverified"
    finally:
        db.close()


def test_recovery_request_does_not_disclose_whether_account_exists():
    client = TestClient(app)
    session = create_session(client, "Account holder")
    db = TestSessionLocal()
    try:
        db.add(CaregiverAccount(
            id="test-account-id",
            email_normalized="known@example.com",
            password_hash="not-used-by-this-test",
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ))
        db.commit()
    finally:
        db.close()

    known = client.post("/api/v1/identity/recovery-requests", json={"email": "known@example.com"})
    unknown = client.post("/api/v1/identity/recovery-requests", json={"email": "unknown@example.com"})
    assert known.status_code == 202, known.text
    assert unknown.status_code == 202, unknown.text
    assert known.json()["message"] == unknown.json()["message"]

    db = TestSessionLocal()
    try:
        rows = db.query(AccountRecoveryRequest).order_by(AccountRecoveryRequest.requested_at).all()
        assert len(rows) == 2
        assert sum(1 for row in rows if row.account_id is not None) == 1
        assert all(row.email_hash not in {"known@example.com", "unknown@example.com"} for row in rows)
    finally:
        db.close()
