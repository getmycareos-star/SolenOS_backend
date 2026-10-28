from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.anonymous_session import AnonymousSession

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


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS care_facts (
                id VARCHAR(36) PRIMARY KEY,
                person_id VARCHAR(255) NOT NULL,
                fact_type VARCHAR(100),
                fact_value TEXT,
                epistemic_status VARCHAR(40),
                effective_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    with TestClient(app) as test_client:
        first = test_client.post("/api/v1/sessions/anonymous", json={
            "name": "Care recipient", "caregiver_name": "Family caregiver"
        })
        second = test_client.post("/api/v1/sessions/anonymous", json={
            "name": "Other recipient", "caregiver_name": "Other caregiver"
        })
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        yield test_client, first.json(), second.json()
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS care_facts"))
    Base.metadata.drop_all(bind=engine)


def test_current_care_projection_is_read_only_and_person_scoped(client):
    test_client, owner, other = client
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES
              ('owner-fact', :owner_id, 'sleep_pattern', 'Caregiver-reported sleep changes', 'reported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
              ('other-fact', :other_id, 'private_detail', 'Other person's private care detail', 'confirmed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"owner_id": owner["person"]["id"], "other_id": other["person"]["id"]})

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get("/api/v1/care-continuity/current-care", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["projection_type"] == "bounded_evidence_derived_current_care"
    assert any(item["id"] == "owner-fact" for item in body["items"])
    assert all(item["id"] != "other-fact" for item in body["items"])
    assert "Other person's private care detail" not in response.text
    # SQLite fixture intentionally omits the full migration schema; the API must
    # report incomplete coverage rather than pretending this is the full LCR.
    assert body["schema_status"] != "complete"
    assert body["missing_required_sources"]


def test_current_care_projection_requires_session(client):
    test_client, _, _ = client
    response = test_client.get("/api/v1/care-continuity/current-care")
    assert response.status_code == 401, response.text


def test_appointment_workspace_requires_session(client):
    test_client, _, _ = client
    assert test_client.get("/api/v1/appointment-workspaces").status_code == 401
    assert test_client.post("/api/v1/appointment-workspaces", json={"payload": {}}).status_code == 401


def test_appointment_workspace_persists_versions_and_revision_history(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    payload = {
        "visit_purpose": "Discuss changes in daily routine",
        "top_concerns": ["Sleep change"],
        "caregiver_report": "Family-reported; not a clinician finding",
        "clinician_findings": "",
        "unanswered_questions": ["What follow-up is needed?"],
        "follow_ups": [{"action": "Call clinic for result", "owner": "Family caregiver", "due": "2026-11-02", "status": "Open"}],
    }
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "preparation", "payload": payload, "change_reason": "Initial visit preparation"
    })
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["version_number"] == 1
    assert record["payload"]["caregiver_report"].startswith("Family-reported")
    record_id = record["id"]

    read = test_client.get(f"/api/v1/appointment-workspaces/{record_id}", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["payload"] == payload

    revised = {**payload, "clinician_findings": "Clinician statement recorded by caregiver; source needs confirmation"}
    updated = test_client.patch(f"/api/v1/appointment-workspaces/{record_id}", headers=headers, json={
        "expected_version": 1, "status": "follow_up_pending", "payload": revised,
        "change_reason": "Added visit notes after appointment"
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["version_number"] == 2

    conflict = test_client.patch(f"/api/v1/appointment-workspaces/{record_id}", headers=headers, json={
        "expected_version": 1, "payload": payload, "change_reason": "Stale update"
    })
    assert conflict.status_code == 409, conflict.text

    revisions = test_client.get(f"/api/v1/appointment-workspaces/{record_id}/revisions", headers=headers)
    assert revisions.status_code == 200, revisions.text
    items = revisions.json()["items"]
    assert [item["revision_number"] for item in items] == [1, 2]
    assert items[0]["payload"]["clinician_findings"] == ""
    assert items[1]["payload"]["clinician_findings"].startswith("Clinician statement")


def test_appointment_workspace_cannot_be_read_or_changed_across_people(client):
    test_client, owner, other = client
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=owner_headers, json={
        "payload": {"private_note": "must not leak"}
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    read = test_client.get(f"/api/v1/appointment-workspaces/{record_id}", headers=other_headers)
    assert read.status_code == 404
    update = test_client.patch(f"/api/v1/appointment-workspaces/{record_id}", headers=other_headers, json={
        "expected_version": 1, "payload": {"private_note": "overwrite"}, "change_reason": "Cross-person attempt"
    })
    assert update.status_code == 404
    listed = test_client.get("/api/v1/appointment-workspaces", headers=other_headers)
    assert listed.status_code == 200
    assert record_id not in str(listed.json())
    assert "must not leak" not in listed.text


def test_preparation_brief_is_read_only_source_linked_and_person_scoped(client):
    test_client, session, other = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES (:id, :person_id, :type, :value, :status, :created, :updated)
        """), [
            {"id": "appointment-fact-owned", "person_id": person_id, "type": "caregiver_observation",
             "value": "Family reports sleep has changed", "status": "reported",
             "created": "2026-10-26 08:00:00", "updated": "2026-10-26 08:00:00"},
            {"id": "appointment-fact-private", "person_id": other["person"]["id"], "type": "private",
             "value": "DO NOT LEAK THIS", "status": "reported",
             "created": "2026-10-26 08:00:00", "updated": "2026-10-26 08:00:00"},
        ])
    response = test_client.get(
        "/api/v1/appointment-workspaces/preparation-brief?window_days=90",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["updates_lcr"] is False
    assert any(item["source_id"] == "appointment-fact-owned" for item in body["facts"])
    assert "DO NOT LEAK THIS" not in response.text
    assert body["source_coverage"]["community"] == "not_authorized"
    assert body["source_coverage"]["medications_and_allergies"] == "unavailable"
    assert "DO NOT LEAK THIS" not in response.text
