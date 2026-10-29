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
        connection.execute(text("DROP TABLE IF EXISTS open_questions"))
        connection.execute(text("DROP TABLE IF EXISTS care_facts"))
    Base.metadata.drop_all(bind=engine)


def test_current_care_projection_is_read_only_and_person_scoped(client):
    test_client, owner, other = client
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES
              ('owner-fact', :owner_id, 'sleep_pattern', 'Caregiver-reported sleep changes', 'reported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
              ('other-fact', :other_id, 'private_detail', 'Other person private care detail', 'confirmed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"owner_id": owner["person"]["id"], "other_id": other["person"]["id"]})

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get("/api/v1/care-continuity/current-care", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["projection_type"] == "bounded_evidence_derived_current_care"
    assert any(item["id"] == "owner-fact" for item in body["items"])
    assert all(item["id"] != "other-fact" for item in body["items"])
    assert "Other person private care detail" not in response.text
    # SQLite fixture intentionally omits the full migration schema; the API must
    # report incomplete coverage rather than pretending this is the full LCR.
    assert body["schema_status"] != "complete"
    assert body["missing_required_sources"]


def test_saved_daily_care_entry_appears_in_longitudinal_projection(client, monkeypatch):
    import app.api.v1 as api_v1

    test_client, owner, _ = client
    # Prove the record write and projection do not depend on a live optional model.
    def unavailable_model(*args, **kwargs):
        raise RuntimeError("optional model unavailable")

    monkeypatch.setattr(api_v1, "extract_review_candidates", unavailable_model)
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    saved = test_client.post("/api/v1/daily/log", headers=headers, json={
        "person_id": owner["person"]["id"],
        "caregiver_id": owner["caregiver"]["id"],
        "medications": [],
        "vitals": [],
        "scales": [],
        "notes": "Caregiver reported a disrupted sleep routine.",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["success"] is True
    assert saved.json()["qwen_extraction_status"] == "unavailable"

    projection = test_client.get("/api/v1/care-continuity/current-care", headers=headers)
    assert projection.status_code == 200, projection.text
    body = projection.json()
    daily_items = [item for item in body["items"] if item["kind"] == "Daily care entry"]
    assert daily_items, body
    assert any("disrupted sleep routine" in item["description"] for item in daily_items)
    assert all(item["source_collection"] == "events" for item in daily_items)

    # The same saved entry must never appear in another person's projection.
    other_headers = {"Authorization": f"Bearer {client[2]['access_token']}"}
    other_projection = test_client.get(
        "/api/v1/care-continuity/current-care", headers=other_headers
    )
    assert other_projection.status_code == 200, other_projection.text
    assert "disrupted sleep routine" not in other_projection.text
    assert all(
        "disrupted sleep routine" not in item.get("description", "")
        for item in other_projection.json()["items"]
    )

    # Explicitly requesting the owner's person ID with the other session must fail.
    forbidden = test_client.get(
        f"/api/v1/events/{owner['person']['id']}", headers=other_headers
    )
    assert forbidden.status_code == 403, forbidden.text


def test_daily_capture_receipt_survives_secondary_pipeline_failures(client, monkeypatch):
    import app.api.v1 as api_v1

    test_client, owner, _ = client

    def fail_secondary(*args, **kwargs):
        raise RuntimeError("secondary pipeline unavailable")

    monkeypatch.setattr(api_v1, "_log_and_learn", fail_secondary)
    monkeypatch.setattr(api_v1, "extract_review_candidates", fail_secondary)
    monkeypatch.setattr(api_v1, "persist_capture_analysis", fail_secondary)

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    note = "Caregiver recorded a new overnight waking observation."
    response = test_client.post("/api/v1/daily/log", headers=headers, json={
        "person_id": owner["person"]["id"],
        "caregiver_id": owner["caregiver"]["id"],
        "medications": [],
        "vitals": [],
        "scales": [],
        "notes": note,
    })

    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["success"] is True
    assert receipt["learning_pipeline_status"] == "unavailable"
    assert receipt["qwen_extraction_status"] == "unavailable"
    assert receipt["qwen_analysis_id"] is None

    projection = test_client.get(
        "/api/v1/care-continuity/current-care", headers=headers
    )
    assert projection.status_code == 200, projection.text
    assert any(
        note in item.get("description", "")
        for item in projection.json()["items"]
        if item["kind"] == "Daily care entry"
    ), projection.text


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


def test_situation_understanding_assessment_is_persisted_and_person_scoped(client):
    test_client, owner, other = client
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS care_delta_runs (
                id VARCHAR(36) PRIMARY KEY,
                person_id VARCHAR(255) NOT NULL,
                source VARCHAR(100) NOT NULL,
                event_date DATETIME NOT NULL,
                source_event_id VARCHAR(255),
                evidence_ids JSON NOT NULL,
                delta_json TEXT NOT NULL,
                model_name VARCHAR(120) NOT NULL,
                created_by_caregiver_id VARCHAR(255),
                created_at DATETIME NOT NULL
            )
        """))
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS open_questions (
                id VARCHAR(36) PRIMARY KEY,
                question TEXT NOT NULL,
                status VARCHAR(40) NOT NULL,
                priority INTEGER,
                evidence_ids TEXT,
                asked_at DATETIME,
                answered_at DATETIME,
                answer TEXT,
                closed_at DATETIME,
                closed_reason TEXT,
                created_by_caregiver_id VARCHAR(255)
            )
        """))
        connection.execute(text("""
            INSERT INTO open_questions
                (id, question, status, priority, asked_at, created_by_caregiver_id)
            VALUES
                ('open-question-1', 'Confirm whether the appointment date is Monday or Tuesday',
                 'open', 1, CURRENT_TIMESTAMP, :caregiver_id)
        """), {"caregiver_id": owner["caregiver"]["id"]})
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES ('understanding-fact', :person_id, 'mobility', 'Caregiver-reported mobility change',
                    'reported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"person_id": owner["person"]["id"]})
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    created = test_client.post("/api/v1/care-continuity/situation-understanding/reassess", headers=headers)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] in {"created", "unchanged"}
    assert body["assessment"]["person_id"] == owner["person"]["id"]
    assert body["assessment"]["assessment_method"] == "deterministic_composition_of_persisted_lcr_sources"
    assert body["assessment"]["confidence_assessment"]["overall"] == "not_calibrated"
    assert len(body["assessment"]["capabilities"]) == 20
    loops = body["assessment"]["open_loops"]
    assert loops["source_of_truth"] == "persisted_lcr_records"
    assert any(
        item["id"] == "open-question-1"
        and item["loop_type"] == "unanswered_question"
        and item["recorded_status"] == "open"
        and item["owner"] is None
        and item["due_at"] is None
        for item in loops["items"]
    ), loops
    assert body["assessment"]["capabilities"]["baseline_establishment"]["evidence_state"] in {
        "supported_by_persisted_records", "insufficient_evidence"
    }
    repeated = test_client.post("/api/v1/care-continuity/situation-understanding/reassess", headers=headers)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["status"] == "unchanged"
    assert repeated.json()["assessment_id"] == body["assessment_id"]
    assert any(
        item.get("id") == "understanding-fact"
        for item in body["assessment"]["current_care_reality"]["records"]
    )

    retrieved = test_client.get("/api/v1/care-continuity/situation-understanding", headers=headers)
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["assessment_id"] == body["assessment_id"]
    assert retrieved.json()["assessment"]["person_id"] == owner["person"]["id"]

    # A genuinely new persisted source record must change the next assessment
    # and be represented as a projection delta, not a clinical interpretation.
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES ('understanding-fact-2', :person_id, 'daily_function', 'Newly reported support requirement',
                    'reported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"person_id": owner["person"]["id"]})
    changed = test_client.post("/api/v1/care-continuity/situation-understanding/reassess", headers=headers)
    assert changed.status_code == 200, changed.text
    changed_body = changed.json()
    assert changed_body["status"] == "created"
    evolution = changed_body["assessment"]["evolution_since_previous_assessment"]
    assert evolution["comparison_available"] is True
    assert evolution["previous_assessment_id"] == body["assessment_id"]
    assert any(
        item["change_type"] == "newly_included" and item["record_id"] == "understanding-fact-2"
        for item in evolution["changes"]
    )
    history = test_client.get("/api/v1/care-continuity/situation-understanding/history", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()["count"] == 2
    assert history.json()["items"][0]["assessment_id"] == changed_body["assessment_id"]
    assert history.json()["items"][1]["assessment_id"] == body["assessment_id"]
    historical = test_client.get(
        f"/api/v1/care-continuity/situation-understanding/history/{body['assessment_id']}",
        headers=headers,
    )
    assert historical.status_code == 200, historical.text
    assert historical.json()["assessment_id"] == body["assessment_id"]

    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    cross_person_snapshot = test_client.get(
        f"/api/v1/care-continuity/situation-understanding/history/{body['assessment_id']}",
        headers=other_headers,
    )
    assert cross_person_snapshot.status_code == 404, cross_person_snapshot.text
    other_view = test_client.get("/api/v1/care-continuity/situation-understanding", headers=other_headers)
    assert other_view.status_code == 200, other_view.text
    assert other_view.json()["status"] == "not_assessed"
    assert "Caregiver-reported mobility change" not in other_view.text
