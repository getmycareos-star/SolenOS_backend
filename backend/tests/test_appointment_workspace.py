from datetime import datetime, timedelta, timezone
import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.appointment_workspace import AppointmentWorkspaceRecord
from app.models.care import Caregiver, CareEvent, Evidence
from app.models.time_location import Appointment
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care_continuity import EvidenceFreshnessPolicy, EvidenceFreshnessAssessment, EvidenceReviewRecord, EvidenceClaimAssessment, EvidenceCompletenessPolicy, EvidenceCompletenessAssessment, CareAssertion, CareStateRevision
from app.models.ai_output_governance import AIOutputLCRProposal, AIOutputLCRDecision

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
                subject_type VARCHAR(80),
                subject_id VARCHAR(255),
                fact_type VARCHAR(100),
                fact_value TEXT,
                epistemic_status VARCHAR(40),
                effective_at DATETIME,
                recorded_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME,
                superseded_by_fact_id VARCHAR(36),
                created_by_caregiver_id VARCHAR(255)
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
    # Deliberately remove one required source to verify the projection fails
    # closed and reports incomplete coverage rather than claiming completeness.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE care_decisions")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_facts (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES
              ('owner-fact', :owner_id, 'person', :owner_id, 'sleep_pattern', 'Caregiver-reported sleep changes', 'reported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
              ('other-fact', :other_id, 'person', :other_id, 'private_detail', 'Other person private care detail', 'confirmed', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
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
    assert "decisions" in body["missing_required_sources"]


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
    assert saved.json()["qwen_extraction_status"] == "queued"

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

    from app.models.open_loop import OpenLoopRecord, OpenLoopEvent
    db = TestSessionLocal()
    synced = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == session["person"]["id"],
        OpenLoopRecord.source_type == "appointment_workspace_follow_up",
        OpenLoopRecord.source_id.like(f"{record_id}:%"),
    ).all()
    assert len(synced) == 1
    assert synced[0].title == "Call clinic for result"
    assert synced[0].owner_label == "Family caregiver"
    # Date-only input has no timezone; preserve the text without inventing a due instant.
    assert synced[0].deadline_at is None
    assert synced[0].evidence[0]["reported_due"] == "2026-11-02"
    assert db.query(OpenLoopEvent).filter_by(loop_id=synced[0].id).count() == 1
    db.close()

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
    db = TestSessionLocal()
    synced_after_update = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == session["person"]["id"],
        OpenLoopRecord.source_type == "appointment_workspace_follow_up",
        OpenLoopRecord.source_id.like(f"{record_id}:%"),
    ).all()
    assert len(synced_after_update) == 1
    assert db.query(OpenLoopEvent).filter_by(loop_id=synced_after_update[0].id).count() == 2
    db.close()

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




def test_appointment_workspace_writes_require_add_evidence_permission(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    db = TestSessionLocal()
    try:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ).one()
        grant.can_manage_members = False
        grant.permissions = ["view_lcr"]
        db.commit()
    finally:
        db.close()

    denied_create = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"payload": {"visit_purpose": "Unauthorized write"}},
    )
    assert denied_create.status_code == 403, denied_create.text
    assert "add evidence" in denied_create.text.lower()

    db = TestSessionLocal()
    try:
        assert db.query(AppointmentWorkspaceRecord).filter_by(
            person_id=session["person"]["id"]
        ).count() == 0
    finally:
        db.close()


