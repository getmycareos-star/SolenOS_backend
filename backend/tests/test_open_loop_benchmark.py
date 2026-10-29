"""Adversarial, API-level benchmark for the persisted Open Loop Engine.

Synthetic fixtures only. This measures deterministic workflow contracts, not clinical
outcomes or production deployment health. Each case exercises the real FastAPI routes
and isolated database used by the existing open-loop integration suite.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from test_open_loops import (
    create_loop,
    session,
    setup_function as setup_open_loop_db,
    teardown_function as teardown_open_loop_db,
)
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database():
    setup_open_loop_db()
    yield
    teardown_open_loop_db()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def authenticated(client):
    _, headers = session(client, "Open-loop benchmark recipient")
    return client, headers


def test_ol01_server_session_scope_prevents_cross_recipient_reads(authenticated):
    client, headers = authenticated
    _, other_headers = session(client, "Different recipient")
    loop = create_loop(client, headers)
    assert client.get(f"/api/v1/open-loops/{loop['id']}", headers=other_headers).status_code == 404
    assert client.get("/api/v1/open-loops", headers=other_headers).json()["items"] == []


def test_ol02_deadline_requires_timezone_and_provenance(authenticated):
    client, headers = authenticated
    payload = {"loop_type": "follow_up", "title": "Confirm date", "description": "Date unresolved",
               "deadline_at": "2026-11-01T09:00:00"}
    assert client.post("/api/v1/open-loops", headers=headers, json=payload).status_code == 422
    payload["deadline_at"] = "2026-11-01T09:00:00Z"
    assert client.post("/api/v1/open-loops", headers=headers, json=payload).status_code == 422


def test_ol03_overdue_filter_uses_persisted_deadline(authenticated):
    client, headers = authenticated
    now = datetime.now(timezone.utc)
    create_loop(client, headers, "follow_up", title="Future", deadline_at=(now + timedelta(days=1)).isoformat(),
                deadline_source="explicit_user")
    overdue = create_loop(client, headers, "follow_up", title="Overdue",
                          deadline_at=(now - timedelta(days=1)).isoformat(), deadline_source="explicit_user")
    result = client.get("/api/v1/open-loops?overdue=true&limit=1", headers=headers)
    assert result.status_code == 200
    assert result.json()["count"] == 1
    assert result.json()["items"][0]["id"] == overdue["id"]
    assert result.json()["items"][0]["overdue"] is True


def test_ol04_action_cannot_close_on_unverified_outcome(authenticated):
    client, headers = authenticated
    loop = create_loop(client, headers, "pending_action")
    response = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                           json={"text": "Done", "evidence": [{"kind": "note"}]})
    assert response.status_code == 409


def test_ol05_professional_response_requires_source_evidence(authenticated):
    client, headers = authenticated
    loop = create_loop(client, headers, "professional_input")
    response = client.post(f"/api/v1/open-loops/{loop['id']}/responses", headers=headers,
                           json={"text": "Professional replied", "evidence": []})
    assert response.status_code == 422


def test_ol06_dependency_cycle_is_rejected_and_blocking_is_enforced(authenticated):
    client, headers = authenticated
    prerequisite = create_loop(client, headers, "pending_action", title="Prerequisite")
    dependent = create_loop(client, headers, "pending_decision", title="Dependent")
    edge = client.post(f"/api/v1/open-loops/{dependent['id']}/dependencies", headers=headers,
                       json={"prerequisite_id": prerequisite["id"], "mandatory": True})
    assert edge.status_code == 200 and edge.json()["blocked"] is True
    cycle = client.post(f"/api/v1/open-loops/{prerequisite['id']}/dependencies", headers=headers,
                        json={"prerequisite_id": dependent["id"], "mandatory": True})
    assert cycle.status_code == 409


def test_ol07_recurrence_keeps_occurrences_without_duplicate_loop_records(authenticated):
    client, headers = authenticated
    first = create_loop(client, headers)
    second = client.post("/api/v1/open-loops", headers=headers, json={
        "loop_type": "unresolved_concern",
        "title": "Follow up on the reported change",
        "description": "Caregiver reported a change; cause remains unknown.",
        "evidence": [{"kind": "caregiver_report", "text": "Another report"}],
    })
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]
    assert second.json()["recurrence_detected"] is True
    occurrences = client.get(f"/api/v1/open-loops/{first['id']}/occurrences", headers=headers)
    assert occurrences.status_code == 200 and len(occurrences.json()) == 2


def test_ol08_reused_create_idempotency_key_does_not_duplicate_records(authenticated):
    client, headers = authenticated
    payload = {"loop_type": "pending_decision", "title": "Confirm plan",
               "description": "The plan is not confirmed.", "idempotency_key": "ol-bench-create-key"}
    first = client.post("/api/v1/open-loops", headers=headers, json=payload)
    retry = client.post("/api/v1/open-loops", headers=headers, json=payload)
    listed = client.get("/api/v1/open-loops", headers=headers)
    assert first.status_code == 201 and retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    assert len(listed.json()["items"]) == 1


def test_ol09_contradiction_cannot_close_without_reconciliation(authenticated):
    client, headers = authenticated
    loop = create_loop(client, headers, "contradiction", title="Conflicting reports")
    premature = client.post(f"/api/v1/open-loops/{loop['id']}/close", headers=headers,
                            json={"text": "Reviewed", "evidence": [{"kind": "closure_review"}]})
    assert premature.status_code == 409
    reconciled = client.post(f"/api/v1/open-loops/{loop['id']}/reconcile", headers=headers,
                             json={"text": "Reports refer to different dates; preserve both.",
                                   "evidence": [{"kind": "source_comparison"}]})
    assert reconciled.status_code == 200


def test_ol10_reassessment_is_persistently_audited(authenticated):
    client, headers = authenticated
    loop = create_loop(client, headers)
    result = client.post("/api/v1/open-loops/reassess", headers=headers)
    assert result.status_code == 200
    assert result.json()["scanned"] >= 1
    history = client.get(f"/api/v1/open-loops/{loop['id']}/history", headers=headers)
    assert history.status_code == 200
    assert any(event["event_type"] == "reassessed" for event in history.json())
