"""Authenticated daily-capture integration tests against the current API contract."""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.auth_identity import AuthAccount
from app.models.care import CareEvent, Caregiver, Person
from app.models.capture import DailyCapture


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def create_authenticated_owner(client: TestClient):
    signup = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "daily-capture-owner@example.com",
            "password": "a-long-test-password-2026",
            "display_name": "Daily Capture Owner",
        },
    )
    assert signup.status_code == 201, signup.text
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "daily-capture-owner@example.com",
            "password": "a-long-test-password-2026",
        },
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    context = client.post(
        "/api/v1/auth/care-contexts",
        headers=headers,
        json={"name": "Daily Capture Test Person"},
    )
    assert context.status_code == 201, context.text
    caregivers = client.get(
        f"/api/v1/caregivers/{context.json()['person_id']}",
        headers=headers,
    )
    assert caregivers.status_code == 200, caregivers.text
    assert len(caregivers.json()) == 1
    return headers, context.json(), caregivers.json()[0]["id"]


def test_daily_capture_persists_for_linked_caregiver():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, context, caregiver_id = create_authenticated_owner(client)
            response = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": context["person_id"],
                    "caregiver_id": caregiver_id,
                    "medications": [],
                    "vitals": [],
                    "scales": [{"axis": "mood", "score": 2, "observed_at": "2026-10-21T10:00:00Z", "note": "Caregiver-reported calm morning"}],
                    "notes": "User-recorded note",
                },
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["success"] is True
            assert body["daily_capture_id"]
            assert body["care_event_id"]

            with TestingSessionLocal() as db:
                capture = db.query(DailyCapture).filter_by(id=body["daily_capture_id"]).one()
                event = db.query(CareEvent).filter_by(id=body["care_event_id"]).one()
                assert capture.person_id == context["person_id"]
                assert capture.caregiver_id == caregiver_id
                assert capture.notes == "User-recorded note"
                assert capture.scales[0]["note"] == "Caregiver-reported calm morning"
                assert event.person_id == context["person_id"]
                assert event.created_by_caregiver_id == caregiver_id
                assert "User-recorded note" in event.description
    finally:
        teardown_db()


def test_daily_capture_rejects_person_without_explicit_account_grant():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, _, _ = create_authenticated_owner(client)
            response = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": "ungranted-person-id",
                    "caregiver_id": "unrelated-caregiver-id",
                    "notes": "Must not be stored",
                },
            )
            assert response.status_code == 403, response.text
            assert response.json()["detail"] == "No access to this care record"
    finally:
        teardown_db()


def test_daily_capture_rejects_caregiver_identity_not_linked_to_account():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, context, _ = create_authenticated_owner(client)
            with TestingSessionLocal() as db:
                unlinked = Caregiver(
                    name="Unlinked caregiver",
                    email="unlinked-daily@example.invalid",
                    relationship="family",
                    person_id=context["person_id"],
                    timezone="UTC",
                )
                db.add(unlinked)
                db.commit()
                unlinked_id = unlinked.id

            response = client.post(
                "/api/v1/daily/log",
                headers=headers,
                json={
                    "person_id": context["person_id"],
                    "caregiver_id": unlinked_id,
                    "notes": "Must not be attributed to an unlinked caregiver",
                },
            )
            assert response.status_code == 403, response.text
            assert response.json()["detail"] == "Authenticated account is not linked to this caregiver identity"
            with TestingSessionLocal() as db:
                assert db.query(DailyCapture).count() == 0
    finally:
        teardown_db()