def test_appointment_workspace_update_requires_add_evidence_permission(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"payload": {"visit_purpose": "Authorized initial note"}},
    )
    assert created.status_code == 201, created.text

    db = TestSessionLocal()
    try:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ).one()
        grant.can_manage_members = False
        grant.permissions = ["view_lcr"]
        db.commit()
    finally:
        db.close()

    denied_update = test_client.patch(
        f"/api/v1/appointment-workspaces/{created.json()['id']}",
        headers=headers,
        json={
            "expected_version": 1,
            "payload": {"visit_purpose": "Unauthorized overwrite"},
            "change_reason": "Permission regression test",
        },
    )
    assert denied_update.status_code == 403, denied_update.text
    assert "add evidence" in denied_update.text.lower()

    unchanged = test_client.get(
        f"/api/v1/appointment-workspaces/{created.json()['id']}",
        headers=headers,
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["version_number"] == 1
    assert unchanged.json()["payload"]["visit_purpose"] == "Authorized initial note"


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
            INSERT INTO care_facts (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES (:id, :person_id, 'person', :person_id, :type, :value, :status, :created, :updated)
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
        claim = CareAssertion(
            id="appointment-claim",
            person_id=person_id,
            subject_type="care_recipient",
            subject_id=person_id,
            field="sleep",
            assertion_kind="current_state",
            asserted_value="Caregiver reported a change in sleep.",
            source_label="Daily Care report",
            source_text="Caregiver reported a change in sleep.",
            evidence_ids=[evidence.id],
            assertion_status="reported",
            epistemic_state="reported",
            created_by_caregiver_id=caregiver_id,
        )
        review = EvidenceReviewRecord(
            id="appointment-evidence-review",
            person_id=person_id,
            evidence_id=evidence.id,
            review_type="reliability",
            outcome="unverified",
            rationale="The original author has not been confirmed.",
            criteria=[{"criterion": "source attribution", "result": "incomplete"}],
            policy_version="evidence-review-v1",
            source_attribution_snapshot="{}",
            reviewed_by_caregiver_id=caregiver_id,
        )
        claim_assessment = EvidenceClaimAssessment(
            id="appointment-claim-assessment",
            person_id=person_id,
            claim_id=claim.id,
            claim_version=1,
            status="unverified",
            rationale="The caregiver report is retained but not independently verified.",
            policy_version="claim-assessment-v1",
            evidence_links=[{
                "evidence_id": evidence.id,
                "relationship": "neutral",
                "source_passage": "A dated source event",
                "scope_note": "Source attribution remains unverified.",
            }],
            reviewed_by_caregiver_id=caregiver_id,
        )
        completeness_policy = EvidenceCompletenessPolicy(
            id="appointment-completeness-policy",
            person_id=person_id,
            domain_key="document_history",
            purpose_key="professional_handoff",
            policy_version="completeness-v1",
            required_fields=["author", "date"],
            rationale="Required source fields for appointment preparation.",
            created_by_caregiver_id=caregiver_id,
        )
        prior_completeness_assessment = EvidenceCompletenessAssessment(
            id="appointment-completeness-assessment-old",
            person_id=person_id,
            policy_id=completeness_policy.id,
            policy_version="completeness-v1",
            subject_type="evidence",
            subject_id=evidence.id,
            assessment_status="complete",
            required_fields=["author", "date"],
            satisfied_fields=["author", "date"],
            missing_fields=[],
            field_evidence_json="{}",
            evidence_ids=[evidence.id],
            rationale="An earlier review found both fields recorded.",
            assessment_basis="linked_source_coverage_only",
            semantic_sufficiency="not_automatically_verified",
            assessed_by_caregiver_id=caregiver_id,
            assessed_at=source_time,
        )
        completeness_assessment = EvidenceCompletenessAssessment(
            id="appointment-completeness-assessment",
            person_id=person_id,
            policy_id=completeness_policy.id,
            policy_version="completeness-v1",
            subject_type="evidence",
            subject_id=evidence.id,
            assessment_status="partial",
            required_fields=["author", "date"],
            satisfied_fields=["date"],
            missing_fields=["author"],
            field_evidence_json="{}",
            evidence_ids=[evidence.id],
            rationale="The source author is not identified.",
            assessment_basis="linked_source_coverage_only",
            semantic_sufficiency="not_automatically_verified",
            assessed_by_caregiver_id=caregiver_id,
        )
        foreign_review = EvidenceReviewRecord(
            id="other-person-review",
            person_id=other["person"]["id"],
            evidence_id=evidence.id,
            review_type="reliability",
            outcome="supported",
            rationale="This review belongs to another care record.",
            criteria=[],
            policy_version="evidence-review-v1",
            source_attribution_snapshot="{}",
            reviewed_by_caregiver_id=other["caregiver"]["id"],
        )
        foreign_claim_assessment = EvidenceClaimAssessment(
            id="other-person-claim-assessment",
            person_id=other["person"]["id"],
            claim_id=claim.id,
            claim_version=2,
            status="supported",
            rationale="This assessment belongs to another care record.",
            policy_version="claim-assessment-v1",
            evidence_links=[],
            reviewed_by_caregiver_id=other["caregiver"]["id"],
        )
        foreign_completeness_policy = EvidenceCompletenessPolicy(
            id="other-person-completeness-policy",
            person_id=other["person"]["id"],
            domain_key="document_history",
            purpose_key="professional_handoff",
            policy_version="completeness-v1",
            required_fields=["author"],
            rationale="Other person's checklist.",
            created_by_caregiver_id=other["caregiver"]["id"],
        )
        foreign_completeness_assessment = EvidenceCompletenessAssessment(
            id="other-person-completeness-assessment",
            person_id=other["person"]["id"],
            policy_id=foreign_completeness_policy.id,
            policy_version="completeness-v1",
            subject_type="evidence",
            subject_id=evidence.id,
            assessment_status="complete",
            required_fields=["author"],
            satisfied_fields=["author"],
            missing_fields=[],
            field_evidence_json="{}",
            evidence_ids=[evidence.id],
            rationale="This assessment belongs to another care record.",
            assessment_basis="linked_source_coverage_only",
            semantic_sufficiency="not_automatically_verified",
            assessed_by_caregiver_id=other["caregiver"]["id"],
        )
        db.add_all([
            evidence, policy, assessment, event, claim, review, claim_assessment,
            completeness_policy, prior_completeness_assessment, completeness_assessment,
            foreign_review, foreign_claim_assessment, foreign_completeness_policy,
            foreign_completeness_assessment,
        ])
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
    assert isinstance(body["prior_decisions"], list)
    assert isinstance(body["decision_outcomes"], list)
    assert "decisions" in body["source_completeness"]
    assert "decision_outcomes" in body["source_completeness"]
    assert any(item["source_id"] == "appointment-fact-owned" for item in body["facts"])
    assert "DO NOT LEAK THIS" not in response.text
    assert body["source_coverage"]["community"] == "not_authorized"
    assert body["source_coverage"]["medications_and_allergies"] == "unavailable"
    assert body["source_coverage"]["evidence_freshness_assessments"] == "available"
    assert body["source_coverage"]["evidence_review_records"] == "available"
    assert body["source_coverage"]["evidence_claim_assessments"] == "available"
    assert body["source_coverage"]["evidence_completeness_assessments"] == "available"
    assert any(item["assessment_id"] == "appointment-freshness-assessment" for item in body["freshness_assessments"])
    assert any(item["review_id"] == "appointment-evidence-review" for item in body["evidence_review_records"])
    assert any(item["assessment_id"] == "appointment-claim-assessment" for item in body["claim_assessments"])
    assert [item["assessment_id"] for item in body["completeness_assessments"]] == ["appointment-completeness-assessment"]
    assert body["completeness_assessments"][0]["assessment_status"] == "partial"
    assert all(item["review_id"] != "other-person-review" for item in body["evidence_review_records"])
    assert all(item["assessment_id"] != "other-person-claim-assessment" for item in body["claim_assessments"])
    assert all(item["assessment_id"] != "other-person-completeness-assessment" for item in body["completeness_assessments"])
    evidence_source = body["events"][0]["evidence_sources"][0]
    assert evidence_source["evidence_reviews"][0]["outcome"] == "unverified"
    assert evidence_source["completeness_assessments"][0]["semantic_sufficiency"] == "not_automatically_verified"
    claim_row = next(item for item in body["assertions"] if item["source_id"] == "appointment-claim")
    assert claim_row["claim_assessments"][0]["status"] == "unverified"
    shared_lcr = body["living_care_record_context"]
    assert any(item["id"] == "appointment-freshness-event" for item in shared_lcr["sources"]["events"]["items"])
    assert any(item["id"] == "appointment-freshness-evidence" for item in shared_lcr["sources"]["evidence"]["items"])
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
            INSERT INTO care_facts (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES ('understanding-fact', :person_id, 'person', :person_id, 'mobility', 'Caregiver-reported mobility change',
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
            INSERT INTO care_facts (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at, updated_at)
            VALUES ('understanding-fact-2', :person_id, 'person', :person_id, 'daily_function', 'Newly reported support requirement',
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




def test_appointment_preparation_brief_uses_stable_source_coverage_contract(client):
    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}

    response = test_client.get("/api/v1/appointment-workspaces/preparation-brief", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["person_scope_verified"] is True
    assert body["read_only"] is True
    assert body["updates_lcr"] is False
    shared_lcr = body["living_care_record_context"]
    assert shared_lcr["person_id"] == owner["person"]["id"]
    assert shared_lcr["read_only"] is True
    assert shared_lcr["retrieval_strategy"] == "person_scoped_full_source_scan"
    assert {"facts", "events", "evidence", "assertions"}.issubset(shared_lcr["sources"])
    assert shared_lcr["retrieval_scan_complete"] is True
    assert body["source_coverage"] == {
        "care_facts": "empty",
        "care_events": "empty",
        "appointments": "empty",
        "care_assertions": "empty",
        "open_questions": "empty",
        "contradictions": "empty",
        "evidence_freshness_assessments": "empty",
        "evidence_review_records": "empty",
        "evidence_claim_assessments": "empty",
        "evidence_completeness_assessments": "empty",
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


def test_appointment_outcome_draft_is_read_only_and_person_scoped(client):
    test_client, owner, other = client
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=owner_headers, json={
        "status": "outcome_pending",
        "payload": {
            "caregiver_report": "The caregiver noticed increased night waking.",
            "clinician_findings": "The caregiver recorded that the clinician requested a sleep diary.",
            "decisions": "The caregiver recorded that a review will be arranged.",
            "instructions": "Keep the existing sleep diary for the next review.",
            "unanswered_questions": ["The reason for the change remains unclear."],
            "follow_ups": [{"action": "Bring the sleep diary", "owner": "", "due": "", "status": "Open"}],
        },
        "change_reason": "Save appointment outcome notes",
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    draft = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-draft",
        headers=owner_headers,
    )
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["read_only"] is True
    assert body["updates_lcr"] is False
    assert body["record_id"] == record_id
    assert body["status"] == "ready_for_review"
    assert body["sections"]["caregiver_report"][0]["text"] == "The caregiver noticed increased night waking."
    professional = body["sections"]["professional_information"][0]
    assert professional["text"] == "The caregiver recorded that the clinician requested a sleep diary."
    assert professional["epistemic_status"] == "caregiver_reported"
    assert body["sections"]["follow_ups"][0]["text"] == "Bring the sleep diary"

    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    denied = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-draft",
        headers=other_headers,
    )
    assert denied.status_code == 404


def test_empty_appointment_outcome_draft_does_not_invent_content(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "preparation", "payload": {}, "change_reason": "Create an empty appointment workspace",
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    draft = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-draft",
        headers=headers,
    )
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["status"] == "no_outcome_information"
    assert all(not items for items in body["sections"].values())
    assert body["updates_lcr"] is False



def test_appointment_outcomes_use_append_only_caregiver_review_before_lcr(client):
    test_client, owner, other = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "outcome_pending",
        "payload": {
            "caregiverReport": "Caregiver reports that sleep was interrupted twice last night.",
            "clinicianFindings": "Caregiver recorded that the clinician recommended a follow-up call.",
            "decisions": "Family decided to call the clinic on Monday.",
            "actualVisitDate": "2026-11-07T10:30:00+01:00",
            "followUps": [{"action": "Call the clinic", "owner": "Family caregiver", "status": "Open"}],
        },
        "change_reason": "Record appointment outcome",
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    prepared = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals",
        headers=headers,
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["written_to_lcr"] is False
    assert body["review_required"] is True
    assert body["count"] == 3
    proposals = body["items"]
    assert all(item["status"] == "pending_authorization" for item in proposals)
    assert all(item["proposed_change"]["epistemic_status"] == "caregiver_reported" for item in proposals)
    assert all(item["proposed_change"]["independently_verified"] is False for item in proposals)

    # Repeating candidate generation must return the same proposal IDs.
    replay = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals",
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert [item["id"] for item in replay.json()["items"]] == [item["id"] for item in proposals]

    # Other people cannot list or review the owner's proposals.
    assert test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals",
        headers=other_headers,
    ).status_code == 404
    assert test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[0]['id']}/review",
        headers=other_headers,
        json={"decision": "accept"},
    ).status_code == 404

    # Accept creates exactly one caregiver-reviewed assertion, not a clinical fact.
    accepted = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[0]['id']}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["written_to_lcr"] is True
    assertion_id = accepted_body["assertion_id"]
    db = TestSessionLocal()
    try:
        assertion = db.query(CareAssertion).filter(CareAssertion.id == assertion_id).one()
        assert assertion.assertion_status == "caregiver_reviewed"
        assert assertion.subject_type == "person"
        assert assertion.field == "caregiver_report"
        assert assertion.created_by_caregiver_id == owner["caregiver"]["id"]
        assert assertion.source_proposal_id == proposals[0]["id"]
        assert assertion.source_conversation_id is None
        assert assertion.source_message_id is None
        assert assertion.observed_at is not None
        assert assertion.review_after_days == 30
        assert assertion.epistemic_state == "reported"
        assert len(assertion.evidence_ids or []) == 1
        appointment_source = db.query(Evidence).filter(
            Evidence.id == assertion.evidence_ids[0],
            Evidence.person_id == owner["person"]["id"],
        ).one()
        assert appointment_source.source_type == "appointment_workspace_review"
        assert appointment_source.evidence_kind == "caregiver_report"
        assert proposals[0]["proposed_change"]["source_quote"] in appointment_source.source_text
        assert db.query(CareAssertion).filter(CareAssertion.source_proposal_id == proposals[0]["id"]).count() == 1
    finally:
        db.close()

    accepted_replay = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[0]['id']}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    assert accepted_replay.status_code == 200, accepted_replay.text
    assert accepted_replay.json()["assertion_id"] == assertion_id
    assert accepted_replay.json()["idempotent_replay"] is True

    # Correction preserves the original proposal and creates a separate pending version.
    corrected = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[1]['id']}/review",
        headers=headers,
        json={
            "decision": "correct",
            "corrected_text": "The clinician asked the family to call in three days.",
            "correction_context": "Caregiver correction: The clinician asked the family to call in three days.",
        },
    )
    assert corrected.status_code == 200, corrected.text
    corrected_body = corrected.json()
    assert corrected_body["proposal"]["status"] == "corrected"
    next_proposal = corrected_body["corrected_proposal"]
    assert next_proposal["status"] == "pending_authorization"
    assert next_proposal["proposed_change"]["source_quote"] == proposals[1]["proposed_change"]["source_quote"]
    assert next_proposal["proposed_change"]["asserted_value"] == "The clinician asked the family to call in three days."

    correction_replay = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[1]['id']}/review",
        headers=headers,
        json={
            "decision": "correct",
            "corrected_text": "The clinician asked the family to call in three days.",
            "correction_context": "Caregiver correction: The clinician asked the family to call in three days.",
        },
    )
    assert correction_replay.status_code == 200, correction_replay.text
    assert correction_replay.json()["corrected_proposal"]["id"] == next_proposal["id"]
    assert correction_replay.json()["idempotent_replay"] is True

    conflicting_correction = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[1]['id']}/review",
        headers=headers,
        json={
            "decision": "correct",
            "corrected_text": "A different corrected statement.",
            "correction_context": "Caregiver correction: A different corrected statement.",
        },
    )
    assert conflicting_correction.status_code == 409, conflicting_correction.text

    # Corrected proposal still requires a separate explicit acceptance.
    corrected_accept = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{next_proposal['id']}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    assert corrected_accept.status_code == 200, corrected_accept.text
    assert corrected_accept.json()["written_to_lcr"] is True

    # Rejection leaves an append-only decision and never creates an assertion.
    rejected = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[2]['id']}/review",
        headers=headers,
        json={"decision": "reject"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["written_to_lcr"] is False
    rejected_replay = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[2]['id']}/review",
        headers=headers,
        json={"decision": "reject"},
    )
    assert rejected_replay.status_code == 200, rejected_replay.text
    assert rejected_replay.json()["idempotent_replay"] is True
    db = TestSessionLocal()
    try:
        rejected_proposal = db.query(AIOutputLCRProposal).filter(
            AIOutputLCRProposal.id == proposals[2]["id"]
        ).one()
        assert rejected_proposal.status == "rejected"
        assert db.query(CareAssertion).filter(
            CareAssertion.source_proposal_id == proposals[2]["id"]
        ).count() == 0
        decisions = db.query(AIOutputLCRDecision).filter(
            AIOutputLCRDecision.proposal_id.in_([proposals[0]["id"], proposals[1]["id"], proposals[2]["id"]])
        ).all()
        assert {item.decision for item in decisions} >= {"accepted", "corrected", "rejected"}
    finally:
        db.close()



