"""Transparency evidence endpoint tests; PostgreSQL-backed when TEST_POSTGRES_URL is set."""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.session_security
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.care import Caregiver, Correction, Evidence
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent
from app.models.care_continuity import CareAssertion, CareStateRevision
from app.models.collaboration import EvidenceUpload
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest

TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
COMPANION_POSTGRES_URL = os.getenv("SOLENOS_COMPANION_TEST_DATABASE_URL")
POSTGRES_URL = TEST_POSTGRES_URL or COMPANION_POSTGRES_URL
if POSTGRES_URL:
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
else:
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
    if TEST_POSTGRES_URL:
        # TEST_POSTGRES_URL is reserved for an isolated database this test owns.
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        Base.metadata.create_all(bind=engine)
    elif not COMPANION_POSTGRES_URL:
        Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    if not POSTGRES_URL:
        Base.metadata.drop_all(bind=engine)


def _create_session(client, caregiver_name="Primary caregiver"):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "Synthetic care recipient", "caregiver_name": caregiver_name},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def test_transparency_evidence_is_authenticated_and_person_scoped():
    client = TestClient(app)
    session, headers = _create_session(client)
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]

    unauthenticated = client.get("/api/v1/transparency/evidence")
    assert unauthenticated.status_code == 401

    db = TestSessionLocal()
    try:
        evidence = Evidence(
            person_id=person_id,
            type="document",
            evidence_kind="uploaded_document",
            epistemic_status="reported",
            source_text="SYNTHETIC-PRIVATE-SOURCE-TEXT-MUST-NOT-LEAK",
            uploaded_by_caregiver_id=caregiver_id,
            evidence_status="received",
        )
        db.add(evidence)
        db.flush()
        db.add(EvidenceUpload(
            evidence_id=evidence.id,
            filename="synthetic-discharge-summary.txt",
            mime_type="text/plain",
            content=b"SYNTHETIC-PRIVATE-BINARY",
            size_bytes=24,
            sha256=hashlib.sha256(b"SYNTHETIC-PRIVATE-BINARY").hexdigest(),
        ))
        analysis = CaptureAnalysis(
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_evidence_id=evidence.id,
            source_text="SYNTHETIC-PRIVATE-SOURCE-TEXT-MUST-NOT-LEAK",
            extraction_status="completed",
            candidates=[],
            review_history=[],
        )
        db.add(analysis)
        db.flush()
        assertion = CareAssertion(
            person_id=person_id,
            subject_type="person",
            subject_id=person_id,
            field="mobility",
            asserted_value="uses a walking aid",
            source_label="caregiver report",
            evidence_ids=[evidence.id],
            assertion_status="caregiver_reviewed",
            epistemic_state="reported",
            created_by_caregiver_id=caregiver_id,
        )
        db.add(assertion)
        db.flush()
        db.add(DocumentReviewEvent(
            analysis_id=analysis.id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_evidence_id=evidence.id,
            candidate_index=0,
            decision_version=1,
            decision="accept",
            before_state={},
            after_state={"status": "accepted"},
            resulting_assertion_id=assertion.id,
        ))
        db.add(Correction(
            person_id=person_id,
            target_type="care_assertion",
            target_id=assertion.id,
            original_text="uses a walking aid",
            corrected_text="uses a walker",
            reason="Caregiver correction",
            caregiver_id=caregiver_id,
            processing_status="completed",
            processing_attempts=1,
            processed_at=datetime.now(timezone.utc),
        ))
        db.add(CareStateRevision(
            person_id=person_id,
            target_type="care_assertion",
            target_id=assertion.id,
            revision_number=1,
            change_kind="created",
            after_state='{"field":"mobility"}',
            evidence_ids=[evidence.id],
            changed_by_caregiver_id=caregiver_id,
        ))
        proposal = AIOutputLCRProposal(
            person_id=person_id,
            caregiver_id=caregiver_id,
            proposal_type="care_update",
            proposed_change_json={"field": "mobility"},
            source_event_ids_json=[],
            source_evidence_ids_json=[evidence.id],
            uncertainty_json={},
            status="accepted",
        )
        db.add(proposal)
        db.flush()
        db.add(AIOutputLCRDecision(
            proposal_id=proposal.id,
            person_id=person_id,
            reviewer_caregiver_id=caregiver_id,
            decision="accept",
            resulting_lcr_revision_id="synthetic-revision-1",
        ))
        db.add(AuditLogEvent(
            actor_id=caregiver_id,
            subject_id=person_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            action="synthetic.document.uploaded",
            target_type="evidence",
            target_id=evidence.id,
            outcome="success",
        ))
        db.add(DataSubjectRequest(
            requester_id=caregiver_id,
            actor_id=caregiver_id,
            subject_id=person_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            request_type="access",
            request_status="received_pending_authority_review",
            requested_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/transparency/evidence", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"]["person_id"] == person_id
    assert payload["scope"]["caregiver_id"] == caregiver_id
    assert payload["coverage"]["active_session"] == "complete"
    assert payload["coverage"]["documents"] == "complete_metadata"
    assert payload["coverage"]["document_review"] == "complete_recent_window"
    assert payload["coverage"]["lcr"] == "complete_aggregate_and_recent"
    assert payload["coverage"]["audit_activity"] == "complete_recent_window"
    assert payload["coverage"]["privacy_requests"] == "complete_recent_window"

    by_kind = {item["kind"]: item for item in payload["items"]}
    assert by_kind["documents_and_extraction"]["details"]["evidence_count"] == 1
    assert by_kind["documents_and_extraction"]["details"]["recent_documents"][0]["filename"] == "synthetic-discharge-summary.txt"
    assert by_kind["documents_and_extraction"]["details"]["extraction_status_counts"] == {"completed": 1}
    assert by_kind["documents_and_extraction"]["details"]["recent_extractions"][0]["extraction_status"] == "completed"
    assert by_kind["document_review_history"]["details"]["recent_reviews"][0]["decision"] == "accept"
    assert by_kind["document_review_history"]["details"]["recent_reviews"][0]["reviewer_name"] == "Primary caregiver"
    assert by_kind["document_review_history"]["details"]["recent_reviews"][0]["resulting_assertion_id"] == assertion.id
    lcr_details = by_kind["living_care_record_state"]["details"]
    assert lcr_details["assertion_status_counts"] == {"caregiver_reviewed": 1}
    assert lcr_details["recent_corrections"][0]["actor_name"] == "Primary caregiver"
    assert lcr_details["recent_revisions"][0]["revision_number"] == 1
    assert lcr_details["recent_proposals"][0]["status"] == "accepted"
    assert lcr_details["recent_proposals"][0]["decisions"][0]["decision"] == "accept"
    assert by_kind["audit_activity"]["details"]["recent_events"][0]["action"] == "synthetic.document.uploaded"
    assert by_kind["privacy_data_control_operations"]["details"]["requests"][0]["request_type"] == "access"

    serialized = response.text
    assert "SYNTHETIC-PRIVATE-SOURCE-TEXT-MUST-NOT-LEAK" not in serialized
    assert "SYNTHETIC-PRIVATE-BINARY" not in serialized
    assert "content" not in by_kind["documents_and_extraction"]["details"]["recent_documents"][0]
    assert by_kind["privacy_security_retention_policy"]["status"] == "unavailable"
    assert by_kind["connected_integrations"]["status"] == "unavailable"

    # Caller-supplied identifiers cannot change the bearer session's record scope.
    wrong_scope = client.get(
        f"/api/v1/transparency/evidence?person_id={secrets.token_hex(12)}",
        headers=headers,
    )
    assert wrong_scope.status_code == 200
    assert wrong_scope.json()["scope"]["person_id"] == person_id


def test_documents_only_caregiver_receives_document_evidence_but_not_lcr_or_member_roster():
    client = TestClient(app)
    primary, _ = _create_session(client)
    person_id = primary["person"]["id"]
    primary_caregiver_id = primary["caregiver"]["id"]

    db = TestSessionLocal()
    try:
        document_caregiver = Caregiver(
            name="Document-limited caregiver",
            relationship="family",
            person_id=person_id,
        )
        db.add(document_caregiver)
        db.flush()
        grant = CaregiverAccessGrant(
            person_id=person_id,
            caregiver_id=document_caregiver.id,
            role="family",
            access_scope="documents_only",
            status="active",
            can_manage_members=False,
            permissions=["view_documents"],
            granted_by_caregiver_id=primary_caregiver_id,
        )
        token = secrets.token_urlsafe(32)
        db.add(grant)
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=document_caregiver.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        db.commit()
    finally:
        db.close()

    limited_headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/transparency/evidence", headers=limited_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"]["access_scope"] == "documents_only"
    assert payload["coverage"]["documents"] == "complete_metadata"
    assert payload["coverage"]["lcr"] == "not_authorized"
    assert payload["coverage"]["sharing_members"] == "not_authorized"
    kinds = {item["kind"] for item in payload["items"]}
    assert "documents_and_extraction" in kinds
    assert "living_care_record_state" not in kinds
    assert "active_caregiver_access" not in kinds
