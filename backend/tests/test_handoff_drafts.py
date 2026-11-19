import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import CareEvent, Caregiver, Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.collaboration import EvidenceUpload
from app.models.action import Action
from app.models.care_continuity import EvidenceFreshnessPolicy, EvidenceFreshnessAssessment, EvidenceReviewRecord, EvidenceClaimAssessment, EvidenceCompletenessPolicy, EvidenceCompletenessAssessment, CareAssertion
from app.models.caregiver_intelligence import CaregiverProfile, CaregiverRole, CaregiverResponsibility, CaregiverHandoff
from app.core.caregiver_enums import CaregiverRoleType, ResponsibilityStatus, ResponsibilityAcceptanceStatus, HandoffAcceptanceStatus
from app.services.caregiver_profile_identity import ensure_caregiver_profile_for_session
from app.models.time_location import Appointment
from app.models.uncertainty import OpenQuestion

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
                created_by_caregiver_id VARCHAR(255),
                review_notes TEXT
            )
        """))
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Care recipient", "caregiver_name": "Family caregiver"},
        )
        assert created.status_code == 201, created.text
        yield test_client, created.json()
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def test_draft_requires_session(client):
    test_client, _ = client
    response = test_client.post("/api/v1/handoff-drafts/prepare", json={})
    assert response.status_code == 401, response.text


@pytest.mark.parametrize("audience, expected_sections", [
    ("family", {"start_here", "daily_routine", "health_medication", "next_actions", "receipt"}),
    ("professional", {"transition_summary", "provenance", "medication_reconciliation", "action_register", "integrity", "receipt"}),
])
def test_draft_returns_audience_specific_template_and_explicit_unassessed_sections(client, audience, expected_sections):
    test_client, session = client
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "audience": audience, "window_days": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["audience"] == audience
    sections = {section["key"]: section for section in body["template_sections"]}
    assert expected_sections.issubset(sections)
    assert all(section["required_fields"] for section in body["template_sections"])
    # The canonical medication projection is connected; allergy coverage remains unavailable.
    assert body["source_statuses"]["medications"]["status"] == "empty"
    assert body["source_statuses"]["allergies"]["status"] == "unavailable"
    if audience == "professional":
        assert sections["medication_reconciliation"]["coverage_status"] == "no_records_returned"
    else:
        assert sections["health_medication"]["coverage_status"] == "no_records_returned"
    assert "allergy information is not available from a connected source" in (
        sections["medication_reconciliation"]["coverage_note"]
        if audience == "professional" else sections["health_medication"]["coverage_note"]
    ).lower()
    assert all("still requires review" in section["coverage_note"] for section in body["template_sections"])


def test_draft_is_read_only_and_only_includes_records_for_session_person(client):
    test_client, session = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :fact_type, :fact_value, :status, :created_at)"""),
            {
                "id": "owned-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "fact_type": "caregiver_observation",
                "fact_value": "Breakfast was mostly uneaten",
                "status": "reported",
                "created_at": "2026-10-26 08:00:00",
            },
        )
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :fact_type, :fact_value, :status, :created_at)"""),
            {
                "id": "other-person-fact",
                "person_id": "another-person",
                "subject_type": "care_recipient",
                "fact_type": "private",
                "fact_value": "MUST NOT LEAK",
                "status": "reported",
                "created_at": "2026-10-26 08:00:00",
            },
        )
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "family_transition", "window_days": 90},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["creates_handoff"] is False
    assert body["updates_lcr"] is False
    assert any(item["source_id"] == "owned-fact" for item in body["source_references"])
    assert all(item["source_id"] != "other-person-fact" for item in body["source_references"])
    assert "MUST NOT LEAK" not in response.text
    assert body["source_statuses"]["community"]["status"] == "not_authorized"
    assert body["retrieval_complete"] is False, "unavailable required schemas must keep the handoff visibly incomplete"
    blocking_limitations = {
        item["source"] for item in body["limitations"]
        if item["status"] in {"unavailable", "failed", "partial"}
    }
    assert blocking_limitations == {"decision_outcomes", "care_timeline_entries", "allergies"}
    assert "community" not in blocking_limitations, "disabled Community is not an authorized care-record source and must not block retrieval"
    assert body["source_statuses"]["documents"]["status"] == "empty"
    with engine.connect() as connection:
        saved = connection.execute(
            text("SELECT fact_value FROM care_facts WHERE id = 'owned-fact'")
        ).scalar_one()
    assert saved == "Breakfast was mostly uneaten"


def test_draft_includes_person_scoped_events_appointments_and_only_actions_from_current_caregivers_evidence(client):
    test_client, session = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        other_caregiver = Caregiver(
            id="other-caregiver-same-person",
            name="Other caregiver",
            relationship="family",
            person_id=person_id,
        )
        db.add(other_caregiver)
        db.flush()
        origin_evidence = Evidence(
            id="handoff-origin-evidence",
            person_id=person_id,
            type="document",
            source_text="Original clinic note",
            uploaded_by_caregiver_id=caregiver_id,
            original_source_reference="clinic-note/hand-off-test",
        )
        db.add(origin_evidence)
        db.flush()
        own_evidence = Evidence(
            id="own-evidence",
            person_id=person_id,
            type="document",
            source_text="Current caregiver's source text",
            uploaded_by_caregiver_id=caregiver_id,
            parent_evidence_id="handoff-origin-evidence",
        )
        sibling_copy = Evidence(
            id="handoff-sibling-copy",
            person_id=person_id,
            type="forwarded_message",
            source_text="Forwarded clinic note",
            uploaded_by_caregiver_id=caregiver_id,
            original_source_reference="clinic-note/hand-off-test",
        )
        other_evidence = Evidence(
            id="other-evidence",
            person_id=person_id,
            type="document",
            source_text="Private source text from another caregiver",
            uploaded_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([own_evidence, sibling_copy, other_evidence])
        db.flush()
        db.add_all([
            Action(
                id="own-action",
                person_id=person_id,
                source_evidence_id=own_evidence.id,
                source_passage="Call the clinic this week",
                source_authority="caregiver",
                normalized_action="Call the clinic this week",
                original_text="Call the clinic this week",
                created_by_caregiver_id=caregiver_id,
                status="pending_confirmation",
                deadline=datetime.now(timezone.utc) + timedelta(days=2),
            ),
            Action(
                id="other-action",
                person_id=person_id,
                source_evidence_id=other_evidence.id,
                source_passage="Private instruction",
                source_authority="caregiver",
                normalized_action="PRIVATE OTHER CAREGIVER ACTION",
                original_text="PRIVATE OTHER CAREGIVER ACTION",
                created_by_caregiver_id=other_caregiver.id,
                status="pending_confirmation",
            ),
        ])
        db.add(CareEvent(
            id="recent-event",
            person_id=person_id,
            event_type="care_observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=2),
            title="Recent recorded event",
            description="Reported change to discuss",
            created_by_caregiver_id=caregiver_id,
        ))
        db.add(Appointment(
            id="next-appointment",
            person_id=person_id,
            title="Clinic appointment",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
        ))
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30, "appointment_id": "next-appointment"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    references = {item["source_id"]: item for item in body["source_references"]}
    assert "recent-event" in references
    assert "next-appointment" in references
    assert "own-action" in references
    assert "other-action" not in references
    assert "PRIVATE OTHER CAREGIVER ACTION" not in response.text
    assert references["recent-event"]["epistemic_status"] == "not_recorded"
    assert references["recent-event"]["record_status"] == "recorded"
    assert references["next-appointment"]["event_at"] is not None
    assert references["next-appointment"]["recorded_at"] is not None
    assert references["next-appointment"]["event_at"] != references["next-appointment"]["recorded_at"]
    assert references["own-action"]["due_at"] is not None
    assert references["own-action"]["record_status"] == "pending_confirmation"
    assert body["source_statuses"]["appointments"]["status"] == "available"
    assert body["source_statuses"]["care_events"]["status"] == "available"
    assert body["source_statuses"]["actions"]["status"] == "available"
    lineage = body["evidence_lineage"]
    assert any(
        group["source_independence_status"] == "shared_origin"
        and "own-evidence" in group["evidence_ids"]
        for group in lineage["groups"]
    ), lineage
    assert lineage["status"] == "assessed"


def test_current_lcr_fact_outside_time_window_is_retained_with_separate_effective_and_recorded_times(client):
    test_client, session = client
    person_id = session["person"]["id"]
    token = session["access_token"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value,
                 epistemic_status, effective_at, recorded_at, created_at, superseded_by_fact_id)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value,
                        :epistemic_status, :effective_at, :recorded_at, :created_at, NULL)"""),
            {
                "id": "longstanding-current-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "subject_id": person_id,
                "fact_type": "communication_preference",
                "fact_value": "Allow extra time to respond",
                "epistemic_status": "reported",
                "effective_at": "2020-01-01 00:00:00",
                "recorded_at": "2020-01-01 00:00:00",
                "created_at": "2020-01-01 00:00:00",
            },
        )
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value,
                 epistemic_status, effective_at, recorded_at, created_at, superseded_by_fact_id)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value,
                        :epistemic_status, :effective_at, :recorded_at, :created_at, :superseded_by_fact_id)"""),
            {
                "id": "old-superseded-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "subject_id": person_id,
                "fact_type": "communication_preference",
                "fact_value": "Old communication preference",
                "epistemic_status": "reported",
                "effective_at": "2019-01-01 00:00:00",
                "recorded_at": "2019-01-01 00:00:00",
                "created_at": "2019-01-01 00:00:00",
                "superseded_by_fact_id": "longstanding-current-fact",
            },
        )
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "family_transition", "window_days": 7},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    fact = next(item for item in body["source_references"] if item["source_id"] == "longstanding-current-fact")
    assert all(item["source_id"] != "old-superseded-fact" for item in body["source_references"])
    assert fact["event_at"].startswith("2020-01-01")
    assert fact["recorded_at"].startswith("2020-01-01")
    assert fact["stale_warning"] is True
    assert fact["details"]["fact_lifecycle"] == "current_in_lcr"


@pytest.mark.parametrize("window_days", [0, -1, 91, 999])
def test_draft_rejects_unbounded_time_window(client, window_days):
    test_client, session = client
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"window_days": window_days},
    )
    assert response.status_code == 422, response.text


def test_handoff_explicit_evidence_reference_parser_keeps_only_declared_links():
    from app.api.handoff_drafts import _explicit_evidence_ids

    assert _explicit_evidence_ids({
        "evidence_ids": '["evidence-a", "evidence-b"]',
        "source_evidence_id": "evidence-c",
    }) == ["evidence-a", "evidence-b", "evidence-c"]
    assert _explicit_evidence_ids({"evidence_ids": None, "source_evidence_id": None}) == []
    assert _explicit_evidence_ids({"evidence_ids": "evidence-d"}) == ["evidence-d"]
    assert _explicit_evidence_ids({
        "evidence_id": "evidence-e",
        "evidence_links": '[{"evidence_id": "evidence-f", "relationship": "neutral"}]',
    }) == ["evidence-e", "evidence-f"]



def test_professional_handoff_includes_freshness_policy_and_assessment_provenance(client):
    test_client, session = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        source_time = datetime.now(timezone.utc) - timedelta(days=45)
        evidence = Evidence(
            id="handoff-freshness-evidence",
            person_id=person_id,
            type="document",
            source_text="A dated care document",
            uploaded_by_caregiver_id=caregiver_id,
            occurred_at=source_time,
            collected_at=source_time,
        )
        policy = EvidenceFreshnessPolicy(
            id="handoff-freshness-policy",
            person_id=person_id,
            domain_key="document_history",
            policy_version="v1",
            max_age_days=30,
            historical_after_days=180,
            rationale="Explicit caregiver-selected review boundary.",
            created_by_caregiver_id=caregiver_id,
            created_at=source_time,
        )
        assessment = EvidenceFreshnessAssessment(
            id="handoff-freshness-assessment",
            person_id=person_id,
            evidence_id=evidence.id,
            policy_id=policy.id,
            policy_version=policy.policy_version,
            freshness_status="needs_review",
            source_time=source_time,
            source_time_kind="event_occurrence",
            assessed_at=source_time,
            age_days=45,
            rationale="The explicit review boundary has elapsed; re-confirm before relying on currentness.",
            assessed_by_caregiver_id=caregiver_id,
        )
        claim = CareAssertion(
            id="handoff-review-claim",
            person_id=person_id,
            subject_type="care_recipient",
            subject_id=person_id,
            field="hydration",
            assertion_kind="current_state",
            asserted_value="Caregiver reported reduced fluid intake.",
            source_label="Daily Care report",
            source_text="Caregiver reported reduced fluid intake.",
            source_context={
                "kind": "caregiver_reviewed_document_candidate",
                "authority": "caregiver_reviewed_report_not_independently_verified",
                "document_candidate": {"source_attribution": "caregiver_reported", "conditionality": "conditional"},
            },
            evidence_ids=[evidence.id],
            assertion_status="reported",
            epistemic_state="reported",
            created_by_caregiver_id=caregiver_id,
            recorded_at=datetime.now(timezone.utc),
        )
        source_review = EvidenceReviewRecord(
            id="handoff-source-review",
            evidence_id=evidence.id,
            person_id=person_id,
            review_type="reliability",
            outcome="unverified",
            rationale="The original author has not yet been independently confirmed.",
            criteria=[
                {"criterion": "source attribution", "result": "incomplete", "rationale": "Author not confirmed."},
                {"criterion": "independent corroboration", "result": "none_linked", "rationale": "No independent source linked."},
                {"criterion": "temporal context", "result": "specified", "rationale": "Document date is present."},
                {"criterion": "evidence freshness", "result": "current", "rationale": "Within the selected review interval."},
                {"criterion": "record completeness", "result": "partial", "rationale": "Some source details are missing."},
            ],
            policy_version="evidence-review-v1",
            source_attribution_snapshot="{}",
            reviewed_by_caregiver_id=caregiver_id,
            created_at=source_time,
        )
        claim_assessment = EvidenceClaimAssessment(
            id="handoff-claim-assessment",
            person_id=person_id,
            claim_id=claim.id,
            claim_version=1,
            status="unverified",
            rationale="The caregiver report is retained but not independently verified.",
            policy_version="claim-assessment-v1",
            evidence_links=[{
                "evidence_id": evidence.id,
                "relationship": "neutral",
                "source_passage": "A dated care document",
                "scope_note": "Source context remains unverified.",
            }],
            reviewed_by_caregiver_id=caregiver_id,
            created_at=source_time,
        )
        completeness_policy = EvidenceCompletenessPolicy(
            id="handoff-completeness-policy",
            person_id=person_id,
            domain_key="document_history",
            purpose_key="professional_handoff",
            policy_version="completeness-v1",
            required_fields=["author", "date"],
            rationale="Required source fields for this briefing purpose.",
            created_by_caregiver_id=caregiver_id,
            created_at=source_time,
        )
        completeness_assessment = EvidenceCompletenessAssessment(
            id="handoff-completeness-assessment",
            person_id=person_id,
            policy_id=completeness_policy.id,
            policy_version=completeness_policy.policy_version,
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
            assessed_at=source_time,
        )
        primary_profile = ensure_caregiver_profile_for_session(
            db,
            person_id=person_id,
            session_caregiver_id=caregiver_id,
            name="Family caregiver",
            relationship="Family",
        )
        backup_caregiver = Caregiver(
            person_id=person_id,
            name="Backup caregiver",
            relationship="Family",
        )
        db.add(backup_caregiver)
        db.flush()
        backup_profile = ensure_caregiver_profile_for_session(
            db,
            person_id=person_id,
            session_caregiver_id=backup_caregiver.id,
            name="Backup caregiver",
            relationship="Family",
        )
        role = CaregiverRole(
            person_id=person_id,
            caregiver_id=primary_profile.id,
            role_type=CaregiverRoleType.APPOINTMENT_COORDINATOR,
            scope="Coordinate appointments and transport.",
            effective_start=datetime.now(timezone.utc),
            is_current=True,
            assignment_source="caregiver_self_reported",
        )
        responsibility = CaregiverResponsibility(
            person_id=person_id,
            caregiver_id=primary_profile.id,
            responsibility_type="appointment_follow_up",
            scope="Confirm the next clinic appointment.",
            effective_start=datetime.now(timezone.utc),
            status=ResponsibilityStatus.ACTIVE,
            acceptance_status=ResponsibilityAcceptanceStatus.EXPLICIT,
            assignment_source="caregiver_self_reported",
        )
        handoff = CaregiverHandoff(
            person_id=person_id,
            from_caregiver_id=primary_profile.id,
            to_caregiver_id=backup_profile.id,
            responsibility_type="appointment_follow_up",
            scope="Confirm the next clinic appointment.",
            timestamp=source_time,
            effective_time=source_time,
            context="The primary caregiver transferred appointment follow-up.",
            acceptance_status=HandoffAcceptanceStatus.ACCEPTED,
            acceptance_evidence="The backup caregiver accepted the transfer.",
            is_completed=False,
            provenance="test:caregiver-responsibility-transfer",
            created_at=source_time,
        )
        db.add_all([role, responsibility, handoff])
        appointment = Appointment(
            id="handoff-freshness-appointment",
            person_id=person_id,
            title="Medication review appointment",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=4),
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([evidence, policy, assessment, claim, source_review, claim_assessment, completeness_policy, completeness_assessment, appointment])
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30, "appointment_id": "handoff-freshness-appointment"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    refs = {item["source_id"]: item for item in body["source_references"]}
    assert "handoff-freshness-assessment" in refs
    assert "handoff-freshness-policy" in refs
    assert refs["handoff-freshness-assessment"]["details"]["freshness_status"] == "needs_review"
    assert refs["handoff-freshness-assessment"]["details"]["policy_version"] == "v1"
    assert refs["handoff-freshness-policy"]["details"]["domain_key"] == "document_history"
    assert refs["handoff-freshness-policy"]["details"]["policy_version"] == "v1"
    assert "handoff-completeness-policy" in refs
    assert refs["handoff-completeness-policy"]["details"]["purpose_key"] == "professional_handoff"
    assert "handoff-completeness-assessment" in refs
    assert refs["handoff-completeness-assessment"]["details"]["assessment_status"] == "partial"
    assert refs["handoff-completeness-assessment"]["details"]["semantic_sufficiency"] == "not_automatically_verified"
    assert body["source_statuses"]["evidence_completeness_policies"]["status"] == "available"
    assert body["source_statuses"]["evidence_completeness_assessments"]["status"] == "available"
    assert "handoff-source-review" in refs
    assert refs["handoff-source-review"]["source_table"] == "evidence_review_records"
    assert refs["handoff-source-review"]["details"]["outcome"] == "unverified"
    assert refs["handoff-review-claim"]["details"]["source_context"]["document_candidate"]["source_attribution"] == "caregiver_reported"
    assert refs["handoff-review-claim"]["details"]["source_context"]["document_candidate"]["conditionality"] == "conditional"
    assert refs["handoff-review-claim"]["details"]["source_context"]["document_candidate"]["source_attribution"] == "caregiver_reported"
    assert "source_excerpt" not in refs["handoff-review-claim"]["details"]["source_context"]["document_candidate"]
    assert "source_span_start" not in refs["handoff-review-claim"]["details"]["source_context"]["document_candidate"]
    assert "handoff-claim-assessment" in refs
    assert refs["handoff-claim-assessment"]["source_table"] == "evidence_claim_assessments"
    assert refs["handoff-claim-assessment"]["details"]["status"] == "unverified"
    assert body["source_statuses"]["evidence_review_records"]["status"] == "available"
    assert body["source_statuses"]["evidence_claim_assessments"]["status"] == "available"
    assert body["source_statuses"]["evidence_freshness_assessments"]["status"] == "available"
    assert body["source_statuses"]["evidence_freshness_policies"]["status"] == "available"
    assert body["source_statuses"]["caregiver_profiles"]["status"] == "available"
    assert body["source_statuses"]["caregiver_roles"]["status"] == "available"
    assert body["source_statuses"]["caregiver_responsibilities"]["status"] == "available"
    assert body["source_statuses"]["caregiver_handoffs"]["status"] == "available"
    assert any(item["source_table"] == "caregiver_roles" for item in refs.values())
    assert any(item["source_table"] == "caregiver_responsibilities" for item in refs.values())
    accepted_handoff = next(item for item in refs.values() if item["source_table"] == "caregiver_handoffs")
    assert accepted_handoff["details"]["acceptance_status"] == "accepted"
    assert accepted_handoff["details"]["is_completed"] is False


def test_preparation_response_and_audit_event_share_the_same_draft_id(client):
    from app.models.privacy_delivery import AuditLogEvent

    test_client, session = client
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    draft_id = response.json()["draft_id"]

    db = TestSessionLocal()
    try:
        audit = db.query(AuditLogEvent).filter(
            AuditLogEvent.action == "handoff_draft.prepare",
            AuditLogEvent.target_type == "handoff_draft",
            AuditLogEvent.target_id == draft_id,
        ).one()
        assert audit.person_id == session["person"]["id"]
        assert audit.caregiver_id == session["caregiver"]["id"]
    finally:
        db.close()


def test_daily_care_adapter_returns_only_persisted_person_scoped_caregiver_reports(client):
    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        evidence = Evidence(
            id="daily-care-source-evidence",
            person_id=person_id,
            type="caregiver_observation",
            source_text="Caregiver reported that lunch was only partly eaten.",
            uploaded_by_caregiver_id=caregiver_id,
            reported_by_caregiver_id=caregiver_id,
            reported_by_label="Family caregiver",
        )
        event = CareEvent(
            id="daily-care-source-event",
            person_id=person_id,
            event_type="structured_daily_capture",
            status="recorded",
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=3),
            occurred_at_timezone="Africa/Lagos",
            title="Lunch observation",
            description="Lunch was only partly eaten.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([evidence, event])
        db.flush()
        db.add_all([
            CaptureAnalysis(
                id="daily-care-review-analysis",
                person_id=person_id,
                caregiver_id=caregiver_id,
                source_evidence_id=evidence.id,
                source_event_id=event.id,
                source_text="Lunch was only partly eaten.",
                extraction_status="available",
                candidates={"kind": "daily_capture_review_v1"},
                review_history=[{
                    "decision": "correct",
                    "original_candidate": {"text": "Lunch was only partly eaten."},
                    "reviewed_candidate": {"text": "Lunch was mostly eaten."},
                    "reviewed_by_caregiver_id": caregiver_id,
                }],
            ),
            CareAssertion(
                id="daily-care-reviewed-assertion",
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field="food_intake",
                assertion_kind="caregiver_reported_observation",
                asserted_value="Lunch was mostly eaten.",
                source_event_id=event.id,
                source_label="Daily Care review",
                source_text="Lunch was mostly eaten.",
                event_time_text=event.occurred_at.isoformat(),
                evidence_ids=[evidence.id],
                assertion_status="caregiver_reviewed",
                observed_at=event.occurred_at,
                created_by_caregiver_id=caregiver_id,
                recorded_at=datetime.now(timezone.utc),
            ),
        ])
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_statuses"]["daily_care"]["status"] == "available"
    item = next(
        row for row in body["source_references"]
        if row["source_id"] == "daily-care-source-event"
    )
    assert item["source_type"] == "daily_care_observation"
    assert item["epistemic_status"] == "caregiver_reported"
    assert item["record_status"] == "recorded"
    assert item["details"]["occurred_at_timezone"] == "Africa/Lagos"
    assert item["details"]["review_status"] == "caregiver_reviewed_assertion_exists"
    assert item["evidence_ids"] == ["daily-care-source-evidence"]
    assert item["details"]["accepted_assertion_references"] == ["daily-care-reviewed-assertion"]
    assert item["details"]["capture_analysis_review_history"][0][0]["reviewed_candidate"]["text"] == "Lunch was mostly eaten."


def test_document_adapter_separates_verified_original_upload_from_extracted_text(client):
    import hashlib

    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    original = b"Follow-up appointment is scheduled for 12 December."
    digest = hashlib.sha256(original).hexdigest()
    db = TestSessionLocal()
    try:
        document = Evidence(
            id="handoff-document-evidence",
            person_id=person_id,
            type="document",
            source_text="Uploaded document record",
            uploaded_by_caregiver_id=caregiver_id,
            original_source_reference="clinic/letter-12",
        )
        db.add(document)
        db.flush()
        document_id = str(document.id)
        db.add(EvidenceUpload(
            evidence_id=document_id,
            filename="clinic-letter.txt",
            mime_type="text/plain",
            content=original,
            size_bytes=len(original),
            sha256=digest,
        ))
        db.add(Appointment(
            id="handoff-document-appointment",
            person_id=person_id,
            title="Document follow-up visit",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
        ))
        db.add(CaptureAnalysis(
            id="handoff-document-extraction",
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_evidence_id=document_id,
            source_text=original.decode("utf-8"),
            extraction_status="available",
            candidates={
                "kind": "document_extraction_v1",
                "binary_sha256": digest,
                "extraction_method": "server_utf8_text",
                "page_references": [1],
                "source_quotations": [{"page": 1, "quote": "Follow-up appointment is scheduled for 12 December."}],
            },
            review_history=[],
        ))
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30, "appointment_id": "handoff-document-appointment"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_statuses"]["documents"]["status"] == "available"
    extracted = next(
        row for row in body["source_references"]
        if row["source_id"] == "handoff-document-extraction"
    )
    original_ref = next(
        row for row in body["source_references"]
        if row["source_id"] == "handoff-document-evidence"
    )
    assert extracted["source_type"] == "document_extracted_text"
    assert extracted["details"]["document_id"] == document_id
    assert extracted["details"]["binary_sha256"] == digest
    assert extracted["details"]["page_references"] == [1]
    assert extracted["details"]["source_quotations"][0]["page"] == 1
    assert extracted["epistemic_status"] == "unreviewed_document_extraction"
    assert extracted["record_status"] == "extracted_not_caregiver_reviewed"
    assert original_ref["source_type"] == "uploaded_document"
    assert original_ref["details"]["extraction_status"] == "available"


def test_draft_persists_idempotently_and_supports_revision_and_approval(client, monkeypatch):
    import app.api.handoff_drafts as handoff_drafts
    from app.models.privacy_delivery import HandoffDraft, HandoffDraftRevision

    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    recipient = Caregiver(
        id="handoff-draft-recipient",
        name="Receiving caregiver",
        relationship="family",
        person_id=person_id,
    )
    recipient_id = str(recipient.id)
    db = TestSessionLocal()
    try:
        db.add(recipient)
        db.commit()
    finally:
        db.close()

    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value, :epistemic_status, :created_at)"""),
            {
                "id": "persisted-handoff-fact",
                "person_id": person_id,
            "subject_type": "care_recipient",
            "subject_id": person_id,
                "fact_type": "caregiver_observation",
                "fact_value": "Caregiver reports that she prefers tea.",
                "epistemic_status": "reported",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    calls = {"count": 0}

    def grounded_generation(records, purpose, audience):
        calls["count"] += 1
        source = next(row for row in records if row["source_id"] == "persisted-handoff-fact")
        return {
            "status": "generated",
            "provider": "ollama",
            "model": "qwen-test-fixture",
            "sections": [{
                "section": "Care preferences",
                "source_ref": "care_facts:persisted-handoff-fact",
                "summary": "The caregiver reports a preference for tea.",
                "supporting_quote": source["text"],
                "text": source["text"],
                "epistemic_status": source["epistemic_status"],
                "record_status": source["record_status"],
                "event_at": source["event_at"],
                "recorded_at": source["recorded_at"],
            }],
        }

    monkeypatch.setattr(handoff_drafts, "_generate_grounded_handoff", grounded_generation)
    headers = {
        "Authorization": f"Bearer {session['access_token']}",
        "x-request-id": "handoff-draft-idempotency-001",
    }
    payload = {"purpose": "family_transition", "audience": "family", "window_days": 30}
    prepared = test_client.post("/api/v1/handoff-drafts/prepare", headers=headers, json=payload)
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["draft_id"]
    assert prepared.json()["generation_status"] == "generated"
    assert prepared.json()["validation_status"] == "exact_quote_reference_validation_passed"
    assert prepared.json()["source_of_truth"] == "living_care_record"
    assert prepared.json()["source_snapshot_id"]
    assert len(prepared.json()["source_snapshot_sha256"]) == 64
    listed = test_client.get(
        "/api/v1/handoff-drafts?limit=10",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert listed.status_code == 200, listed.text
    assert any(item["draft_id"] == draft_id for item in listed.json()["items"])

    retried = test_client.post("/api/v1/handoff-drafts/prepare", headers=headers, json=payload)
    assert retried.status_code == 200, retried.text
    assert retried.json()["draft_id"] == draft_id
    assert calls["count"] == 1

    retrieved = test_client.get(
        f"/api/v1/handoff-drafts/{draft_id}",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert retrieved.status_code == 200, retrieved.text
    assert len(retrieved.json()["revision_history"]) == 1

    edited_content = retrieved.json()
    edited_content = {
        "template_sections": edited_content["template_sections"],
        "sections": edited_content["sections"],
        "generation": edited_content["generation"],
    }
    edited_content["generation"]["sections"][0]["section"] = "What the caregiver reported"
    saved = test_client.put(
        f"/api/v1/handoff-drafts/{draft_id}",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"content": edited_content},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision_number"] == 2
    assert saved.json()["review_status"] == "pending_review"

    unreviewed_approval = test_client.post(
        f"/api/v1/handoff-drafts/{draft_id}/approve",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        },
    )
    assert unreviewed_approval.status_code == 422, unreviewed_approval.text

    approved = test_client.post(
        f"/api/v1/handoff-drafts/{draft_id}/approve",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "reviewed_source_refs": [item["source_ref"] for item in prepared.json()["generation"]["sections"]],
            "semantic_review_acknowledged": True,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_status"] == "approved_for_sharing"
    assert approved.json()["approval"]["approval_revision"] == 2
    assert approved.json()["approval"]["approval_parameters"]["recipient_caregiver_ids"] == [recipient_id]
    assert approved.json()["approval"]["approval_parameters"]["reviewed_source_refs"]
    assert approved.json()["approval"]["approval_parameters"]["semantic_review_acknowledged"] is True

    db = TestSessionLocal()
    try:
        stored = db.query(HandoffDraft).filter(HandoffDraft.id == draft_id).one()
        revisions = db.query(HandoffDraftRevision).filter(HandoffDraftRevision.draft_id == draft_id).all()
        audit = db.query(__import__("app.models.privacy_delivery", fromlist=["AuditLogEvent"]).AuditLogEvent).filter(
            __import__("app.models.privacy_delivery", fromlist=["AuditLogEvent"]).AuditLogEvent.target_id == draft_id,
            __import__("app.models.privacy_delivery", fromlist=["AuditLogEvent"]).AuditLogEvent.action == "handoff_draft.prepare",
        ).one()
        assert stored.review_status == "approved_for_sharing"
        assert len(revisions) == 2
        assert audit.target_id == draft_id
    finally:
        db.close()


def test_source_change_after_preparation_marks_draft_stale_and_blocks_approval(client, monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    test_client, session = client
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value, :epistemic_status, :created_at)"""),
            {
                "id": "stale-handoff-fact",
                "person_id": person_id,
            "subject_type": "care_recipient",
            "subject_id": person_id,
                "fact_type": "caregiver_observation",
                "fact_value": "Caregiver reports a calm morning.",
                "epistemic_status": "reported",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def grounded_generation(records, purpose, audience):
        source = next(row for row in records if row["source_id"] == "stale-handoff-fact")
        return {
            "status": "generated",
            "provider": "ollama",
            "model": "qwen-test-fixture",
            "sections": [{
                "section": "Recent observations and changes",
                "source_ref": "care_facts:stale-handoff-fact",
                "summary": "The caregiver reports a calm morning.",
                "supporting_quote": source["text"],
                "text": source["text"],
            }],
        }

    monkeypatch.setattr(handoff_drafts, "_generate_grounded_handoff", grounded_generation)
    headers = {"Authorization": f"Bearer {session['access_token']}", "X-Request-ID": "stale-draft-source-001"}
    prepared = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={"purpose": "family_transition", "window_days": 30},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["draft_id"]

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE care_facts SET fact_value = :value WHERE id = :id"),
            {"value": "Caregiver reports a difficult morning.", "id": "stale-handoff-fact"},
        )

    # A retry is the same request even though its source snapshot is now stale.
    # It must return the original draft; freshness validation still blocks use.
    retried = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={"purpose": "family_transition", "window_days": 30},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["draft_id"] == draft_id
    assert retried.json()["source_freshness"]["status"] == "stale"
    changed_request = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={"purpose": "family_transition", "window_days": 14},
    )
    assert changed_request.status_code == 409, changed_request.text

    retrieved = test_client.get(
        f"/api/v1/handoff-drafts/{draft_id}",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["source_freshness"]["status"] == "stale"
    approval = test_client.post(
        f"/api/v1/handoff-drafts/{draft_id}/approve",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "recipient_caregiver_ids": ["not-a-real-recipient"],
            "scope": ["lcr_summary"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        },
    )
    assert approval.status_code == 409, approval.text



def test_qwen_appointment_briefing_uses_only_lcr_snapshot_and_appointment_context(monkeypatch):
    import json
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen-test", 30))
    observed = {}

    def capture(messages, **kwargs):
        observed["messages"] = messages
        observed.update(kwargs)
        return json.dumps({"items": [{
            "section": "Appointment purpose and context",
            "source_ref": "appointments:visit-1",
            "summary": "A follow-up appointment is recorded for the person.",
            "supporting_quote": "Follow-up visit scheduled.",
        }]})

    monkeypatch.setattr(handoff_drafts, "qwen_chat", capture)
    result = handoff_drafts._generate_grounded_handoff([{
        "source_table": "appointments",
        "source_id": "visit-1",
        "source_type": "appointment",
        "epistemic_status": "recorded",
        "record_status": "active",
        "event_at": "2026-11-20T09:00:00+00:00",
        "recorded_at": "2026-11-10T09:00:00+00:00",
        "text": "Follow-up visit scheduled.",
    }], "professional_appointment", "professional")

    system_prompt = observed["messages"][0]["content"]
    request_payload = json.loads(observed["messages"][1]["content"])
    assert result["status"] == "generated"
    assert request_payload["authoritative_source"] == "Living Care Record"
    assert request_payload["sources"][0]["source_ref"] == "appointments:visit-1"
    assert "appointment briefing" in system_prompt.lower()
    assert "do not use model memory" in system_prompt.lower()
    assert result["sections"][0]["supporting_quote"] == "Follow-up visit scheduled."


def test_qwen_handoff_generation_rejects_fabricated_source_reference(monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:1.5b", 30))
    monkeypatch.setattr(
        handoff_drafts,
        "qwen_chat",
        lambda *args, **kwargs: __import__("json").dumps({
            "items": [{
                "section": "Recent observations and changes",
                "source_ref": "care_events:invented-id",
                "summary": "A quiet morning was reported.",
                "supporting_quote": "Caregiver reported a quiet morning.",
            }]
        }),
    )
    result = handoff_drafts._generate_grounded_handoff(
        [{
            "source_table": "care_events",
            "source_id": "real-event",
            "source_type": "care_event",
            "epistemic_status": "caregiver_reported",
            "record_status": "recorded",
            "event_at": None,
            "recorded_at": None,
            "text": "Caregiver reported a quiet morning.",
        }],
        "family_transition",
        "family",
    )
    assert result["status"] == "validation_failed"
    assert result["sections"] == []


def test_qwen_handoff_generation_attaches_exact_source_text_to_selected_reference(monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:1.5b", 30))
    monkeypatch.setattr(
        handoff_drafts,
        "qwen_chat",
        lambda *args, **kwargs: __import__("json").dumps({
            "items": [{
                "section": "Recent observations",
                "source_ref": "care_events:real-event",
                "summary": "The caregiver reported a quiet morning.",
                "supporting_quote": "Caregiver reported a quiet morning.",
            }]
        }),
    )
    result = handoff_drafts._generate_grounded_handoff(
        [{
            "source_table": "care_events",
            "source_id": "real-event",
            "source_type": "care_event",
            "epistemic_status": "caregiver_reported",
            "record_status": "recorded",
            "event_at": "2026-11-07T08:00:00+00:00",
            "recorded_at": "2026-11-07T08:05:00+00:00",
            "text": "Caregiver reported a quiet morning.",
        }],
        "family_transition",
        "family",
    )
    assert result["status"] == "generated"
    assert result["provider"] == "ollama"
    assert result["model"] == "qwen2.5:1.5b"
    assert result["sections"][0]["text"] == "Caregiver reported a quiet morning."
    assert result["sections"][0]["summary"] == "The caregiver reported a quiet morning."
    assert result["sections"][0]["supporting_quote"] == "Caregiver reported a quiet morning."
    assert result["authority"] == "living_care_record_source_snapshot"
    assert result["semantic_entailment_status"] == "caregiver_review_required"


def test_qwen_handoff_generation_rejects_quote_not_present_in_lcr_source(monkeypatch):
    import json
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:1.5b", 30))
    monkeypatch.setattr(handoff_drafts, "qwen_chat", lambda *args, **kwargs: json.dumps({
        "items": [{
            "section": "Recent observations",
            "source_ref": "care_events:real-event",
            "summary": "The person fell twice.",
            "supporting_quote": "The person fell twice.",
        }]
    }))
    result = handoff_drafts._generate_grounded_handoff([{
        "source_table": "care_events",
        "source_id": "real-event",
        "source_type": "care_event",
        "epistemic_status": "caregiver_reported",
        "record_status": "recorded",
        "event_at": None,
        "recorded_at": None,
        "text": "Caregiver reported a quiet morning.",
    }], "family_transition", "family")
    assert result["status"] == "validation_failed"
    assert result["sections"] == []


def test_qwen_handoff_generation_rejects_unsupported_section_label(monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:1.5b", 30))
    monkeypatch.setattr(
        handoff_drafts,
        "qwen_chat",
        lambda *args, **kwargs: __import__("json").dumps({
            "items": [{
                "section": "Diagnosis",
                "source_ref": "care_events:real-event",
                "summary": "The caregiver reported a quiet morning.",
                "supporting_quote": "Caregiver reported a quiet morning.",
            }]
        }),
    )
    result = handoff_drafts._generate_grounded_handoff(
        [{
            "source_table": "care_events",
            "source_id": "real-event",
            "source_type": "care_event",
            "epistemic_status": "caregiver_reported",
            "record_status": "recorded",
            "event_at": None,
            "recorded_at": None,
            "text": "Caregiver reported a quiet morning.",
        }],
        "family_transition",
        "family",
    )
    assert result["status"] == "validation_failed"
    assert result["sections"] == []


def test_document_adapter_preserves_failed_ocr_without_claiming_extraction(client):
    import hashlib

    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    original = b"Scanned clinic document; OCR failed."
    digest = hashlib.sha256(original).hexdigest()
    db = TestSessionLocal()
    try:
        document = Evidence(
            id="handoff-failed-ocr-document",
            person_id=person_id,
            type="document",
            source_text="Uploaded document record",
            uploaded_by_caregiver_id=caregiver_id,
            original_source_reference="clinic/scanned-letter",
        )
        db.add(document)
        db.flush()
        document_id = str(document.id)
        db.add(EvidenceUpload(
            evidence_id=document_id,
            filename="scanned-letter.pdf",
            mime_type="application/pdf",
            content=original,
            size_bytes=len(original),
            sha256=digest,
        ))
        db.add(CaptureAnalysis(
            id="handoff-failed-ocr-analysis",
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_evidence_id=document_id,
            source_text="",
            extraction_status="failed",
            candidates={"kind": "document_extraction_v1", "binary_sha256": digest},
            review_history=[],
        ))
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "audience": "family", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_statuses"]["documents"]["status"] == "available"
    original_ref = next(row for row in body["source_references"] if row["source_id"] == "handoff-failed-ocr-document")
    assert original_ref["details"]["extraction_status"] == "failed"
    assert not any(row["source_type"] == "document_extracted_text" for row in body["source_references"])
    assert body["generation_status"] != "generated"


def test_failed_daily_care_adapter_is_not_reported_as_empty(client, monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    test_client, session = client

    def fail_adapter(*args, **kwargs):
        raise RuntimeError("simulated_daily_care_database_failure")

    monkeypatch.setattr(handoff_drafts, "_verified_daily_care_source", fail_adapter)
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "audience": "family", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    status = response.json()["source_statuses"]["daily_care"]
    assert status["status"] == "failed"
    assert status["reason"] == "daily_care_adapter_failed"
    assert status["count"] == 0


@pytest.mark.parametrize("failure", ["timeout", "authentication", "malformed"])
def test_qwen_handoff_generation_reports_real_inference_failures(monkeypatch, failure):
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:1.5b", 30))

    def fail_inference(*args, **kwargs):
        if failure == "timeout":
            raise TimeoutError("simulated inference timeout")
        if failure == "authentication":
            raise PermissionError("simulated model authentication failure")
        return "not-json"

    monkeypatch.setattr(handoff_drafts, "qwen_chat", fail_inference)
    result = handoff_drafts._generate_grounded_handoff(
        [{
            "source_table": "care_events",
            "source_id": "real-event",
            "source_type": "care_event",
            "epistemic_status": "caregiver_reported",
            "record_status": "recorded",
            "event_at": None,
            "recorded_at": None,
            "text": "Caregiver reported a quiet morning.",
        }],
        "family_transition",
        "family",
    )
    assert result["status"] == "source_only_fallback"
    assert result["provider"] is None
    assert result["sections"]
    assert result["sections"][0]["summary"] == "Caregiver reported a quiet morning."
    assert result["sections"][0]["supporting_quote"] == "Caregiver reported a quiet morning."


def test_new_persisted_source_after_preparation_marks_draft_stale(client, monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    test_client, session = client
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value, :epistemic_status, :created_at)"""),
            {
                "id": "handoff-before-new-source",
                "person_id": person_id,
            "subject_type": "care_recipient",
            "subject_id": person_id,
                "fact_type": "caregiver_observation",
                "fact_value": "Caregiver reports a settled morning.",
                "epistemic_status": "reported",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def grounded_generation(records, purpose, audience):
        source = next(row for row in records if row["source_id"] == "handoff-before-new-source")
        return {
            "status": "generated",
            "provider": "ollama",
            "model": "qwen-test-fixture",
            "sections": [{
                "section": "Recent observations and changes",
                "source_ref": "care_facts:handoff-before-new-source",
                "summary": "The caregiver reports a settled morning.",
                "supporting_quote": source["text"],
                "text": source["text"],
            }],
        }

    monkeypatch.setattr(handoff_drafts, "_generate_grounded_handoff", grounded_generation)
    headers = {"Authorization": f"Bearer {session['access_token']}", "X-Request-ID": "new-source-after-prepare-001"}
    prepared = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={"purpose": "family_transition", "window_days": 30},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["draft_id"]

    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value, :epistemic_status, :created_at)"""),
            {
                "id": "handoff-after-new-source",
                "person_id": person_id,
            "subject_type": "care_recipient",
            "subject_id": person_id,
                "fact_type": "caregiver_observation",
                "fact_value": "Caregiver reports a changed afternoon routine.",
                "epistemic_status": "reported",
                "created_at": (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat(),
            },
        )

    retrieved = test_client.get(f"/api/v1/handoff-drafts/{draft_id}", headers=headers)
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["source_freshness"]["status"] == "stale"


def test_qwen_handoff_generation_uses_cold_start_timeout_and_bounded_output(monkeypatch):
    import json
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 30))
    observed = {}

    def capture_inference(messages, **kwargs):
        observed["messages"] = messages
        observed.update(kwargs)
        return json.dumps({"items": [{
            "section": "Recent observations and changes",
            "source_ref": "care_events:real-event",
                "summary": "The caregiver reported a quiet morning.",
                "supporting_quote": "Caregiver reported a quiet morning.",
        }]})

    monkeypatch.setattr(handoff_drafts, "qwen_chat", capture_inference)
    result = handoff_drafts._generate_grounded_handoff(
        [{
            "source_table": "care_events",
            "source_id": "real-event",
            "source_type": "care_event",
            "epistemic_status": "caregiver_reported",
            "record_status": "recorded",
            "event_at": None,
            "recorded_at": None,
            "text": "Caregiver reported a quiet morning.",
        }],
        "family_transition",
        "family",
    )

    assert result["status"] == "generated"
    assert observed["timeout"] == 150
    assert observed["options"]["num_predict"] == 512
    assert observed["response_format"]["properties"]["items"]["maxItems"] == 6
    assert "summary" in observed["response_format"]["properties"]["items"]["items"]["required"]
    assert "supporting_quote" in observed["response_format"]["properties"]["items"]["items"]["required"]
    assert observed["response_format"]["properties"]["items"]["items"]["properties"]["source_ref"]["enum"] == ["care_events:real-event"]
    prompt_sources = json.loads(observed["messages"][1]["content"])["sources"]
    assert len(prompt_sources) == 1
    assert len(prompt_sources[0]["source_text"]) <= 600

def test_qwen_handoff_generation_bounds_context_and_attaches_only_selected_source_text(monkeypatch):
    import json
    import app.api.handoff_drafts as handoff_drafts

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 30))
    observed = {}

    def capture_inference(messages, **kwargs):
        observed["messages"] = messages
        observed.update(kwargs)
        return json.dumps({
            "items": [{
                "section": "Recent observations and changes",
                "source_ref": "care_events:event-9",
                "summary": "The source records exact event 9 text.",
                "supporting_quote": "Exact source text for event 9.",
            }]
        })

    monkeypatch.setattr(handoff_drafts, "qwen_chat", capture_inference)
    records = [
        {
            "source_table": "care_events",
            "source_id": f"event-{index}",
            "source_type": "care_event",
            "epistemic_status": "caregiver_reported",
            "record_status": "recorded",
            "event_at": f"2026-11-{index + 1:02d}T08:00:00+00:00",
            "recorded_at": f"2026-11-{index + 1:02d}T08:05:00+00:00",
            "text": f"Exact source text for event {index}. " * 40,
        }
        for index in range(10)
    ]

    result = handoff_drafts._generate_grounded_handoff(
        records,
        "professional_appointment",
        "professional",
    )

    prompt_sources = json.loads(observed["messages"][1]["content"])["sources"]
    assert result["status"] == "generated"
    assert result["sources_available"] == 10
    assert result["sources_considered"] == 6
    assert len(prompt_sources) == 6
    assert all(len(item["source_text"]) <= 600 for item in prompt_sources)
    assert prompt_sources[0]["source_ref"] == "care_events:event-9"
    assert result["sections"][0]["source_ref"] == "care_events:event-9"
    assert result["sections"][0]["text"] == records[9]["text"][:1200]
    assert result["sections"][0]["supporting_quote"] == "Exact source text for event 9."
    assert result["sections"][0]["summary"] == "The source records exact event 9 text."

def test_handoff_claim_provenance_links_each_exact_quote_to_its_own_source():
    from app.api.handoff_drafts import _handoff_claim_provenance

    content = {
        "generation": {
            "sections": [
                {
                    "section": "Recent observations",
                    "text": "Caregiver reported a quiet morning.",
                    "summary": "The caregiver reported a quiet morning.",
                    "supporting_quote": "Caregiver reported a quiet morning.",
                    "source_ref": "care_events:event-1",
                },
                {
                    "section": "Earlier history",
                    "text": "A clinic note mentioned unsteadiness.",
                    "summary": "A clinic note mentioned unsteadiness.",
                    "supporting_quote": "A clinic note mentioned unsteadiness.",
                    "source_ref": "evidence:note-2",
                },
            ]
        }
    }
    snapshot = {
        "source_records": [
            {
                "source_type": "care_event",
                "source_table": "care_events",
                "source_id": "event-1",
                "text": "Caregiver reported a quiet morning.",
                "event_at": "2026-11-10T08:00:00+00:00",
                "recorded_at": "2026-11-10T08:05:00+00:00",
                "stale_warning": False,
                "evidence_ids": ["note-1"],
                "evidence_sources": [{"evidence_id": "note-1", "reported_by_label": "Daughter"}],
            },
            {
                "source_type": "evidence",
                "source_table": "evidence",
                "source_id": "note-2",
                "text": "A clinic note mentioned unsteadiness.",
                "event_at": "2026-10-01T08:00:00+00:00",
                "recorded_at": "2026-10-01T08:05:00+00:00",
                "stale_warning": True,
            },
        ]
    }
    provenance = _handoff_claim_provenance(content, snapshot)
    assert provenance["coverage"] == "all_claims_have_source_quotes"
    assert provenance["claims"][0]["support_status"] == "matched_saved_source_quote"
    assert provenance["claims"][0]["source_references"][0]["record_id"] == "event-1"
    assert provenance["claims"][0]["source_references"][0]["related_evidence"][0]["reported_by_label"] == "Daughter"
    assert provenance["claims"][1]["support_status"] == "matched_historical_only"
    assert provenance["claims"][1]["source_references"][0]["source_state_label"]

def test_observation_correction_marks_prepared_handoff_source_stale(client, monkeypatch):
    from app.api import handoff_drafts
    from app.models.care_continuity import EvidenceObservation

    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    now = datetime.now(timezone.utc)
    source_text = "She felt unsteady near the kitchen."
    db = TestSessionLocal()
    try:
        evidence = Evidence(
            id="handoff-corrected-observation-evidence",
            person_id=person_id,
            type="caregiver_report",
            source_text=source_text,
            uploaded_by_caregiver_id=caregiver_id,
            reported_by_label="Daughter",
        )
        event = CareEvent(
            id="handoff-corrected-observation-event",
            person_id=person_id,
            event_type="structured_daily_capture",
            status="recorded",
            occurred_at=now - timedelta(hours=2),
            title="Unsteadiness near the kitchen",
            description=source_text,
            evidence_ids=["handoff-corrected-observation-evidence"],
            created_by_caregiver_id=caregiver_id,
        )
        original = EvidenceObservation(
            id="handoff-corrected-observation-original",
            person_id=person_id,
            evidence_id=evidence.id,
            observation_kind="caregiver_report",
            epistemic_status="reported",
            subject_label="person receiving care",
            attribute="unsteadiness",
            value_text="unsteady",
            source_passage=source_text,
            source_span_start=0,
            source_span_end=len(source_text),
            extraction_method="human_entered",
            extraction_version="test-v1",
            created_by_caregiver_id=caregiver_id,
            recorded_at=now - timedelta(hours=1),
        )
        db.add_all([evidence, event, original])
        db.commit()
        event_id = event.id
        evidence_id = evidence.id
        original_observation_id = original.id
    finally:
        db.close()

    monkeypatch.setattr(
        handoff_drafts,
        "_generate_grounded_handoff",
        lambda *args, **kwargs: {"status": "not_generated_no_eligible_source_text", "provider": "ollama", "model": "test", "sections": []},
    )
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    prepared = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={"purpose": "family_transition", "window_days": 30},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["draft_id"]
    event_reference = next(
        item for item in prepared.json()["source_references"]
        if item["source_id"] == event_id
    )
    assert event_reference["details"]["source_observation_history"]["records"][0]["source_state"] == "current_version"

    db = TestSessionLocal()
    try:
        corrected = EvidenceObservation(
            id="handoff-corrected-observation-current",
            person_id=person_id,
            evidence_id=evidence_id,
            supersedes_observation_id=original_observation_id,
            correction_rationale="The caregiver corrected the original interpretation.",
            observation_kind="caregiver_report",
            epistemic_status="reported",
            subject_label="person receiving care",
            attribute="unsteadiness",
            value_text="steady",
            source_passage=source_text,
            source_span_start=0,
            source_span_end=len(source_text),
            extraction_method="human_entered",
            extraction_version="test-v1",
            created_by_caregiver_id=caregiver_id,
            recorded_at=datetime.now(timezone.utc),
        )
        db.add(corrected)
        db.commit()
    finally:
        db.close()

    retrieved = test_client.get(
        f"/api/v1/handoff-drafts/{draft_id}",
        headers=headers,
    )
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["source_freshness"]["status"] == "stale"


def test_handoff_includes_only_valid_necessary_open_questions(client):
    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        source = Evidence(
            person_id=person_id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="The current mobility support plan is needed for the handoff decision.",
            uploaded_by_caregiver_id=caregiver_id,
            source_type="caregiver_report",
        )
        db.add(source)
        db.flush()
        db.add_all([
            OpenQuestion(
                id="handoff-necessary-question",
                person_id=person_id,
                question="What mobility support is required for the next handoff?",
                subject_type="person",
                subject_id=person_id,
                status="open",
                necessity="necessary",
                necessity_rationale="The receiving caregiver needs the current support level to safely plan transfers.",
                evidence_ids=[source.id],
                context=[{
                    "source_kind": "document_extraction",
                    "source_id": source.id,
                    "source_record_refs": [{"record_type": "evidence", "record_id": source.id}],
                    "necessity_assessment": {
                        "necessity_rationale": "The receiving caregiver needs the current support level to safely plan transfers.",
                        "decision_dependency": "The receiving caregiver cannot prepare the transfer plan without the current support level.",
                        "evidence_ids": [source.id],
                    },
                }],
            ),
            OpenQuestion(
                id="handoff-unassessed-question",
                person_id=person_id,
                question="What was the colour of the previous care folder?",
                subject_type="person",
                subject_id=person_id,
                status="open",
                necessity="not_assessed",
                evidence_ids=[source.id],
                context=[{"source_kind": "document_extraction"}],
            ),
        ])
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "audience": "family", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    question_refs = [
        item["source_id"] for item in response.json()["source_references"]
        if item.get("source_table") == "open_questions"
    ]
    assert "handoff-necessary-question" in question_refs
    assert "handoff-unassessed-question" not in question_refs


def test_handoff_paginates_source_history_and_discloses_the_safety_ceiling(client):
    test_client, session = client
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        for index in range(501):
            timestamp = datetime.now(timezone.utc) - timedelta(minutes=index)
            connection.execute(text("""
                INSERT INTO care_facts
                    (id, person_id, subject_type, fact_type, fact_value,
                     epistemic_status, effective_at, recorded_at, created_at, updated_at,
                     created_by_caregiver_id)
                VALUES
                    (:id, :person_id, 'care_recipient', 'caregiver_observation',
                     :value, 'reported', :timestamp, :timestamp, :timestamp, :timestamp,
                     :caregiver_id)
            """), {
                "id": f"handoff-limit-fact-{index}",
                "person_id": person_id,
                "value": f"Caregiver report number {index}",
                "timestamp": timestamp,
                "caregiver_id": session["caregiver"]["id"],
            })

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "audience": "family", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    facts = body["source_statuses"]["care_facts"]
    assert facts["returned_count"] == 500
    assert facts["has_more"] is True
    assert facts["retrieval_complete"] is False
    assert body["retrieval_complete"] is False
    assert {"source": "care_facts", "status": "partial",
            "reason": "source_projection_reached_500_record_safety_limit",
            "returned_count": 500} in body["limitations"]
    assert body["partial_sources"] == [{"source": "care_facts", "returned_count": 500}]

def test_draft_includes_caregiver_reviewable_medication_assertions_with_source_lineage(client):
    test_client, session = client
    token = session["access_token"]
    created = test_client.post(
        "/api/v1/care-continuity/medications",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Example prescribed medicine", "dose": "5 mg", "note": "Caregiver-reported medication"},
    )
    assert created.status_code == 201, created.text
    created_body = created.json()
    assert created_body["record_status"] == "awaiting_caregiver_review"

    appointment = Appointment(
        id="handoff-medication-appointment",
        person_id=session["person"]["id"],
        title="Medication review",
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
        status="scheduled",
        created_by_caregiver_id=session["caregiver"]["id"],
    )
    db = TestSessionLocal()
    try:
        db.add(appointment)
        db.commit()
    finally:
        db.close()
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30, "appointment_id": "handoff-medication-appointment"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_statuses"]["medications"]["status"] == "available"
    medication_refs = [
        item for item in body["source_references"]
        if item["source_type"] == "medication_assertion"
    ]
    assert len(medication_refs) >= 2, "name and explicit status assertions must both be retained"
    assert all(item["source_table"] == "care_assertions" for item in medication_refs)
    assert all(item["epistemic_status"] == "reported" for item in medication_refs)
    assert all(item["record_status"] in {"reported", "review_required"} for item in medication_refs)
    assert body["retrieval_complete"] is False, "unavailable allergy evidence must remain an explicit completeness gap"
    assert any(item["source"] == "allergies" for item in body["limitations"])

def test_qwen_unavailable_uses_exact_source_only_briefing_fallback(monkeypatch):
    import app.api.handoff_drafts as handoff_drafts

    def unavailable():
        raise RuntimeError("Qwen is not configured in this test environment")

    monkeypatch.setattr(handoff_drafts, "get_ollama_config", unavailable)
    source_text = "Caregiver reported that breakfast was mostly uneaten."
    result = handoff_drafts._generate_grounded_handoff([{
        "source_table": "care_facts",
        "source_id": "fallback-fact-001",
        "source_type": "lcr_fact",
        "epistemic_status": "reported",
        "record_status": "current",
        "event_at": "2026-11-19T08:00:00+00:00",
        "recorded_at": "2026-11-19T08:10:00+00:00",
        "text": source_text,
    }], "period_summary", "family")

    assert result["status"] == "source_only_fallback"
    assert result["provider"] is None
    assert result["generation_contract"] == "exact_source_text_only_no_model_paraphrase"
    assert result["sections"][0]["summary"] == source_text
    assert result["sections"][0]["supporting_quote"] == source_text
    assert result["sections"][0]["source_ref"] == "care_facts:fallback-fact-001"
    provenance = handoff_drafts._handoff_claim_provenance(
        {"generation": result},
        {"source_records": [{
            "source_table": "care_facts",
            "source_id": "fallback-fact-001",
            "text": source_text,
            "event_at": "2026-11-19T08:00:00+00:00",
            "recorded_at": "2026-11-19T08:10:00+00:00",
        }]},
    )
    assert provenance["claims"][0]["claim_type"] == "source_only_lcr_excerpt"
    assert provenance["claims"][0]["semantic_entailment_status"] == "not_applicable_no_paraphrase"

def test_appointment_briefing_requires_and_retrieves_one_person_scoped_appointment(client):
    test_client, session = client
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        selected = Appointment(
            id="briefing-selected-appointment",
            person_id=person_id,
            title="Primary care follow-up",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=3),
            provider="Primary care clinician",
            status="scheduled",
            preparation_notes="Review recent sleep and appetite observations.",
            created_by_caregiver_id=caregiver_id,
        )
        unrelated = Appointment(
            id="briefing-unrelated-appointment",
            person_id=person_id,
            title="Dental appointment",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=10),
            provider="Dental clinic",
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([selected, unrelated])
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {session['access_token']}"}
    missing = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30},
    )
    assert missing.status_code == 422, missing.text

    available = test_client.get("/api/v1/handoff-drafts/appointments", headers=headers)
    assert available.status_code == 200, available.text
    assert {item["id"] for item in available.json()["items"]} == {
        "briefing-selected-appointment", "briefing-unrelated-appointment"
    }

    prepared = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={
            "purpose": "professional_appointment",
            "audience": "professional",
            "window_days": 30,
            "appointment_id": "briefing-selected-appointment",
        },
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()
    assert body["appointment_id"] == "briefing-selected-appointment"
    assert body["selected_appointment"]["title"] == "Primary care follow-up"
    appointment_refs = [
        item for item in body["source_references"]
        if item["source_table"] == "appointments"
    ]
    assert [item["source_id"] for item in appointment_refs] == ["briefing-selected-appointment"]