def test_fresh_companion_session_retrieves_only_accepted_appointment_assertions(client, monkeypatch):
    import app.api.collaboration as collaboration

    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "outcome_pending",
        "payload": {
            "caregiverReport": "Caregiver reports that the person asked for help with dressing this morning.",
            "clinicianFindings": "Caregiver recorded that the clinician mentioned a follow-up on Tuesday.",
            "decisions": "Family plans to ask about transport arrangements.",
        },
        "change_reason": "Record appointment outcome for Companion retrieval test",
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    prepared = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals",
        headers=headers,
    )
    assert prepared.status_code == 200, prepared.text
    proposals = prepared.json()["items"]

    accepted = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[0]['id']}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_assertion_id = accepted.json()["assertion_id"]

    # Leave the professional-information candidate pending and reject the decision candidate.
    rejected = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[2]['id']}/review",
        headers=headers,
        json={"decision": "reject"},
    )
    assert rejected.status_code == 200, rejected.text

    captured = {}

    def fake_ollama(base, model, messages, options):
        captured["messages"] = messages
        return "The caregiver reported that the person asked for help with dressing this morning."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_ollama)

    response = test_client.post(
        "/api/v1/conversation/message",
        headers=headers,
        json={
            "person_id": owner["person"]["id"],
            "text": "What did the caregiver report about dressing?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_assertion_ids"] == [accepted_assertion_id]
    assert body["written_to_lcr"] is False
    assert body["persistence"] == "conversation_only"
    assert body["session_id"]
    prompt = "\n".join(item["content"] for item in captured["messages"] if item.get("role") in {"system", "user"})
    assert "asked for help with dressing this morning" in prompt
    assert "mentioned a follow-up on Tuesday" not in prompt
    assert "plans to ask about transport arrangements" not in prompt



def test_appointment_workspace_follow_up_is_system_tracked_without_assuming_caregiver_owner(client):
    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    saved = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={
            "status": "follow_up_pending",
            "payload": {
                "visit_purpose": "Check pending result",
                "followUps": [{
                    "action": "Check whether the clinic has released the result",
                    "due": "2026-11-15T09:00:00-05:00",
                    "timezone": "America/New_York",
                    "status": "pending",
                }],
            },
        },
    )
    assert saved.status_code == 201, saved.text

    listed = test_client.get(
        "/api/v1/open-loops?loop_type=follow_up",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    items = listed.json().get("items", [])
    managed = next(
        item for item in items
        if item.get("source_type") == "appointment_workspace_follow_up"
        and item.get("source_id", "").startswith(saved.json()["id"] + ":")
    )
    assert managed["owner_role"] == "unassigned"
    assert managed["owner_label"] is None
    assert managed["deadline_timezone"] == "America/New_York"
    assert managed["status"] == "open"
    assert managed["outcome_status"] == "outcome_unknown"


def test_appointment_follow_up_uses_persistent_open_loop_and_requires_verified_closure(client):
    test_client, owner, other = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    workspace = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"status": "follow_up_pending", "payload": {"visit_purpose": "Review follow-up"}},
    )
    assert workspace.status_code == 201, workspace.text
    record_id = workspace.json()["id"]
    source_id = record_id + ":follow-up-1"
    create_payload = {
        "loop_type": "follow_up",
        "title": "Call the clinic for the test result",
        "description": "Caregiver-recorded appointment follow-up; result is not yet known.",
        "source_type": "appointment_workspace",
        "source_id": source_id,
        "owner_role": "caregiver",
        "owner_label": "Family caregiver",
        "deadline_at": "2026-11-10T12:00:00+00:00",
        "deadline_source": "explicit_user",
        "idempotency_key": "appointment-follow-up:" + source_id,
    }
    created = test_client.post("/api/v1/open-loops", headers=headers, json=create_payload)
    assert created.status_code == 201, created.text
    loop = created.json()
    assert loop["source_type"] == "appointment_workspace"
    assert loop["source_id"] == source_id
    assert loop["owner_label"] == "Family caregiver"
    assert loop["status"] == "open"

    replay = test_client.post("/api/v1/open-loops", headers=headers, json=create_payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == loop["id"]

    updated = test_client.patch(
        f"/api/v1/open-loops/{loop['id']}",
        headers=headers,
        json={
            "title": "Call the clinic and ask for the result",
            "description": "Caregiver updated the wording; the result remains unconfirmed.",
            "owner_role": "caregiver",
            "owner_label": "Family caregiver",
            "deadline_at": "2026-11-11T12:00:00+00:00",
            "deadline_source": "explicit_user",
            "reason": "Caregiver updated appointment follow-up details",
            "idempotency_key": "appointment-follow-up-update:" + source_id,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Call the clinic and ask for the result"

    reported = test_client.post(
        f"/api/v1/open-loops/{loop['id']}/outcomes",
        headers=headers,
        json={
            "text": "Caregiver marked the task done; result has not been independently verified.",
            "evidence": [],
            "idempotency_key": "appointment-follow-up-done:" + source_id,
        },
    )
    assert reported.status_code == 200, reported.text
    assert reported.json()["outcome_status"] == "outcome_reported"
    assert reported.json()["status"] not in {"closed", "cancelled"}

    # A caregiver-reported completion cannot silently close an evidence-backed loop.
    close = test_client.post(
        f"/api/v1/open-loops/{loop['id']}/close",
        headers=headers,
        json={"text": "Close without evidence", "evidence": [], "idempotency_key": "close-without-evidence"},
    )
    assert close.status_code == 409, close.text

    foreign_read = test_client.get(f"/api/v1/open-loops/{loop['id']}", headers=other_headers)
    assert foreign_read.status_code == 404



def test_appointment_workspace_create_and_revision_writes_are_idempotent(client):
    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    create_payload = {
        "status": "preparation",
        "payload": {"visit_purpose": "Review sleep changes", "caregiver_report": "Family-reported"},
        "change_reason": "Initial appointment preparation",
        "idempotency_key": "appt-create-request-001",
    }
    first = test_client.post("/api/v1/appointment-workspaces", headers=headers, json=create_payload)
    replay = test_client.post("/api/v1/appointment-workspaces", headers=headers, json=create_payload)
    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["version_number"] == 1

    changed_create = {**create_payload, "payload": {"visit_purpose": "Different appointment"}}
    conflict = test_client.post("/api/v1/appointment-workspaces", headers=headers, json=changed_create)
    assert conflict.status_code == 409, conflict.text

    record_id = first.json()["id"]
    update_payload = {
        "expected_version": 1,
        "status": "outcome_pending",
        "payload": {"visit_purpose": "Review sleep changes", "caregiver_report": "Follow-up was requested"},
        "change_reason": "Save the appointment outcome",
        "idempotency_key": "appt-update-request-001",
    }
    updated = test_client.patch(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=headers,
        json=update_payload,
    )
    update_replay = test_client.patch(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=headers,
        json=update_payload,
    )
    assert updated.status_code == 200, updated.text
    assert update_replay.status_code == 200, update_replay.text
    assert updated.json()["version_number"] == 2
    assert update_replay.json()["version_number"] == 2

    changed_update = {
        **update_payload,
        "payload": {"visit_purpose": "Changed after initial save"},
    }
    changed_replay = test_client.patch(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=headers,
        json=changed_update,
    )
    assert changed_replay.status_code == 409, changed_replay.text

    revisions = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}/revisions",
        headers=headers,
    )
    assert revisions.status_code == 200, revisions.text
    assert [item["revision_number"] for item in revisions.json()["items"]] == [1, 2]


def test_appointment_accept_replay_does_not_report_rejected_assertion_as_active_lcr_write(client):
    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "outcome_pending",
        "payload": {
            "caregiverReport": "Caregiver reports that breakfast intake was lower yesterday.",
            "actualVisitDate": "2026-11-10T10:00:00+01:00",
        },
        "change_reason": "Record appointment outcome",
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    generated = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals",
        headers=headers,
    )
    assert generated.status_code == 200, generated.text
    proposal = next(
        item for item in generated.json()["items"]
        if item["proposed_change"].get("field") == "caregiver_report"
    )

    accepted = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposal['id']}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    assertion_id = accepted.json()["assertion_id"]
    assert assertion_id

    db = TestSessionLocal()
    try:
        assertion = db.query(CareAssertion).filter(CareAssertion.id == assertion_id).one()
        assertion.assertion_status = "rejected"
        db.commit()
    finally:
        db.close()

    replay = test_client.post(
        f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposal['id']}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["written_to_lcr"] is False
    assert replay.json()["assertion_id"] is None


