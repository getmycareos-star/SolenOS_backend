from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Evidence
from app.models.care_continuity import EvidenceFreshnessPolicy, EvidenceFreshnessAssessment
from app.models.uncertainty import Contradiction
from app.models.time_location import Appointment

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
    db = TestSessionLocal()
    try:
        caregiver_id = session["caregiver"]["id"]
        source_time = datetime.now(timezone.utc) - timedelta(days=45)
        evidence = Evidence(
            id="appointment-freshness-evidence",
            person_id=person_id,
            type="document",
            source_text="Dated source evidence linked to an appointment preparation event.",
            uploaded_by_caregiver_id=caregiver_id,
            occurred_at=source_time,
            collected_at=source_time,
        )
        policy = EvidenceFreshnessPolicy(
            id="appointment-freshness-policy",
            person_id=person_id,
            domain_key="document_history",
            policy_version="v1",
            max_age_days=30,
            historical_after_days=180,
            rationale="Explicit review boundary for appointment preparation.",
            created_by_caregiver_id=caregiver_id,
        )
        assessment = EvidenceFreshnessAssessment(
            id="appointment-freshness-assessment",
            person_id=person_id,
            evidence_id=evidence.id,
            policy_id=policy.id,
            policy_version="v1",
            freshness_status="needs_review",
            source_time=source_time,
            source_time_kind="event_occurrence",
            assessed_at=datetime.now(timezone.utc),
            age_days=45,
            rationale="The explicit review boundary has elapsed; re-confirm before relying on currentness.",
            assessed_by_caregiver_id=caregiver_id,
        )
        event = CareEvent(
            id="appointment-freshness-event",
            person_id=person_id,
            event_type="care_observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=2),
            title="Dated source event",
            description="Source event linked to the dated document.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([evidence, policy, assessment, event])
        db.commit()
    finally:
        db.close()

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
    assert body["source_coverage"]["evidence_freshness_assessments"] == "available"
    assert any(item["assessment_id"] == "appointment-freshness-assessment" for item in body["freshness_assessments"])
    event_item = next(item for item in body["events"] if item["source_id"] == "appointment-freshness-event")
    assert event_item["freshness_assessments"][0]["freshness_status"] == "needs_review"
    assert event_item["freshness_assessments"][0]["interpretation_limit"].startswith("This is a policy application")
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
                person_id VARCHAR(255) NOT NULL,
                question TEXT NOT NULL,
                subject_type VARCHAR(255) NOT NULL,
                subject_id VARCHAR(255) NOT NULL,
                claim_id VARCHAR(255),
                intended_answerer_role VARCHAR(40),
                intended_answerer_label TEXT,
                necessity VARCHAR(20) NOT NULL DEFAULT 'not_assessed',
                necessity_rationale TEXT,
                status VARCHAR(40) NOT NULL,
                priority INTEGER,
                evidence_ids TEXT,
                context TEXT,
                asked_at DATETIME,
                answered_at DATETIME,
                answer TEXT,
                answer_provenance TEXT,
                answered_by_caregiver_id VARCHAR(255),
                closed_at DATETIME,
                closed_reason TEXT,
                created_by_caregiver_id VARCHAR(255),
                updated_at DATETIME
            )
        """))
        connection.execute(text("""
            INSERT INTO open_questions
                (id, person_id, subject_type, subject_id, question, necessity, status, priority, asked_at, created_by_caregiver_id)
            VALUES
                ('open-question-1', :person_id, 'appointment', 'appointment-date',
                 'Confirm whether the appointment date is Monday or Tuesday',
                 'not_assessed', 'open', 1, CURRENT_TIMESTAMP, :caregiver_id),
                ('open-question-closed', :person_id, 'appointment', 'appointment-date-closed',
                 'Question already answered', 'not_assessed', 'answered', 2, CURRENT_TIMESTAMP, :caregiver_id),
                ('open-question-other-person', :other_person_id, 'appointment', 'private-appointment',
                 'Private question for another person', 'not_assessed', 'open', 1, CURRENT_TIMESTAMP, :caregiver_id)
        """), {
            "person_id": owner["person"]["id"],
            "other_person_id": other["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })
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
    quality = body["assessment"]["assessment_quality"]
    assert quality["numeric_confidence"].startswith("not_provided")
    assert quality["status"] == "limitations_present"
    assert quality["high_impact_conclusion_gate"] == "human_review_required"
    assert quality["assessment_use_status"] == "restricted_by_recorded_limitations"
    dimensions = {item["dimension"]: item for item in quality["dimensions"]}
    assert dimensions["individual_baseline"]["status"] == "not_established"
    assert "individual_baseline_not_established" in quality["blocking_limitations"]
    assert "No-records is not evidence of absence" in quality["interpretation_policy"]
    freshness = body["assessment"]["freshness_assessment"]
    assert freshness["policy"].startswith("Report recency metadata")
    assert freshness["sources"]["facts"]["staleness_assessment"] == "not_assessed_without_domain_specific_threshold"
    assert "not that the information remains current" in freshness["sources"]["facts"]["interpretation_limit"]
    assert len(body["assessment"]["capabilities"]) == 23
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
    loop_ids = {item["id"] for item in loops["items"]}
    assert "open-question-closed" not in loop_ids
    assert "open-question-other-person" not in loop_ids
    assert "Private question for another person" not in str(body["assessment"])
    trajectory = body["assessment"]["situation_trajectory"]
    assert trajectory["source_of_truth"] == "persisted_lcr_records"
    assert trajectory["causal_inference"] == "not_performed"
    fact_entry = next(
        item for item in trajectory["entries"]
        if item["source"] == "facts" and item["record_id"] == "understanding-fact"
    )
    assert fact_entry["epistemic_status"] == "reported"
    assert fact_entry["recorded_at"] is not None
    assert fact_entry["interpretation_limit"].endswith("does not establish causation.")
    assert "understanding-fact" in {item["id"] for item in body["assessment"]["current_care_reality"]["records"]}
    trajectory = body["assessment"]["situation_trajectory"]
    assert trajectory["source_of_truth"] == "persisted_lcr_records"
    assert trajectory["causal_inference"] == "not_performed"
    assert any(entry["record_id"] == "understanding-fact" for entry in trajectory["entries"])
    assert all("interpretation_limit" in entry for entry in trajectory["entries"])
    assert body["assessment"]["baseline_assessment"]["status"] == "not_established"
    assert body["assessment"]["baseline_assessment"]["count"] == 0
    assert body["assessment"]["capabilities"]["baseline_establishment"]["evidence_state"] == "insufficient_evidence"
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
    assert retrieved.json()["source_freshness"]["status"] == "current_for_bounded_source_view"
    assert retrieved.json()["source_freshness"]["reassessment_recommended"] is False

    # A genuinely new persisted source record must change the next assessment
    # and be represented as a projection delta, not a clinical interpretation.
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES ('understanding-fact-2', :person_id, 'daily_function', 'Newly reported support requirement',
                    'reported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """), {"person_id": owner["person"]["id"]})
    check = test_client.get("/api/v1/care-continuity/situation-understanding", headers=headers)
    assert check.status_code == 200, check.text
    assert check.json()["source_freshness"]["status"] == "source_changed_since_assessment"
    assert check.json()["source_freshness"]["reassessment_recommended"] is True
    changed = test_client.post("/api/v1/care-continuity/situation-understanding/reassess", headers=headers)
    assert changed.status_code == 200, changed.text
    changed_body = changed.json()
    assert changed_body["status"] == "created"
    assert changed_body["source_freshness"]["status"] == "current_for_bounded_source_view"
    assert changed_body["source_freshness"]["reassessment_recommended"] is False
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

