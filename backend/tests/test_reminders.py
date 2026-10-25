from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

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


def create_session(client: TestClient, caregiver_name: str):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": caregiver_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_reminder_create_list_complete_persists_attribution():
    client = TestClient(app)
    session = create_session(client, "Caregiver one")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    created = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Review appointment questions",
            "note": "Check the questions before the visit.",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201, created.text
    reminder = created.json()
    assert reminder["person_id"] == session["person"]["id"]
    assert reminder["created_by_caregiver_id"] == session["caregiver"]["id"]
    assert reminder["status"] == "open"
    assert reminder["delivery_mode"] == "in_app_list_only"
    assert reminder["notification_delivery_status"] == "not_sent"

    listed = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [reminder["id"]]

    completed = client.patch(
        f"/api/v1/reminders/{reminder['id']}/complete",
        headers=headers,
        json={"completion_note": "Questions reviewed."},
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "completed"
    assert body["completed_by_caregiver_id"] == session["caregiver"]["id"]
    assert body["completed_at"] is not None
    assert body["completion_note"] == "Questions reviewed."

    listed_completed = client.get(
        f"/api/v1/reminders/{session['person']['id']}?status=completed",
        headers=headers,
    )
    assert listed_completed.status_code == 200, listed_completed.text
    assert listed_completed.json()[0]["id"] == reminder["id"]


def test_reminder_endpoints_reject_cross_record_access():
    client = TestClient(app)
    first = create_session(client, "Caregiver one")
    second = create_session(client, "Caregiver two")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    created = client.post(
        "/api/v1/reminders",
        headers=first_headers,
        json={
            "person_id": first["person"]["id"],
            "title": "Follow up",
            "due_at": due_at,
        },
    )
    assert created.status_code == 201, created.text
    reminder_id = created.json()["id"]

    spoofed_list = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers=second_headers,
    )
    assert spoofed_list.status_code == 403, spoofed_list.text

    spoofed_create = client.post(
        "/api/v1/reminders",
        headers=second_headers,
        json={
            "person_id": first["person"]["id"],
            "title": "Attempted cross-record write",
            "due_at": due_at,
        },
    )
    assert spoofed_create.status_code == 403, spoofed_create.text

    hidden_complete = client.patch(
        f"/api/v1/reminders/{reminder_id}/complete",
        headers=second_headers,
        json={},
    )
    assert hidden_complete.status_code == 404, hidden_complete.text

    hidden_cancel = client.patch(
        f"/api/v1/reminders/{reminder_id}/cancel",
        headers=second_headers,
        json={},
    )
    assert hidden_cancel.status_code == 404, hidden_cancel.text

    still_open = client.get(
        f"/api/v1/reminders/{first['person']['id']}",
        headers=first_headers,
    )
    assert still_open.status_code == 200, still_open.text
    assert still_open.json()[0]["status"] == "open"


def test_reminder_requires_timezone_aware_due_date_and_nonblank_title():
    client = TestClient(app)
    session = create_session(client, "Caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    naive = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "Follow up",
            "due_at": "2026-10-26T12:00:00",
        },
    )
    assert naive.status_code == 422, naive.text

    blank = client.post(
        "/api/v1/reminders",
        headers=headers,
        json={
            "person_id": session["person"]["id"],
            "title": "   ",
            "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    assert blank.status_code == 422, blank.text