def test_appointment_follow_up_removal_cancels_not_completes_and_reappearance_reopens(client):
    from app.models.open_loop import OpenLoopRecord, OpenLoopEvent

    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    initial_payload = {
        "follow_ups": [{
            "action": "Confirm the laboratory result with the clinic",
            "owner": "Clinic",
            "owner_role": "professional",
            "due": "2026-11-15T09:00:00-05:00",
            "status": "Open",
        }],
    }
    created = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "preparation", "payload": initial_payload,
        "change_reason": "Record expected result follow-up",
    })
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    db = TestSessionLocal()
    loop = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == session["person"]["id"],
        OpenLoopRecord.source_type == "appointment_workspace_follow_up",
        OpenLoopRecord.source_id.like(f"{record_id}:%"),
    ).one()
    loop_id = loop.id
    assert loop.deadline_at is not None
    assert loop.deadline_source == "explicit_user"
    assert loop.deadline_timezone == "UTC-05:00"
    assert loop.owner_label == "Clinic"
    assert loop.owner_role == "professional"
    assert loop.evidence[0]["reported_owner_role"] == "professional"
    db.close()

    removed = test_client.patch(f"/api/v1/appointment-workspaces/{record_id}", headers=headers, json={
        "expected_version": 1, "payload": {},
        "change_reason": "Follow-up was removed from the revised appointment record",
    })
    assert removed.status_code == 200, removed.text
    db = TestSessionLocal()
    loop = db.query(OpenLoopRecord).filter_by(id=loop_id).one()
    assert loop.status == "cancelled"
    assert loop.closure_result == "cancelled_not_resolved"
    assert "not marked completed" in loop.cancellation_reason
    assert db.query(OpenLoopEvent).filter_by(loop_id=loop_id).count() == 2
    db.close()

    restored = test_client.patch(f"/api/v1/appointment-workspaces/{record_id}", headers=headers, json={
        "expected_version": 2, "payload": initial_payload,
        "change_reason": "The clinic follow-up is required again",
    })
    assert restored.status_code == 200, restored.text
    db = TestSessionLocal()
    loop = db.query(OpenLoopRecord).filter_by(id=loop_id).one()
    assert loop.status == "open"
    assert loop.closed_at is None
    assert loop.cancellation_reason is None
    assert db.query(OpenLoopEvent).filter_by(loop_id=loop_id).count() == 3
    db.close()

    reported_complete = test_client.post("/api/v1/appointment-workspaces", headers=headers, json={
        "status": "preparation",
        "payload": {"follow_ups": [{
            "action": "Confirm the discharge summary reached the primary clinician",
            "owner": "Family caregiver",
            "due": "2026-11-16T10:00:00Z",
            "status": "Completed",
        }]},
        "change_reason": "Record reported follow-up completion",
    })
    assert reported_complete.status_code == 201, reported_complete.text
    completed_workspace_id = reported_complete.json()["id"]
    db = TestSessionLocal()
    reported_loop = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == session["person"]["id"],
        OpenLoopRecord.source_type == "appointment_workspace_follow_up",
        OpenLoopRecord.source_id.like(f"{completed_workspace_id}:%"),
    ).one()
    assert reported_loop.status == "open"
    assert reported_loop.outcome_status == "outcome_reported"
    assert reported_loop.outcome_evidence[0]["epistemic_status"] == "caregiver_reported"
    assert reported_loop.closure_result is None
    db.close()


