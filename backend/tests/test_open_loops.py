from datetime import datetime, timedelta, timezone

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


def session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def create_loop(client, headers, loop_type="unresolved_concern", **overrides):
    payload = {
        "loop_type": loop_type,
        "title": "Follow up on the reported change",
        "description": "Caregiver reported a change; cause remains unknown.",
        "evidence": [{"kind": "caregiver_report", "text": "Caregiver report"}],
        **overrides,
    }
    response = client.post("/api/v1/open-loops", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_open_loop_persists_and_lists_only_within_server_issued_session():
    client = TestClient(app)
    first, first_headers = session(client, "First care recipient")
    second, second_headers = session(client, "Second care recipient")
    created = create_loop(client, first_headers)
    assert created["person_id"] == first["person"]["id"]
    assert created["caregiver_id"] == first["caregiver"]["id"]
    assert created["status"] == "open"

    listed = client.get("/api/v1/open-loops", headers=first_headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [created["id"]]

    isolated = client.get("/api/v1/open-loops", headers=second_headers)
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["items"] == []
    denied = client.get(f"/api/v1/open-loops/{created['id']}", headers=second_headers)
    assert denied.status_code == 404, denied.text


def test_deadline_requires_timezone_and_source():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    no_timezone = create_payload = {
        "loop_type": "follow_up",
        "title": "Follow up",
        "description": "A recorded follow-up",
        "deadline_at": "2026-11-01T09:00:00",
        "deadline_source": "explicit_user",
    }
    response = client.post("/api/v1/open-loops", json=no_timezone, headers=headers)
    assert response.status_code == 422, response.text

    no_source = {
        "loop_type": "follow_up",
        "title": "Follow up",
        "description": "A recorded follow-up",
        "deadline_at": "2026-11-01T09:00:00Z",
    }
    response = client.post("/api/v1/open-loops", json=no_source, headers=headers)
    assert response.status_code == 422, response.text


def test_action_loop_cannot_close_until_outcome_is_verified():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "pending_action")
    closure = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Action was marked complete", "evidence": [{"kind": "action_note"}]},
    )
    assert closure.status_code == 409, closure.text

    reported = client.post(
        f"/api/v1/open-loops/{loop['id']}/outcomes",
        headers=headers,
        json={"text": "Caregiver reports intended outcome", "evidence": [{"kind": "caregiver_report"}]},
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["outcome_status"] == "outcome_reported"

    verified = client.post(
        f"/api/v1/open-loops/{loop['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "Outcome checked against the stated verification rule",
            "evidence": [{"kind": "supporting_record", "reference": "recorded-by-caregiver"}],
            "verification_rule": "The recorded intended outcome is corroborated by the linked source record.",
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["outcome_status"] == "outcome_verified"

    closure = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Closure reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert closure.status_code == 200, closure.text
    assert closure.json()["verification"] == "verified"
    assert closure.json()["loop"]["status"] == "closed"


def test_professional_input_requires_source_evidence():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    loop = create_loop(client, headers, "professional_input")
    response = client.post(
        f"/api/v1/open-loops/{loop['id']}/responses",
        headers=headers,
        json={"text": "A professional responded", "evidence": []},
    )
    assert response.status_code == 422, response.text


def test_dependency_cycle_is_rejected_and_prerequisite_blocks_dependent():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    prerequisite = create_loop(client, headers, "pending_action", title="Prerequisite")
    dependent = create_loop(client, headers, "pending_decision", title="Dependent")

    edge = client.post(
        f"/api/v1/open-loops/{dependent['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": prerequisite["id"], "mandatory": True},
    )
    assert edge.status_code == 200, edge.text
    assert edge.json()["blocked"] is True

    cycle = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": dependent["id"], "mandatory": True},
    )
    assert cycle.status_code == 409, cycle.text


def test_invalid_loop_type_is_rejected():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    response = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={"loop_type": "invented", "title": "No", "description": "Invalid type"},
    )
    assert response.status_code == 422, response.text


