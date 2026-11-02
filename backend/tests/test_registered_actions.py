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


def create_evidence(client, session, source_text="Caregiver recorded that the clinic asked for a follow-up call."):
    response = client.post(
        "/api/v1/evidence",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "type": "caregiver_report",
            "source_text": source_text,
            "uploaded_by_caregiver_id": session["caregiver"]["id"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def approval_payload(evidence_id, **overrides):
    payload = {
        "explicit_approval": True,
        "idempotency_key": "care-action-approval-key-001",
        "title": "Call the clinic to confirm the appointment details",
        "action_type": "call",
        "rationale": "The saved caregiver report records a request for a follow-up call.",
        "limitations": "The record does not establish a clinical urgency.",
        "evidence_ids": [evidence_id],
    }
    payload.update(overrides)
    return payload


def headers(session):
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_suggestion_is_not_registered_until_explicit_approval_and_persists():
    client = TestClient(app)
    session = create_session(client, "Action recipient")
    evidence = create_evidence(client, session)

    empty = client.get(
        f"/api/v1/registered-actions/{session['person']['id']}",
        headers=headers(session),
    )
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    missing_approval = approval_payload(evidence["id"])
    missing_approval.pop("explicit_approval")
    rejected = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=missing_approval,
    )
    assert rejected.status_code == 422, rejected.text

    approved = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=approval_payload(evidence["id"]),
    )
    assert approved.status_code == 201, approved.text
    action = approved.json()
    assert action["status"] == "approved"
    assert action["created_by_caregiver_id"] == session["caregiver"]["id"]
    assert action["source_evidence_ids"] == [evidence["id"]]
    assert action["approved_at"]

    replay = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=approval_payload(evidence["id"]),
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == action["id"]

    listed = client.get(
        f"/api/v1/registered-actions/{session['person']['id']}",
        headers=headers(session),
    )
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [action["id"]]

    from app.models.action import Action
    db = TestSessionLocal()
    try:
        assert db.query(Action).filter_by(person_id=session["person"]["id"]).count() == 1
    finally:
        db.close()


def test_registered_action_requires_auth_and_isolated_to_care_recipient():
    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    evidence = create_evidence(client, first)
    action_response = client.post(
        f"/api/v1/registered-actions/{first['person']['id']}/approve",
        headers=headers(first),
        json=approval_payload(evidence["id"]),
    )
    assert action_response.status_code == 201, action_response.text
    action_id = action_response.json()["id"]

    no_auth = client.get(f"/api/v1/registered-actions/{first['person']['id']}")
    assert no_auth.status_code == 401, no_auth.text

    cross_list = client.get(
        f"/api/v1/registered-actions/{first['person']['id']}",
        headers=headers(second),
    )
    assert cross_list.status_code == 403, cross_list.text

    cross_history = client.get(
        f"/api/v1/registered-actions/{action_id}/history",
        headers=headers(second),
    )
    assert cross_history.status_code == 404, cross_history.text

    cross_status = client.patch(
        f"/api/v1/registered-actions/{action_id}/status",
        headers=headers(second),
        json={"status": "cancelled", "expected_version": 1, "reason": "Not mine"},
    )
    assert cross_status.status_code == 404, cross_status.text


def test_lifecycle_transitions_require_explicit_completion_and_write_actor_audit():
    client = TestClient(app)
    session = create_session(client, "Lifecycle recipient")
    evidence = create_evidence(client, session)
    created = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=approval_payload(evidence["id"]),
    )
    assert created.status_code == 201, created.text
    action = created.json()
    action_id = action["id"]

    started = client.patch(
        f"/api/v1/registered-actions/{action_id}/status",
        headers=headers(session),
        json={"status": "in_progress", "expected_version": action["version"]},
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress"

    not_confirmed = client.patch(
        f"/api/v1/registered-actions/{action_id}/status",
        headers=headers(session),
        json={"status": "completed", "expected_version": started.json()["version"]},
    )
    assert not_confirmed.status_code == 422, not_confirmed.text

    completed = client.patch(
        f"/api/v1/registered-actions/{action_id}/status",
        headers=headers(session),
        json={
            "status": "completed",
            "expected_version": started.json()["version"],
            "explicit_completion_confirmation": True,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"]

    history = client.get(
        f"/api/v1/registered-actions/{action_id}/history",
        headers=headers(session),
    )
    assert history.status_code == 200, history.text
    events = history.json()
    assert [event["new_status"] for event in events] == ["approved", "in_progress", "completed"]
    assert all(event["actor_caregiver_id"] == session["caregiver"]["id"] for event in events)


def test_deferred_action_retains_history_and_stale_writes_are_rejected():
    client = TestClient(app)
    session = create_session(client, "Deferred recipient")
    evidence = create_evidence(client, session)
    created = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=approval_payload(evidence["id"]),
    )
    action = created.json()

    deferred = client.patch(
        f"/api/v1/registered-actions/{action['id']}/status",
        headers=headers(session),
        json={
            "status": "deferred",
            "expected_version": action["version"],
            "reason": "Caregiver chose to revisit this next week.",
            "deferred_until": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        },
    )
    assert deferred.status_code == 200, deferred.text
    assert deferred.json()["status"] == "deferred"
    assert deferred.json()["deferred_until"]

    stale = client.patch(
        f"/api/v1/registered-actions/{action['id']}/status",
        headers=headers(session),
        json={"status": "approved", "expected_version": action["version"]},
    )
    assert stale.status_code == 409, stale.text

    history = client.get(
        f"/api/v1/registered-actions/{action['id']}/history",
        headers=headers(session),
    )
    assert [event["new_status"] for event in history.json()] == ["approved", "deferred"]


def test_reminder_is_created_only_when_explicitly_requested_and_linked_once():
    client = TestClient(app)
    session = create_session(client, "Reminder-linked recipient")
    evidence = create_evidence(client, session)
    due_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    payload = approval_payload(
        evidence["id"],
        idempotency_key="care-action-reminder-key-002",
        create_reminder=True,
        reminder_due_at=due_at,
    )
    created = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=payload,
    )
    assert created.status_code == 201, created.text

    replay = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=payload,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == created.json()["id"]

    reminders = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers=headers(session),
    )
    assert reminders.status_code == 200, reminders.text
    assert len(reminders.json()) == 1
    assert reminders.json()[0]["action_id"] == created.json()["id"]

    from app.models.reminder import CareReminder
    db = TestSessionLocal()
    try:
        assert db.query(CareReminder).filter_by(action_id=created.json()["id"]).count() == 1
    finally:
        db.close()