def test_blocked_appointment_follow_up_creates_and_reassesses_one_evidence_backed_question(client):
    from app.models.open_loop import OpenLoopRecord
    from app.models.uncertainty import OpenQuestion

    test_client, owner, _ = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    db = TestSessionLocal()
    evidence = Evidence(
        person_id=owner["person"]["id"],
        type="appointment_record",
        source_text="Clinic record: the result is not available; the follow-up decision is blocked until the clinic confirms it.",
        uploaded_by_caregiver_id=owner["caregiver"]["id"],
        source_type="caregiver_report",
    )
    db.add(evidence)
    db.commit()
    evidence_id = evidence.id
    db.close()

    payload = {
        "follow_ups": [{
            "action": "Confirm the pending test result with the clinic",
            "owner": "Clinic",
            "owner_role": "professional",
            "status": "blocked",
            "clarification_question": "Has the clinic released the pending test result?",
            "necessity_rationale": "The follow-up is explicitly blocked until the clinic confirms whether the result is available.",
            "decision_dependency": "The caregiver cannot complete the documented appointment follow-up or pass the result to the next care professional until the clinic confirms its availability.",
            "evidence_ids": [evidence_id],
            "intended_answerer_role": "professional",
        }],
    }
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"status": "outcome_pending", "payload": payload, "change_reason": "Record blocked clinic follow-up"},
    )
    assert created.status_code == 201, created.text

    db = TestSessionLocal()
    try:
        loop = db.query(OpenLoopRecord).filter(
            OpenLoopRecord.person_id == owner["person"]["id"],
            OpenLoopRecord.source_type == "appointment_workspace_follow_up",
        ).one()
        question = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == owner["person"]["id"],
            OpenQuestion.subject_type == "appointment_follow_up",
            OpenQuestion.subject_id == loop.id,
            OpenQuestion.question == payload["follow_ups"][0]["clarification_question"],
        ).one()
        assert loop.status == "blocked"
        assert question.necessity == "necessary"
        assert question.status == "open"
        assert question.evidence_ids == [evidence_id]
        assert question.necessity_rationale == payload["follow_ups"][0]["necessity_rationale"]
        assert any(
            item.get("source_kind") == "appointment_workspace_follow_up"
            and item.get("source_id") == loop.id
            for item in question.context
        )
    finally:
        db.close()

    # Removing the explicit blocked state removes the decision dependency.
    # The shared lifecycle must suppress the old request, not leave it active.
    updated = test_client.patch(
        f"/api/v1/appointment-workspaces/{created.json()['id']}",
        headers=headers,
        json={
            "expected_version": 1,
            "status": "outcome_pending",
            "payload": {"follow_ups": [{
                **payload["follow_ups"][0],
                "status": "open",
            }]},
            "change_reason": "Clinic follow-up is no longer blocked",
        },
    )
    assert updated.status_code == 200, updated.text

    db = TestSessionLocal()
    try:
        question = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == owner["person"]["id"],
            OpenQuestion.question == payload["follow_ups"][0]["clarification_question"],
        ).one()
        assert question.status == "closed"
        assert str(question.closed_reason).startswith("automatically_superseded:")
    finally:
        db.close()


