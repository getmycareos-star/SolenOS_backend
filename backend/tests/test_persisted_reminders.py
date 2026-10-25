import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.reminder import CareReminder

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
        json={"name": name, "caregiver_name": "Family caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_reminder(client, session, title="Review appointment questions"):
    return client.post(
        "/api/v1/reminders",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "title": title,
            "note": "Ask the clinician to clarify the next step.",
            "due_at": "2026-11-02T10:30:00+00:00",
        },
    )


def test_reminder_create_list_and_lifecycle_are_persisted_with_server_attribution():
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    created = create_reminder(client, session)
    assert created.status_code == 201, created.text
    reminder = created.json()
    assert reminder["person_id"] == session["person"]["id"]
    assert reminder["created_by_caregiver_id"] == session["caregiver"]["id"]
    assert reminder["status"] == "open"
    assert reminder["delivery_mode"] == "in_app_list_only"
    assert reminder["notification_delivery_status"] == "not_sent"

    listed = client.get(f"/api/v1/reminders/{session['person']['id']}", headers=auth)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [reminder["id"]]

    completed = client.patch(
        f"/api/v1/reminders/{reminder['id']}/complete",
        headers=auth,
        json={},
    )
    assert completed.status_code == 200, completed.text
    completed_body = completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["completed_by_caregiver_id"] == session["caregiver"]["id"]
    assert completed_body["completed_at"]

    # A separate SQLAlchemy session confirms that the write was committed, not only held in response state.
    db = TestSessionLocal()
    try:
        stored = db.query(CareReminder).filter_by(id=reminder["id"]).one()
        assert stored.status == "completed"
        assert stored.completed_by_caregiver_id == session["caregiver"]["id"]
    finally:
        db.close()


def test_reminder_access_is_scoped_and_foreign_record_is_not_disclosed():
    client = TestClient(app)
    first = create_session(client, "First care recipient")
    second = create_session(client, "Second care recipient")
    created = create_reminder(client, first, "Follow up on the appointment")
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]
    second_auth = {"Authorization": f"Bearer {second['access_token']}"}

    foreign_list = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers=second_auth,
    )
    assert foreign_list.status_code == 403, foreign_list.text

    foreign_complete = client.patch(
        f"/api/v1/reminders/{reminder_id}/complete",
        headers=second_auth,
        json={},
    )
    assert foreign_complete.status_code == 404, foreign_complete.text

    db = TestSessionLocal()
    try:
        stored = db.query(CareReminder).filter_by(id=reminder_id).one()
        assert stored.status == "open"
        assert stored.completed_by_caregiver_id is None
    finally:
        db.close()


def test_reminder_rejects_forged_person_id_and_timezone_naive_due_date():
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    auth = {"Authorization": f"Bearer {session['access_token']}"}

    forged = client.post(
        "/api/v1/reminders",
        headers=auth,
        json={
            "person_id": "another-person-id",
            "title": "A follow-up",
            "due_at": "2026-11-02T10:30:00+00:00",
        },
    )
    assert forged.status_code == 403, forged.text

    naive = client.post(
        "/api/v1/reminders",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "title": "A follow-up",
            "due_at": "2026-11-02T10:30:00",
        },
    )
    assert naive.status_code == 422, naive.text