def test_state_revision_as_of_reconstructs_historical_state_and_isolates_people(client):
    import json
    from app.models.care_continuity import CareStateRevision

    test_client, owner, other = client
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    db = TestSessionLocal()
    try:
        db.add_all([
            CareStateRevision(
                id="history-revision-1",
                person_id=owner["person"]["id"],
                target_type="care_events",
                target_id="history-event-1",
                revision_number=1,
                change_kind="created",
                before_state=None,
                after_state=json.dumps({"status": "reported", "description": "Caregiver reports poor sleep"}),
                evidence_ids=["history-evidence-1"],
                changed_by_caregiver_id=owner["caregiver"]["id"],
                changed_at=datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc),
            ),
            CareStateRevision(
                id="history-revision-2",
                person_id=owner["person"]["id"],
                target_type="care_events",
                target_id="history-event-1",
                revision_number=2,
                change_kind="updated",
                before_state=json.dumps({"status": "reported", "description": "Caregiver reports poor sleep"}),
                after_state=json.dumps({"status": "reviewed", "description": "Source passage reviewed; cause remains unknown"}),
                evidence_ids=["history-evidence-1", "history-evidence-2"],
                changed_by_caregiver_id=owner["caregiver"]["id"],
                changed_at=datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc),
            ),
            # A late-arriving report concerns an earlier event time but was not
            # recorded until the next day. "Known at the time" reconstruction must
            # not retroactively include it in the October 29 snapshot.
            CareStateRevision(
                id="history-revision-3-late-arrival",
                person_id=owner["person"]["id"],
                target_type="care_events",
                target_id="history-event-1",
                revision_number=3,
                change_kind="updated",
                before_state=json.dumps({"status": "reviewed", "description": "Source passage reviewed; cause remains unknown"}),
                after_state=json.dumps({
                    "status": "reviewed",
                    "description": "Source passage reviewed; cause remains unknown",
                    "late_arrival": {
                        "event_time": "2026-10-20T08:00:00Z",
                        "first_recorded_at": "2026-10-30T10:00:00Z",
                        "interpretation": "Arrived later; not part of what was recorded on October 29.",
                    },
                }),
                evidence_ids=["history-evidence-1", "history-evidence-2"],
                changed_by_caregiver_id=owner["caregiver"]["id"],
                changed_at=datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc),
            ),
        ])
        db.commit()
    finally:
        db.close()

    url = "/api/v1/care-continuity/state-revisions/care_events/history-event-1/as-of"
    earlier = test_client.get(url, headers=owner_headers, params={"as_of": "2026-10-28T23:59:00Z"})
    assert earlier.status_code == 200, earlier.text
    assert earlier.json()["revision_number"] == 1
    assert earlier.json()["state"]["status"] == "reported"
    assert earlier.json()["evidence_ids"] == ["history-evidence-1"]

    later = test_client.get(url, headers=owner_headers, params={"as_of": "2026-10-29T12:00:00Z"})
    assert later.status_code == 200, later.text
    assert later.json()["revision_number"] == 2
    assert later.json()["state"]["status"] == "reviewed"
    assert "cause remains unknown" in later.json()["state"]["description"]
    assert later.json()["historical_state_source"] == "persisted_care_state_revision"
    assert "does not establish facts" in later.json()["interpretation_limit"]

    late_arrival = test_client.get(
        url, headers=owner_headers, params={"as_of": "2026-10-30T12:00:00Z"}
    )
    assert late_arrival.status_code == 200, late_arrival.text
    assert late_arrival.json()["revision_number"] == 3
    assert late_arrival.json()["state"]["late_arrival"]["event_time"] == "2026-10-20T08:00:00Z"
    assert late_arrival.json()["state"]["late_arrival"]["first_recorded_at"] == "2026-10-30T10:00:00Z"
    assert "not part of what was recorded on October 29" in late_arrival.json()["state"]["late_arrival"]["interpretation"]

    cross_person = test_client.get(url, headers=other_headers, params={"as_of": "2026-10-29T12:00:00Z"})
    assert cross_person.status_code == 404, cross_person.text