def test_blocked_appointment_follow_up_rejects_another_persons_evidence(client):
    test_client, owner, other = client
    db = TestSessionLocal()
    foreign_evidence = Evidence(
        person_id=other["person"]["id"],
        type="appointment_record",
        source_text="Evidence belonging to a different care record.",
        uploaded_by_caregiver_id=other["caregiver"]["id"],
        source_type="caregiver_report",
    )
    db.add(foreign_evidence)
    db.commit()
    foreign_evidence_id = foreign_evidence.id
    db.close()

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={
            "status": "outcome_pending",
            "change_reason": "Attempt cross-record follow-up",
            "payload": {"follow_ups": [{
                "action": "Confirm the pending result",
                "status": "blocked",
                "clarification_question": "Has the result been released?",
                "necessity_rationale": "The action is blocked pending confirmation.",
                "decision_dependency": "The follow-up cannot proceed until the result is confirmed.",
                "evidence_ids": [foreign_evidence_id],
            }]},
        },
    )
    assert response.status_code == 422, response.text


def test_appointment_brief_discloses_when_lcr_fact_history_exceeds_its_projection_limit(client):
    test_client, owner, _ = client
    with engine.begin() as connection:
        for index in range(41):
            timestamp = datetime.now(timezone.utc) - timedelta(minutes=index)
            connection.execute(text("""
                INSERT INTO care_facts
                    (id, person_id, subject_type, subject_id, fact_type, fact_value,
                     epistemic_status, effective_at, recorded_at, created_at, updated_at,
                     created_by_caregiver_id)
                VALUES
                    (:id, :person_id, 'person', :person_id, 'caregiver_report',
                     :value, 'reported', :timestamp, :timestamp, :timestamp, :timestamp,
                     :caregiver_id)
            """), {
                "id": f"appointment-limit-fact-{index}",
                "person_id": owner["person"]["id"],
                "value": f"Saved caregiver report {index}",
                "timestamp": timestamp,
                "caregiver_id": owner["caregiver"]["id"],
            })

    response = test_client.get(
        "/api/v1/appointment-workspaces/preparation-brief?window_days=90",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["facts"]) == 40
    assert body["source_completeness"]["care_facts"] == {
        "returned_count": 40,
        "has_more": True,
        "retrieval_complete": False,
        "record_limit": 40,
    }

def test_appointment_workspace_is_marked_stale_after_person_care_state_changes(client):
    test_client, owner, other = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={
            "status": "preparation",
            "change_reason": "Prepare for the next appointment",
            "payload": {"questions": ["Review recent sleep changes"]},
        },
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]

    # Simulate a later committed care-state mutation in the same person's record.
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_state_revisions
                (id, person_id, target_type, target_id, revision_number, change_kind, evidence_ids, changed_at)
            VALUES
                ('later-owner-revision', :person_id, 'care_events', 'later-event',
                 1, 'created', '[]', :changed_at)
        """), {
            "person_id": owner["person"]["id"],
            "changed_at": datetime.now(timezone.utc) + timedelta(minutes=1),
        })

    response = test_client.get(
        f"/api/v1/appointment-workspaces/{workspace_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    freshness = response.json()["source_freshness"]
    assert freshness["status"] == "stale"
    assert freshness["reason"] == "care_record_changed_after_workspace_save"


def test_appointment_workspace_freshness_watermark_is_person_scoped(client):
    test_client, owner, other = client
    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={
            "status": "preparation",
            "change_reason": "Prepare for the next appointment",
            "payload": {"questions": ["Review recent sleep changes"]},
        },
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]

    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO care_state_revisions
                (id, person_id, target_type, target_id, revision_number, change_kind, changed_at)
            VALUES
                ('later-other-person-revision', :person_id, 'care_events', 'other-event',
                 1, 'created', :changed_at)
        """), {
            "person_id": other["person"]["id"],
            "changed_at": datetime.now(timezone.utc) + timedelta(minutes=1),
        })

    response = test_client.get(
        f"/api/v1/appointment-workspaces/{workspace_id}", headers=headers
    )
    assert response.status_code == 200, response.text
    freshness = response.json()["source_freshness"]
    assert freshness["status"] == "current"
    assert freshness["reason"] is None


