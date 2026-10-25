from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, SessionLocal as DefaultSessionLocal, get_db
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


def create_session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_reminder(client, session, title="Check appointment notes"):
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = client.post(
        "/api/v1/reminders",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "title": title,
            "note": "Caregiver-entered note",
            "due_at": due_at,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reminder_create_list_complete_and_cancel_persist_in_database():
    client = TestClient(app)
    session = create_session(client, "Reminder test person")
    created = create_reminder(client, session)

    assert created["status"] == "open"
    assert created["created_by_caregiver_id"] == session["caregiver"]["id"]
    assert created["delivery_mode"] == "in_app_list_only"
    assert created["notification_delivery_status"] == "not_sent"

    listed = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created["id"]]

    completed = client.patch(
        f"/api/v1/reminders/{created['id']}/complete",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"completion_note": "Reviewed"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_by_caregiver_id"] == session["caregiver"]["id"]

    cancellable = create_reminder(client, session, "Check transport plan")
    cancelled = client.patch(
        f"/api/v1/reminders/{cancellable['id']}/cancel",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelled_by_caregiver_id"] == session["caregiver"]["id"]

    # Re-query using a fresh SQLAlchemy session to assert persistence beyond the request.
    from app.models.reminder import CareReminder

    db = TestSessionLocal()
    try:
        stored_completed = db.query(CareReminder).filter_by(id=created["id"]).one()
        stored_cancelled = db.query(CareReminder).filter_by(id=cancellable["id"]).one()
        assert stored_completed.status == "completed"
        assert stored_completed.completion_note == "Reviewed"
        assert stored_cancelled.status == "cancelled"
    finally:
        db.close()


def test_reminders_require_bearer_session_and_enforce_person_scope():
    client = TestClient(app)
    first = create_session(client, "First test person")
    second = create_session(client, "Second test person")
    first_reminder = create_reminder(client, first)

    no_auth = client.get(f"/api/v1/reminders/{first['person']['id']}")
    assert no_auth.status_code == 401, no_auth.text

    cross_list = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert cross_list.status_code == 403, cross_list.text

    cross_complete = client.patch(
        f"/api/v1/reminders/{first_reminder['id']}/complete",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={},
    )
    assert cross_complete.status_code == 404, cross_complete.text

    cross_cancel = client.patch(
        f"/api/v1/reminders/{first_reminder['id']}/cancel",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert cross_cancel.status_code == 404, cross_cancel.text


def test_reminder_rejects_missing_timezone_and_blank_title():
    client = TestClient(app)
    session = create_session(client, "Validation test person")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    missing_timezone = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Timezone test",
            "due_at": "2026-10-26T09:00:00",
        },
    )
    assert missing_timezone.status_code == 422, missing_timezone.text

    blank_title = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "   ",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    assert blank_title.status_code == 422, blank_title.text