def test_recurring_identical_issue_preserves_separate_occurrences():
    client = TestClient(app)
    _, headers = session(client, "Care recipient")
    first = create_loop(client, headers, "unresolved_concern")
    second = client.post(
        "/api/v1/open-loops",
        headers=headers,
        json={
            "loop_type": "unresolved_concern",
            "title": "Follow up on the reported change",
            "description": "Caregiver reported a change; cause remains unknown.",
            "evidence": [{"kind": "caregiver_report", "text": "Second report"}],
        },
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["id"] == first["id"]
    assert body["recurrence_detected"] is True
    occurrences = client.get(
        f"/api/v1/open-loops/{first['id']}/occurrences", headers=headers
    )
    assert occurrences.status_code == 200, occurrences.text
    assert len(occurrences.json()) == 2

def test_reused_idempotency_key_cannot_commit_a_second_mutation():
    client = TestClient(app)
    _, headers = session(client, "Idempotency recipient")
    payload = {
        "loop_type": "pending_decision",
        "title": "Confirm the appointment plan",
        "description": "The appointment plan still needs confirmation.",
        "idempotency_key": "same-request-key",
    }
    first = client.post("/api/v1/open-loops", headers=headers, json=payload)
    assert first.status_code == 201, first.text

    retry = client.post("/api/v1/open-loops", headers=headers, json=payload)
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == first.json()["id"]

    listed = client.get("/api/v1/open-loops", headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1

def test_overdue_filter_is_applied_before_pagination_and_reports_total():
    client = TestClient(app)
    _, headers = session(client, "Deadline recipient")
    now = datetime.now(timezone.utc)
    create_loop(
        client, headers, "follow_up", title="Future follow-up",
        deadline_at=(now + timedelta(days=2)).isoformat(),
        deadline_source="explicit_user",
    )
    overdue_loop = create_loop(
        client, headers, "follow_up", title="Past follow-up",
        deadline_at=(now - timedelta(days=2)).isoformat(),
        deadline_source="explicit_user",
    )

    response = client.get(
        "/api/v1/open-loops?overdue=true&limit=1", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert [item["id"] for item in body["items"]] == [overdue_loop["id"]]
    assert body["items"][0]["overdue"] is True

def test_dependent_unblocks_only_after_verified_prerequisite_closure():
    client = TestClient(app)
    _, headers = session(client, "Dependency recipient")
    prerequisite = create_loop(client, headers, "pending_action", title="Complete prerequisite")
    dependent = create_loop(client, headers, "pending_decision", title="Continue dependent work")

    edge = client.post(
        f"/api/v1/open-loops/{dependent['id']}/dependencies",
        headers=headers,
        json={"prerequisite_id": prerequisite["id"], "mandatory": True},
    )
    assert edge.status_code == 200, edge.text
    assert edge.json()["blocked"] is True

    reported = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/outcomes",
        headers=headers,
        json={"text": "The prerequisite outcome was reported", "evidence": [{"kind": "caregiver_report"}]},
    )
    assert reported.status_code == 200, reported.text
    verified = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/verify-outcome",
        headers=headers,
        json={
            "text": "The prerequisite outcome was checked",
            "evidence": [{"kind": "supporting_record"}],
            "verification_rule": "The recorded outcome is corroborated by the source record.",
        },
    )
    assert verified.status_code == 200, verified.text
    closed = client.post(
        f"/api/v1/open-loops/{prerequisite['id']}/close",
        headers=headers,
        json={"text": "Prerequisite closure reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert closed.status_code == 200, closed.text

    refreshed = client.get(f"/api/v1/open-loops/{dependent['id']}", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["loop"]["status"] == "open"
    history = refreshed.json()["history"]
    assert any(item["event_type"] == "dependency_unblocked" for item in history)

def test_contradiction_closure_uses_persisted_reconciliation_not_magic_words():
    client = TestClient(app)
    _, headers = session(client, "Contradiction recipient")
    loop = create_loop(client, headers, "contradiction", title="Two conflicting reports")

    premature = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Reviewed", "evidence": [{"kind": "closure_review"}]},
    )
    assert premature.status_code == 409, premature.text

    reconciled = client.post(
        f"/api/v1/open-loops/{loop['id']}/reconcile",
        headers=headers,
        json={
            "text": "The two reports refer to different dates; neither is discarded.",
            "evidence": [{"kind": "source_comparison"}],
        },
    )
    assert reconciled.status_code == 200, reconciled.text

    closed = client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "The record was reviewed against the reconciliation.", "evidence": [{"kind": "closure_review"}]},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["loop"]["status"] == "closed"