def test_appointment_workspace_reports_stale_after_later_care_state_revision(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={
            "status": "preparation",
            "payload": {"visit_purpose": "Review recent sleep changes"},
            "change_reason": "Initial preparation",
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["source_freshness"]["status"] == "current"
    assert record["source_freshness"]["basis"] == "care_state_revision_watermark"

    db = TestSessionLocal()
    try:
        db.add(CareStateRevision(
            id="later-care-state-revision",
            person_id=session["person"]["id"],
            target_type="care_assertion",
            target_id="changed-care-assertion",
            revision_number=1,
            change_kind="created",
            after_state='{"asserted_value":"New caregiver-reported change"}',
            evidence_ids=[],
            changed_by_caregiver_id=session["caregiver"]["id"],
            changed_at=datetime.utcnow() + timedelta(seconds=5),
        ))
        db.commit()
    finally:
        db.close()

    read = test_client.get(
        f"/api/v1/appointment-workspaces/{record['id']}",
        headers=headers,
    )
    assert read.status_code == 200, read.text
    freshness = read.json()["source_freshness"]
    assert freshness["status"] == "stale"
    assert freshness["reason"] == "care_record_changed_after_workspace_save"
    assert freshness["latest_care_change_at"] is not None

    # A stale preparation remains retrievable as historical context; it is not
    # silently rewritten or deleted when later care information is recorded.
    assert read.json()["payload"]["visit_purpose"] == "Review recent sleep changes"


def test_appointment_workspace_freshness_watermark_is_person_scoped(client):
    test_client, owner, other = client
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=owner_headers,
        json={"payload": {"visit_purpose": "Owner's appointment preparation"}},
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    db = TestSessionLocal()
    try:
        db.add(CareStateRevision(
            id="other-person-care-state-revision",
            person_id=other["person"]["id"],
            target_type="care_assertion",
            target_id="other-private-change",
            revision_number=1,
            change_kind="created",
            after_state='{"asserted_value":"Private other-person care change"}',
            evidence_ids=[],
            changed_by_caregiver_id=other["caregiver"]["id"],
            changed_at=datetime.utcnow() + timedelta(seconds=5),
        ))
        db.commit()
    finally:
        db.close()

    response = test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=owner_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["source_freshness"]["status"] == "current"
    assert "Private other-person care change" not in response.text
    assert test_client.get(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=other_headers,
    ).status_code == 404


def test_appointment_workspace_reports_unknown_when_revision_history_is_unavailable(client, monkeypatch):
    from app.api import appointment_workspace as workspace_api

    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"payload": {"visit_purpose": "Review care changes"}},
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    real_inspect = workspace_api.inspect

    class InspectorWithoutRevisionHistory:
        def has_table(self, table_name):
            if table_name == "care_state_revisions":
                return False
            return real_inspect(engine).has_table(table_name)

    monkeypatch.setattr(workspace_api, "inspect", lambda bind: InspectorWithoutRevisionHistory())
    response = test_client.get(f"/api/v1/appointment-workspaces/{record_id}", headers=headers)
    assert response.status_code == 200, response.text
    freshness = response.json()["source_freshness"]
    assert freshness["status"] == "unknown"
    assert freshness["reason"] == "care_state_revision_history_unavailable"



def test_documents_only_member_cannot_read_or_write_appointment_workspace(client):
    test_client, session, _ = client
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    created = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"payload": {"visit_purpose": "Private appointment preparation"}},
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]

    db = TestSessionLocal()
    try:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
        ).one()
        grant.can_manage_members = False
        grant.access_scope = "documents_only"
        grant.permissions = ["view_documents"]
        db.commit()
    finally:
        db.close()

    for path in (
        "/api/v1/appointment-workspaces",
        f"/api/v1/appointment-workspaces/{record_id}",
        f"/api/v1/appointment-workspaces/{record_id}/outcome-draft",
        f"/api/v1/appointment-workspaces/{record_id}/revisions",
    ):
        denied_read = test_client.get(path, headers=headers)
        assert denied_read.status_code == 403, (path, denied_read.text)

    denied_write = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=headers,
        json={"payload": {"visit_purpose": "Must remain inaccessible"}},
    )
    assert denied_write.status_code == 403, denied_write.text

    denied_update = test_client.patch(
        f"/api/v1/appointment-workspaces/{record_id}",
        headers=headers,
        json={"expected_version": 1, "payload": {"visit_purpose": "Unauthorized edit"}, "change_reason": "Unauthorized edit attempt"},
    )
    assert denied_update.status_code == 403, denied_update.text


