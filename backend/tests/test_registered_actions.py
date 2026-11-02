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
        "suggestion_id": "open-loop:clinic-follow-up-001",
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
        stored_action = db.query(Action).filter_by(person_id=session["person"]["id"]).one()
        assert stored_action.extraction_confidence is None
        assert stored_action.risk_tier == "not_assessed"
        assert stored_action.responsible_caregiver_id == session["caregiver"]["id"]
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

    from app.models.action import ActionLifecycleAudit
    db = TestSessionLocal()
    try:
        audit = db.query(ActionLifecycleAudit).filter_by(action_id=action_id).first()
        original_reason = audit.reason
        audit.reason = "Attempted audit mutation"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
        persisted = db.query(ActionLifecycleAudit).filter_by(id=audit.id).one()
        assert persisted.reason == original_reason
    finally:
        db.close()


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
    assert replay.json()["linked_reminder_status"] == "open"

    reminders = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers=headers(session),
    )
    assert reminders.status_code == 200, reminders.text
    assert len(reminders.json()) == 1
    assert reminders.json()[0]["action_id"] == created.json()["id"]
    action_list = client.get(
        f"/api/v1/registered-actions/{session['person']['id']}",
        headers=headers(session),
    )
    assert action_list.status_code == 200, action_list.text
    assert action_list.json()[0]["linked_reminder_status"] == "open"
    assert action_list.json()[0]["linked_reminder_due_at"]

    completed = client.patch(
        f"/api/v1/registered-actions/{created.json()['id']}/status",
        headers=headers(session),
        json={
            "status": "completed",
            "expected_version": created.json()["version"],
            "explicit_completion_confirmation": True,
        },
    )
    assert completed.status_code == 200, completed.text
    refreshed_reminders = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers=headers(session),
    )
    assert refreshed_reminders.status_code == 200, refreshed_reminders.text
    assert refreshed_reminders.json()[0]["status"] == "completed"
    assert refreshed_reminders.json()[0]["completed_by_caregiver_id"] == session["caregiver"]["id"]
    completed_actions = client.get(
        f"/api/v1/registered-actions/{session['person']['id']}",
        headers=headers(session),
    )
    assert completed_actions.json()[0]["linked_reminder_status"] == "completed"

    from app.models.reminder import CareReminder
    db = TestSessionLocal()
    try:
        assert db.query(CareReminder).filter_by(action_id=created.json()["id"]).count() == 1
    finally:
        db.close()



def test_same_evidence_and_action_cannot_be_registered_under_another_suggestion():
    client = TestClient(app)
    session = create_session(client, "Duplicate candidate recipient")
    evidence = create_evidence(client, session)
    first = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=approval_payload(evidence["id"]),
    )
    assert first.status_code == 201, first.text

    duplicate_payload = approval_payload(
        evidence["id"],
        idempotency_key="different-idempotency-key-002",
        suggestion_id="different-open-loop-candidate-002",
    )
    duplicate = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=duplicate_payload,
    )
    assert duplicate.status_code == 409, duplicate.text

    listed = client.get(
        f"/api/v1/registered-actions/{session['person']['id']}",
        headers=headers(session),
    )
    assert len(listed.json()) == 1


def test_session_with_unlinked_caregiver_cannot_approve():
    import hashlib
    from datetime import datetime, timedelta, timezone
    from app.models.anonymous_session import AnonymousSession

    client = TestClient(app)
    first = create_session(client, "Scoped action recipient")
    second = create_session(client, "Unlinked caregiver recipient")
    evidence = create_evidence(client, first)
    token = "forged-unlinked-caregiver-session-token"
    db = TestSessionLocal()
    try:
        db.add(AnonymousSession(
            person_id=first["person"]["id"],
            caregiver_id=second["caregiver"]["id"],
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        db.commit()
    finally:
        db.close()

    denied = client.post(
        f"/api/v1/registered-actions/{first['person']['id']}/approve",
        headers={"Authorization": f"Bearer {token}"},
        json=approval_payload(evidence["id"]),
    )
    assert denied.status_code == 403, denied.text



def test_deferring_action_reschedules_its_linked_reminder_and_requires_new_date():
    client = TestClient(app)
    session = create_session(client, "Deferred reminder recipient")
    evidence = create_evidence(client, session)
    due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    created = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=approval_payload(
            evidence["id"],
            idempotency_key="deferred-reminder-approval-key-003",
            suggestion_id="deferred-reminder-suggestion-003",
            create_reminder=True,
            reminder_due_at=due_at,
        ),
    )
    assert created.status_code == 201, created.text

    missing_new_date = client.patch(
        f"/api/v1/registered-actions/{created.json()['id']}/status",
        headers=headers(session),
        json={
            "status": "deferred",
            "expected_version": created.json()["version"],
            "reason": "Caregiver chose to postpone.",
        },
    )
    assert missing_new_date.status_code == 422, missing_new_date.text

    new_due_at = (datetime.now(timezone.utc) + timedelta(days=8)).isoformat()
    deferred = client.patch(
        f"/api/v1/registered-actions/{created.json()['id']}/status",
        headers=headers(session),
        json={
            "status": "deferred",
            "expected_version": created.json()["version"],
            "reason": "Caregiver chose to postpone.",
            "deferred_until": new_due_at,
        },
    )
    assert deferred.status_code == 200, deferred.text

    reminders = client.get(
        f"/api/v1/reminders/{session['person']['id']}",
        headers=headers(session),
    )
    assert reminders.status_code == 200, reminders.text
    assert len(reminders.json()) == 1
    assert reminders.json()[0]["status"] == "open"
    assert reminders.json()[0]["due_at"].startswith(new_due_at[:16])



def test_direct_medication_change_is_blocked_but_contacting_clinician_is_allowed():
    client = TestClient(app)
    session = create_session(client, "Medication safety recipient")
    evidence = create_evidence(client, session, "The caregiver wants to clarify a medication concern with the prescribing clinician.")

    blocked_payload = approval_payload(
        evidence["id"],
        idempotency_key="medication-change-block-key-004",
        suggestion_id="medication-change-block-suggestion-004",
        title="Increase medication dosage to 10 mg",
    )
    blocked = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=blocked_payload,
    )
    assert blocked.status_code == 422, blocked.text
    assert "Direct medication changes cannot be registered" in blocked.text

    conditional_payload = approval_payload(
        evidence["id"],
        idempotency_key="conditional-follow-up-key-006",
        suggestion_id="conditional-follow-up-suggestion-006",
        title="Call the clinic if symptoms worsen",
    )
    conditional = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=conditional_payload,
    )
    assert conditional.status_code == 422, conditional.text
    assert "Conditional follow-ups cannot be registered" in conditional.text

    allowed_payload = approval_payload(
        evidence["id"],
        idempotency_key="clinician-call-approval-key-005",
        suggestion_id="clinician-call-suggestion-005",
        title="Call the prescribing clinician to discuss the medication concern",
    )
    allowed = client.post(
        f"/api/v1/registered-actions/{session['person']['id']}/approve",
        headers=headers(session),
        json=allowed_payload,
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["status"] == "approved"

    listed = client.get(
        f"/api/v1/registered-actions/{session['person']['id']}",
        headers=headers(session),
    )
    assert len(listed.json()) == 1