def test_observation_coverage_api_persists_and_isolates_person_records(client):
    test_client, owner, other = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    window_end = datetime(2026, 10, 29, tzinfo=timezone.utc)
    window_start = window_end - timedelta(days=14)
    payload = {
        "subject_type": "mobility",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "coverage_status": "covered",
        "source_evidence_ids": [],
        "rationale": "Caregiver explicitly recorded that mobility was observed throughout this interval.",
    }

    created = test_client.post("/api/v1/care-continuity/observation-coverage", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["person_id"] == owner["person"]["id"]
    assert created.json()["coverage_status"] == "covered"

    situation = test_client.post("/api/v1/care-continuity/situation-understanding/reassess", headers=headers)
    assert situation.status_code == 200, situation.text
    coverage_projection = situation.json()["assessment"]["observation_coverage"]
    assert coverage_projection["source_of_truth"] == "persisted_person_scoped_observation_coverage"
    assert any(item["id"] == created.json()["id"] for item in coverage_projection["items"])
    assert "not no events" in coverage_projection["absence_rule"]

    own_records = test_client.get("/api/v1/care-continuity/observation-coverage?subject_type=mobility", headers=headers)
    other_records = test_client.get("/api/v1/care-continuity/observation-coverage?subject_type=mobility", headers=other_headers)
    assert own_records.status_code == 200, own_records.text
    assert other_records.status_code == 200, other_records.text
    assert any(item["id"] == created.json()["id"] for item in own_records.json()["items"])
    assert other_records.json()["items"] == []

    assessment = test_client.get(
        "/api/v1/care-continuity/observation-coverage/assessment",
        headers=headers,
        params={
            "subject_type": "mobility",
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
    )
    assert assessment.status_code == 200, assessment.text
    assert assessment.json()["coverage_status"] == "covered"
    assert assessment.json()["coverage_fraction"] == 1.0
    assert assessment.json()["source_of_truth"] == "persisted_person_scoped_observation_coverage"

    # A client cannot redirect a coverage record into another person's LCR.
    forged = test_client.post(
        "/api/v1/care-continuity/observation-coverage",
        headers=headers,
        json={**payload, "person_id": other["person"]["id"]},
    )
    assert forged.status_code == 422

def test_structured_source_observation_appears_in_current_care_projection(client):
    test_client, owner, other = client
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO evidence (
                id, person_id, type, source_text, uploaded_by_caregiver_id
            ) VALUES (
                'sleep-source-evidence', :person_id, 'caregiver_note',
                'Caregiver recorded: woke twice overnight.', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })
        connection.execute(text("""
            INSERT INTO evidence_observations (
                id, person_id, evidence_id, observation_kind, epistemic_status,
                subject_label, attribute, value_text, source_passage,
                source_span_start, source_span_end, context, observer_label,
                observation_method, negated, hedge_marker, ambiguity_note,
                extraction_method, extraction_version, created_by_caregiver_id
            ) VALUES (
                'sleep-structured-observation', :person_id, 'sleep-source-evidence',
                'reported_observation', 'caregiver_reported', 'care recipient',
                'sleep', 'woke twice overnight', 'woke twice overnight',
                20, 40, 'After the usual evening routine', 'family caregiver',
                'caregiver report', 0, 'reported as', 'Count not independently verified',
                'test_fixture_manual_annotation', 'test-fixture-v1', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })
        connection.execute(text("""
            INSERT INTO evidence (
                id, person_id, type, source_text, uploaded_by_caregiver_id
            ) VALUES (
                'private-evidence-other', :other_person_id, 'caregiver_note',
                'Private information belonging to another care recipient.', :other_caregiver_id
            )
        """), {
            "other_person_id": other["person"]["id"],
            "other_caregiver_id": other["caregiver"]["id"],
        })
        connection.execute(text("""
            INSERT INTO care_events (
                id, person_id, event_type, status, occurred_at, title,
                evidence_ids, created_by_caregiver_id
            ) VALUES (
                'foreign-evidence-link-event', :person_id, 'structured_daily_capture', 'recorded',
                CURRENT_TIMESTAMP, 'Record with unauthorized legacy evidence link',
                '["private-evidence-other"]', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })
        connection.execute(text("""
            INSERT INTO care_events (
                id, person_id, event_type, status, occurred_at, title,
                evidence_ids, created_by_caregiver_id
            ) VALUES (
                'sleep-linked-event', :person_id, 'sleep', 'recorded',
                CURRENT_TIMESTAMP, 'Recorded sleep event',
                '["sleep-source-evidence"]', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get("/api/v1/care-continuity/current-care", headers=headers)
    assert response.status_code == 200, response.text
    item = next(
        row for row in response.json()["items"]
        if row["id"] == "sleep-structured-observation"
    )
    assert item["source_collection"] == "evidence_observations"
    assert item["title"] == "sleep"
    assert item["evidence_ids"] == ["sleep-source-evidence"]
    foreign_link_item = next(
        row for row in response.json()["items"]
        if row["id"] == "foreign-evidence-link-event"
    )
    assert foreign_link_item["evidence_ids"] == []
    assert "private-evidence-other" not in response.text
    assert item["source_span_start"] == 20
    assert item["source_span_end"] == 40
    assert item["source_passage"] == "woke twice overnight"
    assert item["epistemic_status"] == "caregiver_reported"

    situation = test_client.post(
        "/api/v1/care-continuity/situation-understanding/reassess", headers=headers
    )
    assert situation.status_code == 200, situation.text
    assessment = situation.json()["assessment"]
    projected = [
        row for row in assessment["current_care_reality"]["records"]
        if row.get("id") == "sleep-structured-observation"
    ]
    assert len(projected) == 1
    assert projected[0]["source"] == "evidence_observations"
    assert projected[0]["evidence_ids"] == ["sleep-source-evidence"]
    assert any(
        row["record_id"] == "sleep-structured-observation"
        and row["source"] == "evidence_observations"
        for row in assessment["situation_trajectory"]["entries"]
    )
    trajectory_observation = next(
        row for row in assessment["situation_trajectory"]["entries"]
        if row["record_id"] == "sleep-structured-observation"
    )
    assert trajectory_observation["context"] == "After the usual evening routine"
    assert trajectory_observation["observer_label"] == "family caregiver"
    assert trajectory_observation["observation_method"] == "caregiver report"
    assert trajectory_observation["negated"] is False
    assert trajectory_observation["hedge_marker"] == "reported as"
    assert trajectory_observation["ambiguity_note"] == "Count not independently verified"
    shared_source_group = next(
        group for group in assessment["cross_domain_source_links"]["groups"]
        if group["evidence_id"] == "sleep-source-evidence"
    )
    assert set(shared_source_group["domains"]) == {
        "current_state_reconstruction", "evidence_grounded_situation_understanding"
    }
    assert {item["source"] for item in shared_source_group["records"]} == {
        "events", "evidence_observations"
    }
    assert "does not establish independent corroboration" in shared_source_group["interpretation_limit"]
    foreign_linked_event = next(
        row for row in assessment["current_care_reality"]["records"]
        if row.get("id") == "foreign-evidence-link-event"
    )
    assert foreign_linked_event["evidence_ids"] == []
    assert "private-evidence-other" not in str(assessment)

    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    other_response = test_client.get("/api/v1/care-continuity/current-care", headers=other_headers)
    assert other_response.status_code == 200, other_response.text
    assert "sleep-structured-observation" not in other_response.text
    assert "woke twice overnight" not in other_response.text

def test_observation_capture_validates_exact_source_span_and_person_scope(client):
    test_client, owner, other = client
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO evidence (
                id, person_id, type, source_text, uploaded_by_caregiver_id
            ) VALUES
                ('capture-source-owner', :owner_id, 'caregiver_note',
                 'Caregiver recorded: woke twice overnight.', :owner_caregiver_id),
                ('capture-source-other', :other_id, 'caregiver_note',
                 'Private report: missed breakfast.', :other_caregiver_id)
        """), {
            "owner_id": owner["person"]["id"],
            "owner_caregiver_id": owner["caregiver"]["id"],
            "other_id": other["person"]["id"],
            "other_caregiver_id": other["caregiver"]["id"],
        })

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    payload = {
        "observation_kind": "caregiver_report",
        "epistemic_status": "reported",
        "subject_label": "care recipient",
        "attribute": "sleep",
        "value_text": "woke twice overnight",
        "source_passage": "woke twice overnight",
        "source_span_start": 20,
        "source_span_end": 40,
        "extraction_method": "test_fixture_manual_annotation",
        "extraction_version": "test-fixture-v1",
    }
    created = test_client.post(
        "/api/v1/care-continuity/evidence/capture-source-owner/observations",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["evidence_id"] == "capture-source-owner"
    assert body["source_passage"] == payload["source_passage"]
    assert body["source_span_start"] == 20 and body["source_span_end"] == 40
    assert body["epistemic_status"] == "reported"

    wrong_span = test_client.post(
        "/api/v1/care-continuity/evidence/capture-source-owner/observations",
        headers=headers,
        json={**payload, "source_span_start": 19},
    )
    assert wrong_span.status_code == 422, wrong_span.text

    cross_person = test_client.post(
        "/api/v1/care-continuity/evidence/capture-source-other/observations",
        headers=headers,
        json={**payload, "source_passage": "missed breakfast", "source_span_start": 23, "source_span_end": 39},
    )
    assert cross_person.status_code == 404, cross_person.text

    with engine.begin() as connection:
        persisted = connection.execute(text("""
            SELECT id, person_id, evidence_id, source_span_start, source_span_end
            FROM evidence_observations WHERE id = :id
        """), {"id": body["id"]}).mappings().one()
    assert persisted["person_id"] == owner["person"]["id"]
    assert persisted["evidence_id"] == "capture-source-owner"
    assert persisted["source_span_start"] == 20
    assert persisted["source_span_end"] == 40


def test_current_care_hides_superseded_observation_but_retains_history(client):
    test_client, owner, other = client
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO evidence (
                id, person_id, type, source_text, uploaded_by_caregiver_id
            ) VALUES (
                'correction-source-evidence', :person_id, 'caregiver_note',
                'Caregiver recorded: woke twice overnight.', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })
        connection.execute(text("""
            INSERT INTO evidence_observations (
                id, person_id, evidence_id, supersedes_observation_id, correction_rationale,
                observation_kind, epistemic_status, subject_label, attribute, value_text,
                source_passage, source_span_start, source_span_end, extraction_method,
                extraction_version, created_by_caregiver_id
            ) VALUES (
                'sleep-observation-v1', :person_id, 'correction-source-evidence', NULL, NULL,
                'reported_observation', 'caregiver_reported', 'care recipient', 'sleep',
                'woke twice', 'woke twice overnight', 20, 40,
                'test_fixture_manual_annotation', 'test-fixture-v1', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })
        connection.execute(text("""
            INSERT INTO evidence_observations (
                id, person_id, evidence_id, supersedes_observation_id, correction_rationale,
                observation_kind, epistemic_status, subject_label, attribute, value_text,
                source_passage, source_span_start, source_span_end, extraction_method,
                extraction_version, created_by_caregiver_id
            ) VALUES (
                'sleep-observation-v2', :person_id, 'correction-source-evidence',
                'sleep-observation-v1', 'Preserved the complete wording of the source passage.',
                'reported_observation', 'caregiver_reported', 'care recipient', 'sleep',
                'woke twice overnight', 'woke twice overnight', 20, 40,
                'test_fixture_manual_annotation', 'test-fixture-v2', :caregiver_id
            )
        """), {
            "person_id": owner["person"]["id"],
            "caregiver_id": owner["caregiver"]["id"],
        })

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    branching_correction = test_client.post(
        "/api/v1/care-continuity/evidence/correction-source-evidence/observations",
        headers=headers,
        json={
            "observation_kind": "caregiver_report",
            "epistemic_status": "reported",
            "subject_label": "care recipient",
            "attribute": "sleep",
            "value_text": "woke twice",
            "source_passage": "woke twice overnight",
            "source_span_start": 20,
            "source_span_end": 40,
            "extraction_method": "human_entered",
            "extraction_version": "manual-v1",
            "supersedes_observation_id": "sleep-observation-v1",
            "correction_rationale": "Attempted branch from an already corrected observation.",
        },
    )
    assert branching_correction.status_code == 409, branching_correction.text

    projection = test_client.get("/api/v1/care-continuity/current-care", headers=headers)
    assert projection.status_code == 200, projection.text
    current_observations = [
        item for item in projection.json()["items"]
        if item["source_collection"] == "evidence_observations"
    ]
    assert [item["id"] for item in current_observations] == ["sleep-observation-v2"]
    assert current_observations[0]["supersedes_observation_id"] == "sleep-observation-v1"
    assert current_observations[0]["correction_rationale"] == "Preserved the complete wording of the source passage."

    history = test_client.get(
        "/api/v1/care-continuity/evidence/correction-source-evidence/observations",
        headers=headers,
    )
    assert history.status_code == 200, history.text
    history_items = {item["id"]: item for item in history.json()}
    assert set(history_items) == {"sleep-observation-v1", "sleep-observation-v2"}
    assert history_items["sleep-observation-v2"]["supersedes_observation_id"] == "sleep-observation-v1"

    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    private_history = test_client.get(
        "/api/v1/care-continuity/evidence/correction-source-evidence/observations",
        headers=other_headers,
    )
    assert private_history.status_code == 404, private_history.text



def test_appointment_evidence_reference_parser_preserves_only_explicit_ids():
    from app.api.appointment_workspace import _evidence_ids_from_row

    assert _evidence_ids_from_row({"evidence_ids": '["evidence-a", "evidence-b"]'}) == [
        "evidence-a", "evidence-b"
    ]
    assert _evidence_ids_from_row({"source_evidence_id": "evidence-c"}) == ["evidence-c"]
    assert _evidence_ids_from_row({"evidence_ids": "not-json"}) == ["not-json"]
    assert _evidence_ids_from_row({"evidence_ids": None, "source_evidence_id": None}) == []



def test_person_historical_projection_reconstructs_latest_revision_per_target(client):
    from app.models.care_continuity import CareStateRevision

    test_client, owner, other = client
    as_of = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    with TestSessionLocal() as db:
        db.add_all([
            CareStateRevision(
                person_id=owner["person"]["id"], target_type="care_events", target_id="history-event",
                revision_number=1, change_kind="created", after_state='{"status":"reported"}',
                evidence_ids=[], changed_at=datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc),
            ),
            CareStateRevision(
                person_id=owner["person"]["id"], target_type="care_events", target_id="history-event",
                revision_number=2, change_kind="updated", before_state='{"status":"reported"}',
                after_state='{"status":"reviewed"}', evidence_ids=[],
                changed_at=datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc),
            ),
            CareStateRevision(
                person_id=other["person"]["id"], target_type="care_events", target_id="private-event",
                revision_number=1, change_kind="created", after_state='{"private":"not-visible"}',
                evidence_ids=[], changed_at=datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc),
            ),
        ])
        db.commit()

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get(
        "/api/v1/care-continuity/state-reconstruction/as-of",
        headers=headers,
        params={"as_of": as_of.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["coverage_status"] == "partial_revision_history"
    assert body["truncated"] is False
    assert len(body["items"]) == 1
    assert body["items"][0]["target_id"] == "history-event"
    assert body["items"][0]["revision_number"] == 2
    assert body["items"][0]["state"] == {"status": "reviewed"}
    assert "not a complete historical Living Care Record" in body["interpretation_limit"]
    assert "not-visible" not in response.text


def test_appointment_workspace_create_is_idempotent_and_rejects_key_reuse(client):
    test_client, session, _ = client
    headers = {"Authorization": "Bearer " + session["access_token"]}
    request_body = {
        "status": "outcome_pending",
        "idempotency_key": "appointment-outcome-save-001",
        "change_reason": "Save visit outcome",
        "payload": {
            "attendance_status": "attended",
            "caregiver_report": "Caregiver reports the appointment took place.",
            "professional_statements": [],
            "follow_up_actions": [],
        },
    }
    first = test_client.post("/api/v1/appointment-workspaces", headers=headers, json=request_body)
    replay = test_client.post("/api/v1/appointment-workspaces", headers=headers, json=request_body)
    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["version_number"] == 1
    changed = {**request_body, "payload": {**request_body["payload"], "caregiver_report": "Different report"}}
    conflict = test_client.post("/api/v1/appointment-workspaces", headers=headers, json=changed)
    assert conflict.status_code == 409, conflict.text
    listed = test_client.get("/api/v1/appointment-workspaces", headers=headers)
    matching = [item for item in listed.json()["items"] if item["id"] == first.json()["id"]]
    assert len(matching) == 1

def test_appointment_outcome_updates_the_linked_appointment_and_is_person_scoped(client):
    from app.models.time_location import Appointment
    test_client, owner, other = client
    db = TestSessionLocal()
    scheduled = datetime.now(timezone.utc) + timedelta(days=2)
    appointments = []
    for index, session in enumerate((owner, owner, owner, owner, other)):
        appointment = Appointment(
            person_id=session['person']['id'],
            title='Follow-up visit ' + str(index),
            scheduled_at=scheduled,
            provider='Care clinic',
            status='scheduled',
            created_by_caregiver_id=session['caregiver']['id'],
        )
        db.add(appointment)
        appointments.append(appointment)
    db.commit()
    ids = [item.id for item in appointments]
    db.close()
    headers = {'Authorization': 'Bearer ' + owner['access_token']}
    outcomes = ['attended', 'cancelled', 'rescheduled', 'missed']
    expected = ['completed', 'cancelled', 'rescheduled', 'missed']
    for index, (attendance, status) in enumerate(zip(outcomes, expected)):
        payload = {
            'appointment_id': ids[index],
            'status': 'outcome_pending',
            'idempotency_key': 'appointment-attendance-save-' + str(index),
            'payload': {
                'attendanceStatus': attendance,
                'actualVisitAt': datetime.now(timezone.utc).isoformat() if attendance == 'attended' else None,
                'rescheduledAt': (scheduled + timedelta(days=5)).isoformat() if attendance == 'rescheduled' else None,
            },
        }
        response = test_client.post('/api/v1/appointment-workspaces', headers=headers, json=payload)
        assert response.status_code == 201, response.text
        db = TestSessionLocal()
        saved = db.query(Appointment).filter(Appointment.id == ids[index]).one()
        assert saved.status == status
        if attendance == 'attended':
            assert saved.completed_at is not None
        if attendance == 'rescheduled':
            assert saved.scheduled_at.date() == (scheduled + timedelta(days=5)).date()
        db.close()
    cross_person = test_client.post('/api/v1/appointment-workspaces', headers=headers, json={
        'appointment_id': ids[4],
        'idempotency_key': 'cross-person-appointment-link-01',
        'payload': {'attendanceStatus': 'attended'},
    })
    assert cross_person.status_code == 404, cross_person.text

def test_appointment_to_lcr_and_follow_up_survives_session_refresh(client):
    from app.models.time_location import Appointment
    test_client, owner, other = client
    db = TestSessionLocal()
    appointment = Appointment(
        person_id=owner['person']['id'],
        title='Neurology review',
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
        provider='Clinic',
        status='scheduled',
        created_by_caregiver_id=owner['caregiver']['id'],
    )
    db.add(appointment)
    db.commit()
    appointment_id = appointment.id
    db.close()
    headers = {'Authorization': 'Bearer ' + owner['access_token']}
    actual_visit = datetime.now(timezone.utc).isoformat()
    note = 'Caregiver reports the professional asked for a review of the current treatment next month.'
    saved = test_client.post('/api/v1/appointment-workspaces', headers=headers, json={
        'appointment_id': appointment_id,
        'status': 'outcome_pending',
        'idempotency_key': 'appointment-full-flow-0001',
        'payload': {
            'attendanceStatus': 'attended',
            'actualVisitAt': actual_visit,
            'clinicianFindings': note,
            'followUps': [{'action': 'Call the clinic for the next review date', 'owner': 'Family caregiver', 'due': '2026-11-12', 'status': 'Not started', 'resultStatus': 'Not expected', 'notes': '', 'register': True}],
        },
    })
    assert saved.status_code == 201, saved.text
    record_id = saved.json()['id']
    db = TestSessionLocal()
    linked = db.query(Appointment).filter(Appointment.id == appointment_id).one()
    assert linked.status == 'completed'
    assert linked.completed_at is not None
    db.close()
    evidence = test_client.post('/api/v1/evidence', headers=headers, json={
        'person_id': owner['person']['id'],
        'type': 'appointment_note',
        'evidence_kind': 'caregiver_report',
        'epistemic_status': 'caregiver_reported',
        'source_text': note,
        'source_type': 'caregiver_report_of_professional_statement',
        'reported_by_label': 'Family caregiver',
        'reported_by_caregiver_id': owner['caregiver']['id'],
        'uploaded_by_caregiver_id': owner['caregiver']['id'],
        'occurred_at': actual_visit,
        'collection_method': 'manual',
        'ingestion_key': 'appointment-evidence-full-flow-0001',
    })
    assert evidence.status_code == 200, evidence.text
    evidence_replay = test_client.post('/api/v1/evidence', headers=headers, json={
        'person_id': owner['person']['id'],
        'type': 'appointment_note',
        'evidence_kind': 'caregiver_report',
        'epistemic_status': 'caregiver_reported',
        'source_text': note,
        'source_type': 'caregiver_report_of_professional_statement',
        'reported_by_label': 'Family caregiver',
        'reported_by_caregiver_id': owner['caregiver']['id'],
        'uploaded_by_caregiver_id': owner['caregiver']['id'],
        'occurred_at': actual_visit,
        'collection_method': 'manual',
        'ingestion_key': 'appointment-evidence-full-flow-0001',
    })
    assert evidence_replay.status_code == 200, evidence_replay.text
    assert evidence_replay.json()['id'] == evidence.json()['id']
    assertion = test_client.post('/api/v1/care-continuity/assertions', headers=headers, json={
        'subject_type': 'appointment',
        'subject_id': record_id,
        'field': 'clinicianFindings',
        'assertion_kind': 'time_bound_observation',
        'asserted_value': note,
        'source_label': 'Caregiver-reported professional statement',
        'source_text': note,
        'evidence_ids': [evidence.json()['id']],
        'observed_at': actual_visit,
    })
    assert assertion.status_code == 201, assertion.text
    assertion_replay = test_client.post('/api/v1/care-continuity/assertions', headers=headers, json={
        'subject_type': 'appointment',
        'subject_id': record_id,
        'field': 'clinicianFindings',
        'assertion_kind': 'time_bound_observation',
        'asserted_value': note,
        'source_label': 'Caregiver-reported professional statement',
        'source_text': note,
        'evidence_ids': [evidence.json()['id']],
        'observed_at': actual_visit,
    })
    assert assertion_replay.status_code == 201, assertion_replay.text
    assert assertion_replay.json()['id'] == assertion.json()['id']
    assertion_conflict = test_client.post('/api/v1/care-continuity/assertions', headers=headers, json={
        'subject_type': 'appointment',
        'subject_id': record_id,
        'field': 'clinicianFindings',
        'assertion_kind': 'time_bound_observation',
        'asserted_value': 'Changed wording without new evidence',
        'source_label': 'Caregiver-reported professional statement',
        'source_text': note,
        'evidence_ids': [evidence.json()['id']],
        'observed_at': actual_visit,
    })
    assert assertion_conflict.status_code == 409, assertion_conflict.text
    accepted = test_client.patch('/api/v1/care-continuity/assertions/' + assertion.json()['id'] + '/status', headers=headers, json={'assertion_status': 'accepted'})
    assert accepted.status_code == 200, accepted.text
    conflicting_note = 'A second caregiver note records a different recollection of the professional instruction.'
    conflicting_evidence = test_client.post('/api/v1/evidence', headers=headers, json={
        'person_id': owner['person']['id'],
        'type': 'appointment_note',
        'evidence_kind': 'caregiver_report',
        'epistemic_status': 'caregiver_reported',
        'source_text': conflicting_note,
        'source_type': 'caregiver_report_of_professional_statement',
        'reported_by_label': 'Family caregiver',
        'reported_by_caregiver_id': owner['caregiver']['id'],
        'uploaded_by_caregiver_id': owner['caregiver']['id'],
        'occurred_at': actual_visit,
        'collection_method': 'manual',
        'ingestion_key': 'appointment-evidence-conflict-0001',
    })
    assert conflicting_evidence.status_code == 200, conflicting_evidence.text
    conflicting_assertion = test_client.post('/api/v1/care-continuity/assertions', headers=headers, json={
        'subject_type': 'appointment',
        'subject_id': record_id,
        'field': 'clinicianFindings',
        'assertion_kind': 'time_bound_observation',
        'asserted_value': conflicting_note,
        'source_label': 'Caregiver-reported professional statement',
        'source_text': conflicting_note,
        'evidence_ids': [conflicting_evidence.json()['id']],
        'observed_at': actual_visit,
    })
    assert conflicting_assertion.status_code == 201, conflicting_assertion.text
    db = TestSessionLocal()
    contradiction = db.query(Contradiction).filter(
        Contradiction.person_id == owner['person']['id'],
        Contradiction.subject_type == 'appointment',
        Contradiction.subject_id == record_id,
        Contradiction.field == 'clinicianFindings',
        Contradiction.resolution_status == 'unresolved',
    ).one_or_none()
    assert contradiction is not None
    assert set(contradiction.source_assertion_ids) == {assertion.json()['id'], conflicting_assertion.json()['id']}
    db.close()
    action = test_client.post('/api/v1/registered-actions/' + owner['person']['id'] + '/approve', headers=headers, json={
        'explicit_approval': True,
        'idempotency_key': 'appointment-followup-full-flow-0001',
        'suggestion_id': 'appointment-' + record_id + '-followup-0',
        'title': 'Call the clinic for the next review date',
        'action_type': 'follow_up',
        'rationale': 'Caregiver-approved next step after the appointment.',
        'limitations': 'The responsible-person name is a note and does not grant account access.',
        'evidence_ids': [evidence.json()['id']],
        'create_reminder': False,
    })
    assert action.status_code == 201, action.text
    progressed = test_client.patch('/api/v1/registered-actions/' + action.json()['id'] + '/status', headers=headers, json={
        'status': 'in_progress',
        'expected_version': action.json()['version'],
        'reason': 'Caregiver started the follow-up.',
    })
    assert progressed.status_code == 200, progressed.text
    refreshed = test_client.post('/api/v1/sessions/refresh', headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    fresh_headers = {'Authorization': 'Bearer ' + refreshed.json()['access_token']}
    email = 'appointment-' + uuid4().hex + '@example.com'
    account = test_client.post('/api/v1/accounts', headers=fresh_headers, json={
        'email': email,
        'password': 'SecureAppointmentPass2026!',
    })
    assert account.status_code == 201, account.text
    login = test_client.post('/api/v1/accounts/login', json={
        'email': email,
        'password': 'SecureAppointmentPass2026!',
    })
    assert login.status_code == 200, login.text
    device_headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
    recovered_on_new_device = test_client.get('/api/v1/appointment-workspaces/' + record_id, headers=device_headers)
    assert recovered_on_new_device.status_code == 200, recovered_on_new_device.text
    assert recovered_on_new_device.json()['payload']['clinicianFindings'] == note
    recovered = test_client.get('/api/v1/appointment-workspaces/' + record_id, headers=fresh_headers)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['payload']['clinicianFindings'] == note
    assert recovered.json()['payload']['followUps'][0]['action'] == 'Call the clinic for the next review date'
    history = test_client.get('/api/v1/appointment-workspaces/' + record_id + '/revisions', headers=fresh_headers)
    assert history.status_code == 200, history.text
    assert history.json()['items'][0]['payload']['attendanceStatus'] == 'attended'
    other_headers = {'Authorization': 'Bearer ' + other['access_token']}
    denied = test_client.get('/api/v1/appointment-workspaces/' + record_id, headers=other_headers)
    assert denied.status_code == 404, denied.text
    other_evidence = test_client.get('/api/v1/evidence/' + other['person']['id'], headers=other_headers)
    assert other_evidence.status_code == 200, other_evidence.text
    assert note not in other_evidence.text

def test_follow_up_reassignment_requires_accepted_consent_governed_handoff(client):
    from app.models.action import ActionLifecycleAudit
    from app.models.care import Caregiver
    from app.models.collaboration import ConsentRecord
    from app.models.privacy_delivery import HandoffRecord, HandoffRecipient
    from app.models.anonymous_session import AnonymousSession
    import hashlib
    import json

    test_client, owner, _ = client
    owner_headers = {"Authorization": "Bearer " + owner["access_token"]}
    note = "Caregiver recorded the clinic's request to confirm the next review date."
    evidence = test_client.post("/api/v1/evidence", headers=owner_headers, json={
        "person_id": owner["person"]["id"],
        "type": "appointment_note",
        "evidence_kind": "caregiver_report",
        "epistemic_status": "caregiver_reported",
        "source_text": note,
        "source_type": "caregiver_report",
        "reported_by_label": "Family caregiver",
        "reported_by_caregiver_id": owner["caregiver"]["id"],
        "uploaded_by_caregiver_id": owner["caregiver"]["id"],
        "collection_method": "manual",
        "ingestion_key": "reassignment-evidence-0001",
    })
    assert evidence.status_code == 200, evidence.text
    created = test_client.post("/api/v1/registered-actions/" + owner["person"]["id"] + "/approve",
        headers=owner_headers, json={
            "explicit_approval": True,
            "idempotency_key": "reassignment-action-0001",
            "suggestion_id": "reassignment-follow-up-0001",
            "title": "Call the clinic to confirm the next review date",
            "action_type": "follow_up",
            "rationale": "Caregiver-approved coordination step.",
            "evidence_ids": [evidence.json()["id"]],
            "create_reminder": False,
        })
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]

    db = TestSessionLocal()
    try:
        target = Caregiver(
            id="appointment-follow-up-recipient",
            name="Second family caregiver",
            email="follow-up-recipient@example.com",
            relationship="Family",
            person_id=owner["person"]["id"],
        )
        db.add(target)
        for caregiver_id in (owner["caregiver"]["id"], target.id):
            db.add(ConsentRecord(
                person_id=owner["person"]["id"],
                caregiver_id=caregiver_id,
                medical_disclaimer_acknowledged=True,
                privacy_model_acknowledged=True,
                multi_caregiver_acknowledged=True,
                no_advertising_acknowledged=True,
                accepted_at=datetime.now(timezone.utc),
            ))
        handoff_id = "appointment-follow-up-handoff-0001"
        now = datetime.now(timezone.utc)
        summary_snapshot = {
            "sections": {"open_loops": "Call the clinic to confirm the next review date"},
            "source_record_ids": [],
            "source_record_tables": [],
            "prepared_at": now.isoformat(),
            "stored_at": now.isoformat(),
            "caregiver_reviewed": True,
            "review_attestation": "client_declared_not_server_verified",
            "source_id_validation": "not_supplied",
            "semantic_grounding_status": "not_automatically_verified",
        }
        summary_snapshot["content_sha256"] = hashlib.sha256(
            json.dumps(summary_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        db.add(HandoffRecord(
            id=handoff_id,
            person_id=owner["person"]["id"],
            created_by=owner["caregiver"]["id"],
            handoff_status="accepted",
            scope_snapshot=["open_loops", "appointments"],
            summary_snapshot=summary_snapshot,
            expires_at=now + timedelta(days=7),
            policy_version="handoff-v1",
        ))
        db.flush()
        db.add(HandoffRecipient(
            id="appointment-follow-up-recipient-link",
            handoff_id=handoff_id,
            person_id=owner["person"]["id"],
            recipient_id=target.id,
            access_status="accepted",
            accepted_at=now,
        ))
        token = "appointment-follow-up-recipient-token"
        db.add(AnonymousSession(
            person_id=owner["person"]["id"],
            caregiver_id=target.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now + timedelta(days=30),
        ))
        db.commit()
        target_id = target.id
    finally:
        db.close()

    denied = test_client.patch("/api/v1/registered-actions/" + action_id + "/assignee",
        headers=owner_headers, json={
            "target_caregiver_id": target_id,
            "handoff_id": "missing-handoff",
            "expected_version": created.json()["version"],
            "idempotency_key": "appointment-follow-up-denied-0001",
            "reason": "Transfer follow-up responsibility.",
        })
    assert denied.status_code == 403, denied.text

    reassigned = test_client.patch("/api/v1/registered-actions/" + action_id + "/assignee",
        headers=owner_headers, json={
            "target_caregiver_id": target_id,
            "handoff_id": handoff_id,
            "expected_version": created.json()["version"],
            "idempotency_key": "appointment-follow-up-assigned-0001",
            "reason": "The second caregiver accepted responsibility for the clinic call.",
        })
    assert reassigned.status_code == 200, reassigned.text
    assert reassigned.json()["responsible_caregiver_id"] == target_id
    assert reassigned.json()["version"] == created.json()["version"] + 1
    assignment_replay = test_client.patch("/api/v1/registered-actions/" + action_id + "/assignee",
        headers=owner_headers, json={
            "target_caregiver_id": target_id,
            "handoff_id": handoff_id,
            "expected_version": created.json()["version"],
            "idempotency_key": "appointment-follow-up-assigned-0001",
            "reason": "The second caregiver accepted responsibility for the clinic call.",
        })
    assert assignment_replay.status_code == 200, assignment_replay.text
    assert assignment_replay.json()["version"] == reassigned.json()["version"]

    history = test_client.get("/api/v1/registered-actions/" + action_id + "/history", headers=owner_headers)
    assert history.status_code == 200, history.text
    assignment = next(row for row in history.json() if row["handoff_id"] == handoff_id)
    assert assignment["previous_responsible_caregiver_id"] == owner["caregiver"]["id"]
    assert assignment["new_responsible_caregiver_id"] == target_id

    target_headers = {"Authorization": "Bearer " + token}
    target_update = test_client.patch("/api/v1/registered-actions/" + action_id + "/status",
        headers=target_headers, json={
            "status": "in_progress",
            "expected_version": reassigned.json()["version"],
            "reason": "The assigned caregiver began the clinic follow-up.",
        })
    assert target_update.status_code == 200, target_update.text
    assert target_update.json()["status"] == "in_progress"

    db = TestSessionLocal()
    try:
        audit = db.query(ActionLifecycleAudit).filter(
            ActionLifecycleAudit.action_id == action_id,
            ActionLifecycleAudit.handoff_id == handoff_id,
        ).one()
        audit.reason = "Attempted audit alteration"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_follow_up_result_receipt_and_review_are_separate_idempotent_events(client):
    from app.models.care_continuity import CareActionOutcome

    test_client, owner, other = client
    headers = {"Authorization": "Bearer " + owner["access_token"]}
    action_source = "Caregiver recorded the clinician's request to obtain the test result."
    action_evidence = test_client.post("/api/v1/evidence", headers=headers, json={
        "person_id": owner["person"]["id"],
        "type": "appointment_note",
        "evidence_kind": "caregiver_report",
        "epistemic_status": "caregiver_reported",
        "source_text": action_source,
        "source_type": "caregiver_report",
        "reported_by_label": "Family caregiver",
        "reported_by_caregiver_id": owner["caregiver"]["id"],
        "uploaded_by_caregiver_id": owner["caregiver"]["id"],
        "collection_method": "manual",
        "ingestion_key": "appointment-result-action-source-0001",
    })
    assert action_evidence.status_code == 200, action_evidence.text
    action = test_client.post("/api/v1/registered-actions/" + owner["person"]["id"] + "/approve",
        headers=headers, json={
            "explicit_approval": True,
            "idempotency_key": "appointment-result-action-0001",
            "suggestion_id": "appointment-result-follow-up-0001",
            "title": "Call the clinic to obtain the test result",
            "action_type": "follow_up",
            "rationale": "Caregiver-approved result coordination.",
            "evidence_ids": [action_evidence.json()["id"]],
            "create_reminder": False,
        })
    assert action.status_code == 201, action.text

    result_text = "Caregiver reports that the clinic sent the requested result and it needs clinician interpretation."
    result_evidence = test_client.post("/api/v1/evidence", headers=headers, json={
        "person_id": owner["person"]["id"],
        "type": "appointment_result",
        "evidence_kind": "caregiver_report",
        "epistemic_status": "caregiver_reported",
        "source_text": result_text,
        "source_type": "caregiver_recorded_received_result",
        "reported_by_label": "Family caregiver",
        "reported_by_caregiver_id": owner["caregiver"]["id"],
        "uploaded_by_caregiver_id": owner["caregiver"]["id"],
        "collection_method": "manual",
        "ingestion_key": "appointment-result-evidence-0001",
    })
    assert result_evidence.status_code == 200, result_evidence.text

    received_payload = {
        "action_id": action.json()["id"],
        "action_type": "follow_up_result",
        "outcome_status": "result_received",
        "notes": result_text,
        "evidence_ids": [result_evidence.json()["id"]],
        "idempotency_key": "appointment-result-received-0001",
    }
    received = test_client.post("/api/v1/care-continuity/action-outcomes", headers=headers, json=received_payload)
    assert received.status_code == 201, received.text
    replay = test_client.post("/api/v1/care-continuity/action-outcomes", headers=headers, json=received_payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == received.json()["id"]

    conflicting_replay = test_client.post("/api/v1/care-continuity/action-outcomes", headers=headers, json={
        **received_payload,
        "notes": "Different result wording using the same save key.",
    })
    assert conflicting_replay.status_code == 409, conflicting_replay.text

    reviewed_payload = {
        "action_id": action.json()["id"],
        "action_type": "follow_up_result",
        "outcome_status": "result_reviewed",
        "related_outcome_id": received.json()["id"],
        "review_decision": "needs_clinician_review",
        "notes": "The caregiver reviewed the received information and recorded that a clinician must interpret it.",
        "evidence_ids": [result_evidence.json()["id"]],
        "idempotency_key": "appointment-result-reviewed-0001",
    }
    reviewed = test_client.post("/api/v1/care-continuity/action-outcomes", headers=headers, json=reviewed_payload)
    assert reviewed.status_code == 201, reviewed.text
    assert reviewed.json()["created_by_caregiver_id"] == owner["caregiver"]["id"]
    assert reviewed.json()["related_outcome_id"] == received.json()["id"]
    assert reviewed.json()["review_decision"] == "needs_clinician_review"
    assert reviewed.json()["reviewed_by_caregiver_id"] == owner["caregiver"]["id"]
    assert reviewed.json()["reviewed_at"] is not None
    assert received.json()["received_by_caregiver_id"] == owner["caregiver"]["id"]
    assert received.json()["received_at"] is not None

    timeline = test_client.get("/api/v1/care-continuity/outcomes/" + owner["person"]["id"], headers=headers)
    assert timeline.status_code == 200, timeline.text
    result_events = [row for row in timeline.json() if row.get("action_id") == action.json()["id"]]
    assert {row["outcome_status"] for row in result_events} >= {"result_received", "result_reviewed"}

    other_headers = {"Authorization": "Bearer " + other["access_token"]}
    other_timeline = test_client.get("/api/v1/care-continuity/outcomes/" + other["person"]["id"], headers=other_headers)
    assert other_timeline.status_code == 200, other_timeline.text
    assert not any(row.get("action_id") == action.json()["id"] for row in other_timeline.json())

    db = TestSessionLocal()
    try:
        received_row = db.query(CareActionOutcome).filter(CareActionOutcome.id == received.json()["id"]).one()
        received_row.notes = "Attempted overwrite of the received result."
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_appointment_workspace_update_is_idempotent(client):
    test_client, owner, _ = client
    headers = {'Authorization': 'Bearer ' + owner['access_token']}
    created = test_client.post('/api/v1/appointment-workspaces', headers=headers, json={
        'status': 'outcome_pending',
        'idempotency_key': 'appointment-update-create-01',
        'payload': {'attendanceStatus': 'not_recorded', 'caregiverReport': 'Initial note'},
    })
    assert created.status_code == 201, created.text
    record_id = created.json()['id']
    update_body = {
        'expected_version': 1,
        'idempotency_key': 'appointment-update-retry-0001',
        'status': 'outcome_pending',
        'payload': {'attendanceStatus': 'attended', 'actualVisitAt': datetime.now(timezone.utc).isoformat(), 'caregiverReport': 'Updated note'},
        'change_reason': 'Caregiver saved visit outcome',
    }
    first = test_client.patch('/api/v1/appointment-workspaces/' + record_id, headers=headers, json=update_body)
    replay = test_client.patch('/api/v1/appointment-workspaces/' + record_id, headers=headers, json=update_body)
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()['version_number'] == 2
    assert replay.json()['version_number'] == 2
    changed = {**update_body, 'payload': {**update_body['payload'], 'caregiverReport': 'Different update'}}
    conflict = test_client.patch('/api/v1/appointment-workspaces/' + record_id, headers=headers, json=changed)
    assert conflict.status_code == 409, conflict.text
    revisions = test_client.get('/api/v1/appointment-workspaces/' + record_id + '/revisions', headers=headers)
    assert revisions.status_code == 200, revisions.text
    assert len(revisions.json()['items']) == 2

@pytest.mark.parametrize(
    ("attendance", "expected_status"),
    [
        ("attended", "completed"),
        ("cancelled", "cancelled"),
        ("rescheduled", "rescheduled"),
        ("missed", "missed"),
    ],
)
def test_all_appointment_attendance_outcomes_update_the_scheduled_record(client, attendance, expected_status):
    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    original_time = datetime.now(timezone.utc) + timedelta(days=2)
    appointment = Appointment(
        person_id=owner["person"]["id"],
        title="Attendance state verification",
        scheduled_at=original_time,
        provider="Care clinic",
        status="scheduled",
        created_by_caregiver_id=owner["caregiver"]["id"],
    )
    db = TestSessionLocal()
    db.add(appointment)
    db.commit()
    appointment_id = appointment.id
    db.close()

    payload = {"attendanceStatus": attendance}
    if attendance == "attended":
        payload["actualVisitAt"] = datetime.now(timezone.utc).isoformat()
    if attendance == "rescheduled":
        new_time = datetime.now(timezone.utc) + timedelta(days=7)
        payload["rescheduledAt"] = new_time.isoformat()

    saved = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "appointment_id": appointment_id,
        "status": "outcome_pending",
        "idempotency_key": "attendance-state-" + attendance + "-0001",
        "payload": payload,
    })
    assert saved.status_code == 201, saved.text

    db = TestSessionLocal()
    persisted = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.person_id == owner["person"]["id"],
    ).one()
    assert persisted.status == expected_status
    if attendance == "attended":
        assert persisted.completed_at is not None
        assert persisted.resolved_at is None
    elif attendance in {"cancelled", "missed"}:
        assert persisted.completed_at is None
        assert persisted.resolved_at is not None
    else:
        assert persisted.completed_at is None
        assert persisted.resolved_at is None
        assert persisted.scheduled_at.replace(tzinfo=timezone.utc).date() == new_time.date()
    db.close()


def test_appointment_workspace_rejects_invalid_lifecycle_transition(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "preparation",
        "payload": {"visit_purpose": "Discuss recent changes"},
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    invalid = test_client.patch(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=headers,
        json={
            "expected_version": 1,
            "status": "closed_with_outstanding_items",
            "payload": {"visit_purpose": "Discuss recent changes"},
            "change_reason": "Invalid direct closure",
        },
    )
    assert invalid.status_code == 409, invalid.text
    assert invalid.json()["detail"]["code"] == "invalid_appointment_workspace_transition"

    unchanged = test_client.get(f"/api/v1/appointment-workspaces/{record_id}", headers=headers)
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["status"] == "preparation"
    assert unchanged.json()["version_number"] == 1


def test_appointment_preparation_brief_uses_stable_source_coverage_contract(client):
    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    response = test_client.get("/api/v1/appointment-workspaces/preparation-brief", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["person_scope_verified"] is True
    assert body["read_only"] is True
    assert body["updates_lcr"] is False
    assert body["source_coverage"] == {
        "care_facts": "empty",
        "care_events": "empty",
        "appointments": "empty",
        "evidence_freshness_assessments": "empty",
        "medications_and_allergies": "unavailable",
        "documents": "empty",
        "community": "not_authorized",
    }

    # The preparation brief must never disclose another person's care data.
    other_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    assert "Other person private care detail" not in response.text


def test_appointment_preparation_brief_includes_person_scoped_extracted_document_text(client):
    from app.models.capture_analysis import CaptureAnalysis

    test_client, owner, other = client
    with TestSessionLocal() as db:
        evidence = Evidence(
            id="appointment-prep-document",
            person_id=owner["person"]["id"],
            type="document",
            source_text="",
            uploaded_by_caregiver_id=owner["caregiver"]["id"],
            source_type="caregiver_upload",
        )
        db.add(evidence)
        db.flush()
        db.add(CaptureAnalysis(
            id="appointment-prep-extraction",
            person_id=owner["person"]["id"],
            caregiver_id=owner["caregiver"]["id"],
            source_evidence_id=evidence.id,
            source_text="Discharge summary: follow-up requested in two weeks.",
            extraction_status="available",
            candidates={
                "kind": "document_extraction_v1",
                "filename": "discharge-summary.txt",
                "extraction_method": "server_utf8_text",
            },
            review_history=[],
        ))
        db.commit()

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get("/api/v1/appointment-workspaces/preparation-brief", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_coverage"]["documents"] == "available"
    assert len(body["documents"]) == 1
    assert body["documents"][0]["source_id"] == "appointment-prep-extraction"
    assert body["documents"][0]["source_evidence_id"] == "appointment-prep-document"
    assert "follow-up requested in two weeks" in body["documents"][0]["extracted_text"]
    assert body["documents"][0]["interpretation_limit"].startswith("Extracted document text is source material")

    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    private_response = test_client.get(
        "/api/v1/appointment-workspaces/preparation-brief",
        headers=other_headers,
    )
    assert private_response.status_code == 200, private_response.text
    assert private_response.json()["documents"] == []
    assert private_response.json()["source_coverage"]["documents"] == "empty"
    assert "Discharge summary" not in private_response.text



def test_appointment_outcome_draft_is_read_only_and_person_scoped(client):
    test_client, owner, other = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    saved = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "outcome_pending",
        "payload": {
            "attendanceStatus": "attended",
            "actualVisitDate": "2026-11-07T10:30:00+00:00",
            "caregiverReport": "The caregiver noticed increased night waking.",
            "clinicianFindings": "The caregiver recorded that the clinician requested a sleep diary.",
            "decisions": "The caregiver recorded that a review will be arranged.",
            "instructions": "Keep the existing sleep diary for the next review.",
            "unanswered": "The reason for the change remains unclear.",
            "pendingResults": "A report is still expected.",
            "followUps": [{"action": "Bring the sleep diary", "owner": "", "due": "", "status": "Not started"}],
        },
        "change_reason": "Save appointment outcome notes",
        "idempotency_key": "appointment-outcome-draft-test-001",
    })
    assert saved.status_code == 201, saved.text
    record_id = saved.json()["id"]

    draft = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-draft",
        headers=headers,
    )
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["read_only"] is True
    assert body["updates_lcr"] is False
    assert body["record_id"] == record_id
    assert body["attendance_status"] == "attended"
    assert body["sections"]["caregiver_report"][0]["text"] == "The caregiver noticed increased night waking."
    professional_item = body["sections"]["professional_information"][0]
    assert professional_item["text"] == "The caregiver recorded that the clinician requested a sleep diary."
    assert professional_item["epistemic_status"] == "caregiver_reported"
    assert body["sections"]["follow_ups"][0]["text"] == "Bring the sleep diary"
    assert "No appointment outcome has been recorded" not in draft.text

    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    denied = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-draft",
        headers=other_headers,
    )
    assert denied.status_code == 404