def test_appointment_preparation_brief_includes_person_scoped_unresolved_follow_ups(client):
    test_client, owner, other = client
    db = TestSessionLocal()
    try:
        db.add_all([
            OpenLoopRecord(
                person_id=owner["person"]["id"],
                caregiver_id=owner["caregiver"]["id"],
                loop_type="follow_up",
                title="Call clinic about pending result",
                description="Check whether the test result is available",
                status="open",
                priority="high",
                source_fingerprint="a" * 64,
                owner_role="unassigned",
                owner_label=None,
            ),
            OpenLoopRecord(
                person_id=owner["person"]["id"],
                caregiver_id=owner["caregiver"]["id"],
                loop_type="follow_up",
                title="Clarify transport arrangement",
                description="Transport arrangement still needs clarification",
                status="blocked",
                source_fingerprint="b" * 64,
            ),
            OpenLoopRecord(
                person_id=owner["person"]["id"],
                caregiver_id=owner["caregiver"]["id"],
                loop_type="follow_up",
                title="Already completed follow-up",
                description="This item is closed",
                status="closed",
                source_fingerprint="c" * 64,
            ),
            OpenLoopRecord(
                person_id=other["person"]["id"],
                caregiver_id=other["caregiver"]["id"],
                loop_type="follow_up",
                title="Other person's private follow-up",
                description="Must not appear in this visit brief",
                status="open",
                source_fingerprint="d" * 64,
            ),
        ])
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get("/api/v1/appointment-workspaces/preparation-brief", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    titles = [item["title"] for item in body["open_loops"]]
    assert "Call clinic about pending result" in titles
    assert "Clarify transport arrangement" in titles
    assert titles.index("Call clinic about pending result") < titles.index("Clarify transport arrangement")
    assert "Already completed follow-up" not in titles
    assert "Other person's private follow-up" not in titles
    assert body["source_coverage"]["open_loops"] == "available"
    assert body["source_completeness"]["open_loops"]["retrieval_complete"] is True
    assert all(item["interpretation_limit"] for item in body["open_loops"])


def test_appointment_preparation_brief_selects_nearest_upcoming_visit(client):
    test_client, owner, other = client
    now = datetime.now(timezone.utc)
    db = TestSessionLocal()
    try:
        db.add_all([
            Appointment(
                person_id=owner["person"]["id"],
                created_by_caregiver_id=owner["caregiver"]["id"],
                title="Later specialist visit",
                scheduled_at=now + timedelta(days=8),
                status="scheduled",
            ),
            Appointment(
                person_id=owner["person"]["id"],
                created_by_caregiver_id=owner["caregiver"]["id"],
                title="Next clinic visit",
                scheduled_at=now + timedelta(days=2),
                provider="Family doctor",
                location="Clinic",
                status="scheduled",
            ),
            Appointment(
                person_id=owner["person"]["id"],
                created_by_caregiver_id=owner["caregiver"]["id"],
                title="Past visit still marked scheduled",
                scheduled_at=now - timedelta(days=2),
                status="scheduled",
            ),
            Appointment(
                person_id=other["person"]["id"],
                created_by_caregiver_id=other["caregiver"]["id"],
                title="Other person's appointment",
                scheduled_at=now + timedelta(days=1),
                status="scheduled",
            ),
        ])
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {owner['access_token']}"}
    response = test_client.get("/api/v1/appointment-workspaces/preparation-brief", headers=headers)
    assert response.status_code == 200, response.text
    next_visit = response.json()["next_appointment"]
    assert next_visit["title"] == "Next clinic visit"
    assert next_visit["provider"] == "Family doctor"
    assert next_visit["location"] == "Clinic"
    assert next_visit["status"] == "scheduled"
    assert "Other person's appointment" not in response.text


def test_appointment_follow_up_is_shared_across_authorised_caregivers_and_keeps_appointment_link(client):
    from app.models.open_loop import OpenLoopRecord

    test_client, owner, other_person = client
    now = datetime.now(timezone.utc)
    db = TestSessionLocal()
    appointment = Appointment(
        person_id=owner["person"]["id"],
        created_by_caregiver_id=owner["caregiver"]["id"],
        title="Primary care review",
        scheduled_at=now + timedelta(days=3),
        status="scheduled",
    )
    db.add(appointment)
    db.commit()
    appointment_id = appointment.id
    db.close()

    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    workspace_response = test_client.post(
        "/api/v1/appointment-workspaces",
        headers=owner_headers,
        json={
            "appointment_id": appointment_id,
            "status": "follow_up_pending",
            "payload": {
                "follow_ups": [{
                    "id": "pending-clinic-result",
                    "action": "Check whether the clinic released the result",
                    "status": "open",
                }],
            },
            "change_reason": "Record appointment follow-up",
        },
    )
    assert workspace_response.status_code == 201, workspace_response.text
    workspace_id = workspace_response.json()["id"]

    # A second caregiver receives an explicit server-side grant for this same
    # person. Their own bearer session must see the same persisted workspace.
    db = TestSessionLocal()
    shared_caregiver = Caregiver(
        name="Authorised second caregiver",
        relationship="family",
        person_id=owner["person"]["id"],
    )
    db.add(shared_caregiver)
    db.flush()
    db.add(CaregiverAccessGrant(
        person_id=owner["person"]["id"],
        caregiver_id=shared_caregiver.id,
        role="family",
        access_scope="full_record",
        status="active",
        can_manage_members=False,
        permissions=["view_lcr", "add_evidence"],
        granted_by_caregiver_id=owner["caregiver"]["id"],
    ))
    raw_token = str(uuid.uuid4())
    db.add(AnonymousSession(
        person_id=owner["person"]["id"],
        caregiver_id=shared_caregiver.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(days=1),
    ))
    db.commit()
    db.close()
    shared_headers = {"Authorization": f"Bearer {raw_token}"}

    shared_read = test_client.get(
        f"/api/v1/appointment-workspaces/{workspace_id}",
        headers=shared_headers,
    )
    assert shared_read.status_code == 200, shared_read.text
    assert shared_read.json()["appointment_id"] == appointment_id

    # A second authorised caregiver editing the same source follow-up must
    # reconcile the existing person-level Open Loop, not create a duplicate.
    updated = test_client.patch(
        f"/api/v1/appointment-workspaces/{workspace_id}",
        headers=shared_headers,
        json={
            "expected_version": 1,
            "status": "follow_up_pending",
            "payload": {
                "follow_ups": [{
                    "id": "pending-clinic-result",
                    "action": "Check whether the clinic released the result",
                    "status": "open",
                }],
            },
            "change_reason": "Second caregiver reviewed the same follow-up",
        },
    )
    assert updated.status_code == 200, updated.text

    shared_brief = test_client.get(
        "/api/v1/appointment-workspaces/preparation-brief",
        headers=shared_headers,
    )
    assert shared_brief.status_code == 200, shared_brief.text
    linked = [
        item for item in shared_brief.json()["open_loops"]
        if item.get("source_workspace_id") == workspace_id
    ]
    assert len(linked) == 1
    assert linked[0]["appointment_id"] == appointment_id
    assert linked[0]["source_reference"].startswith(workspace_id + ":")

    db = TestSessionLocal()
    try:
        loops = db.query(OpenLoopRecord).filter(
            OpenLoopRecord.person_id == owner["person"]["id"],
            OpenLoopRecord.source_type == "appointment_workspace_follow_up",
            OpenLoopRecord.source_id.like(f"{workspace_id}:%"),
        ).all()
        assert len(loops) == 1
        assert loops[0].evidence[0]["appointment_id"] == appointment_id
    finally:
        db.close()

    # A caregiver with no grant for this person cannot read the appointment.
    denied = test_client.get(
        f"/api/v1/appointment-workspaces/{workspace_id}",
        headers={"Authorization": f"Bearer {other_person['access_token']}"},
    )
    assert denied.status_code == 404
