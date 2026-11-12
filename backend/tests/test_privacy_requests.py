import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
if TEST_POSTGRES_URL:
    engine = create_engine(TEST_POSTGRES_URL, pool_pre_ping=True)
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
    # This file is also run against a dedicated, disposable PostgreSQL database.
    # Reset only that isolated database's public schema between tests so reviewer
    # queues cannot see previous test rows and FK dependencies cannot break teardown.
    if TEST_POSTGRES_URL:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    if not TEST_POSTGRES_URL:
        Base.metadata.drop_all(bind=engine)


def create_session(client, name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_privacy_request_requires_authenticated_session():
    client = TestClient(app)
    response = client.post(
        "/api/v1/privacy/requests",
        json={
            "request_type": "deletion",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert response.status_code == 401


def test_privacy_request_is_pending_and_does_not_claim_fulfilment():
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    response = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "deletion",
            "authority_basis": "authorized_caregiver",
            "authority_attested": True,
            "note": "Please review this request.",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "received_pending_authority_review"
    assert body["authority_verified"] is False
    assert body["fulfilment_started"] is False

    listed = client.get("/api/v1/privacy/requests", headers=headers)
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["requests"]] == [body["id"]]

    from app.models.care import Person
    db = TestSessionLocal()
    try:
        assert db.query(Person).filter(Person.id == session["person"]["id"]).count() == 1
    finally:
        db.close()


def test_privacy_request_list_is_scoped_to_the_requesting_caregiver_and_record():
    client = TestClient(app)
    first = create_session(client, "First recipient")
    second = create_session(client, "Second recipient")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=first_headers,
        json={
            "request_type": "access",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    own = client.get("/api/v1/privacy/requests", headers=first_headers)
    other = client.get("/api/v1/privacy/requests", headers=second_headers)
    assert len(own.json()["requests"]) == 1
    assert other.json()["requests"] == []


def test_privacy_request_rejects_unattested_authority():
    client = TestClient(app)
    session = create_session(client, "Care recipient")
    response = client.post(
        "/api/v1/privacy/requests",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "request_type": "export",
            "authority_basis": "other",
            "authority_attested": False,
        },
    )
    assert response.status_code == 422


from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest


def test_privacy_review_requires_configured_reviewer_credential(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "r" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "founder_privacy_reviewer")
    client = TestClient(app)
    session = create_session(client, "Review recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "deletion",
            "authority_basis": "legal_representative",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    denied = client.get("/api/v1/privacy/requests/reviewer/pending", headers=headers)
    assert denied.status_code == 401

    reviewer_headers = {
        **headers,
        "X-SolenOS-Privacy-Reviewer-Token": "r" * 40,
    }
    pending = client.get("/api/v1/privacy/requests/reviewer/pending", headers=reviewer_headers)
    assert pending.status_code == 200, pending.text
    assert [item["id"] for item in pending.json()["requests"]] == [request_id]
    assert pending.json()["reviewer_id"] == "founder_privacy_reviewer"

    reviewed = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the submitted identity and authority evidence.",
            "authority_evidence_checked": "Government-issued identity and signed legal authority document.",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["status"] == "approved_pending_fulfilment_assessment"
    assert body["authority_verified_by_reviewer"] is True
    assert body["fulfilment_started"] is False
    assert body["resolved_at"] is None
    assert "no data was exported" in body["message"].casefold()

    # Approval removes the request from the pending queue but does not mark it
    # fulfilled or resolved.
    after_approval = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert after_approval.status_code == 200, after_approval.text
    assert request_id not in [item["id"] for item in after_approval.json()["requests"]]

    duplicate = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Attempted duplicate review.",
            "authority_evidence_checked": "Same evidence.",
        },
    )
    assert duplicate.status_code == 409

    db = TestSessionLocal()
    try:
        event = (
            db.query(AuditLogEvent)
            .filter(
                AuditLogEvent.target_id == request_id,
                AuditLogEvent.action == "privacy_request.reviewed",
            )
            .one()
        )
        assert event.actor_id == "founder_privacy_reviewer"
        assert event.subject_id == session["person"]["id"]
        assert event.person_id == session["person"]["id"]
        assert event.caregiver_id == session["caregiver"]["id"]
        assert event.outcome == "approve"
        assert event.record_metadata == {
            "request_type": "deletion",
            "fulfilment_started": False,
        }
        assert "authority_evidence_checked" not in str(event.record_metadata)
    finally:
        db.close()


def test_privacy_review_approval_requires_authority_evidence(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "s" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "founder_privacy_reviewer")
    client = TestClient(app)
    session = create_session(client, "Evidence recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "export",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text

    response = client.post(
        f"/api/v1/privacy/requests/{submitted.json()['id']}/review",
        headers={
            **headers,
            "X-SolenOS-Privacy-Reviewer-Token": "s" * 40,
        },
        json={
            "decision": "approve",
            "review_basis": "Reviewed the available request.",
        },
    )
    assert response.status_code == 422


def test_privacy_reviewer_fails_closed_when_configuration_is_missing(monkeypatch):
    monkeypatch.delenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", raising=False)
    monkeypatch.delenv("SOLENOS_PRIVACY_REVIEWER_ID", raising=False)
    response = TestClient(app).get("/api/v1/privacy/requests/reviewer/pending")
    assert response.status_code == 503


def test_privacy_reviewer_rejects_wrong_and_short_credentials(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "v" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    for token in ("wrong-token", "v" * 31, ""):
        response = client.get(
            "/api/v1/privacy/requests/reviewer/pending",
            headers={"X-SolenOS-Privacy-Reviewer-Token": token},
        )
        assert response.status_code == 401, response.text


def test_privacy_review_rejection_is_audited_and_removed_from_pending(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "w" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Rejection recipient")
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "request_type": "correction",
            "authority_basis": "other",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "w" * 40}

    rejected = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "reject",
            "review_basis": "The submitted authority evidence was insufficient.",
        },
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["authority_verified_by_reviewer"] is False
    assert body["fulfilment_started"] is False
    assert body["resolved_at"] is not None
    assert "no data was exported" in body["message"].casefold()

    duplicate_rejection = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "reject",
            "review_basis": "Attempted duplicate rejection.",
        },
    )
    assert duplicate_rejection.status_code == 409, duplicate_rejection.text

    pending = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert pending.status_code == 200, pending.text
    assert request_id not in [item["id"] for item in pending.json()["requests"]]

    db = TestSessionLocal()
    try:
        event = (
            db.query(AuditLogEvent)
            .filter(
                AuditLogEvent.target_id == request_id,
                AuditLogEvent.action == "privacy_request.reviewed",
            )
            .one()
        )
        assert event.actor_id == "privacy-reviewer"
        assert event.outcome == "reject"
    finally:
        db.close()


def test_privacy_request_accepts_each_supported_request_type(monkeypatch):
    client = TestClient(app)
    session = create_session(client, "Request type recipient")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    for request_type in ("access", "export", "correction", "deletion"):
        response = client.post(
            "/api/v1/privacy/requests",
            headers=headers,
            json={
                "request_type": request_type,
                "authority_basis": "self",
                "authority_attested": True,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["request_type"] == request_type
        assert response.json()["fulfilment_started"] is False


@pytest.mark.parametrize("request_type", ["export", "correction", "deletion"])
def test_privacy_review_approval_does_not_claim_or_start_fulfilment(monkeypatch, request_type):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "z" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, f"{request_type} fulfilment boundary recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "z" * 40}

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": request_type,
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Verified the requester's identity and authority evidence.",
            "authority_evidence_checked": "Identity evidence matched the care-record authority.",
        },
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["request_type"] == request_type
    assert body["status"] == "approved_pending_fulfilment_assessment"
    assert body["authority_verified_by_reviewer"] is True
    assert body["fulfilment_started"] is False
    assert body["resolved_at"] is None
    assert "no data was exported, corrected, or deleted" in body["message"].casefold()

    # Approval is a one-way state transition. A replay must not rewrite the
    # reviewer decision or append a second review audit event.
    duplicate_review = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Attempted duplicate approval.",
            "authority_evidence_checked": "Attempted duplicate authority check.",
        },
    )
    assert duplicate_review.status_code == 409, duplicate_review.text

    for unavailable_field in (
        "export_url",
        "download_url",
        "correction_result",
        "amendment_id",
        "deletion_job_id",
        "erasure_status",
        "deleted_at",
        "fulfilled_at",
    ):
        assert unavailable_field not in body

    db = TestSessionLocal()
    try:
        row = db.query(DataSubjectRequest).filter(DataSubjectRequest.id == request_id).one()
        assert row.request_status == "approved_pending_fulfilment_assessment"
        assert row.resolved_at is None
        assert row.record_metadata["review"]["fulfilment_started"] is False
        assert db.query(DataSubjectRequest).filter(
            DataSubjectRequest.id == request_id,
            DataSubjectRequest.person_id == session["person"]["id"],
            DataSubjectRequest.caregiver_id == session["caregiver"]["id"],
        ).count() == 1
        audit = db.query(AuditLogEvent).filter(
            AuditLogEvent.target_id == request_id,
            AuditLogEvent.action == "privacy_request.reviewed",
        ).one()
        assert audit.actor_id == "privacy-reviewer"
        assert audit.outcome == "approve"
        assert audit.record_metadata == {
            "request_type": request_type,
            "fulfilment_started": False,
        }
        assert "authority_evidence_checked" not in str(audit.record_metadata)
    finally:
        db.close()

def test_privacy_review_rejects_invalid_reviewer_and_unknown_request(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "x" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Reviewer authorization recipient")
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "request_type": "access",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    payload = {
        "decision": "approve",
        "review_basis": "The request was reviewed.",
        "authority_evidence_checked": "Identity evidence was checked.",
    }
    wrong_token = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers={"X-SolenOS-Privacy-Reviewer-Token": "wrong"},
        json=payload,
    )
    assert wrong_token.status_code == 401, wrong_token.text

    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "x" * 40}
    unknown = client.post(
        "/api/v1/privacy/requests/not-a-real-request/review",
        headers=reviewer_headers,
        json=payload,
    )
    assert unknown.status_code == 404, unknown.text

    still_pending = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert still_pending.status_code == 200, still_pending.text
    assert [item["id"] for item in still_pending.json()["requests"]] == [request_id]


def test_privacy_reviewer_queue_excludes_request_notes_and_non_pending_items(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "y" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Reviewer queue privacy recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "y" * 40}

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "correction",
            "authority_basis": "self",
            "authority_attested": True,
            "note": "PRIVATE_REQUEST_NOTE_MUST_NOT_APPEAR_IN_REVIEW_QUEUE",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    pending = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert pending.status_code == 200, pending.text
    assert request_id in [item["id"] for item in pending.json()["requests"]]
    assert "PRIVATE_REQUEST_NOTE_MUST_NOT_APPEAR_IN_REVIEW_QUEUE" not in pending.text

    rejected = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "reject",
            "review_basis": "The submitted authority evidence was insufficient.",
        },
    )
    assert rejected.status_code == 200, rejected.text

    after_rejection = client.get(
        "/api/v1/privacy/requests/reviewer/pending",
        headers=reviewer_headers,
    )
    assert after_rejection.status_code == 200, after_rejection.text
    assert request_id not in [item["id"] for item in after_rejection.json()["requests"]]




def test_approved_insight_correction_is_fulfilled_once_and_preserves_original(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "c" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    from app.models.care import Insight, Correction

    client = TestClient(app)
    session = create_session(client, "Correction recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {
        **caregiver_headers,
        "X-SolenOS-Privacy-Reviewer-Token": "c" * 40,
    }
    db = TestSessionLocal()
    try:
        insight = Insight(
            person_id=session["person"]["id"],
            title="Mobility observation",
            description="The caregiver reported difficulty walking on Monday.",
            insight_type="observation",
            confidence=0.7,
            evidence_ids=[],
            possible_context="The duration and cause are not established.",
        )
        db.add(insight)
        db.commit()
        db.refresh(insight)
        insight_id = insight.id
        original = insight.description
    finally:
        db.close()

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "correction",
            "authority_basis": "self",
            "authority_attested": True,
            "correction_target_type": "insight",
            "correction_target_id": insight_id,
            "correction_text": "The caregiver reported difficulty walking on Tuesday.",
            "correction_reason": "The original weekday was entered incorrectly.",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the identity and correction authority evidence.",
            "authority_evidence_checked": "Requester identity and record authority were checked.",
        },
    )
    assert approved.status_code == 200, approved.text

    fulfilled = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-correction",
        headers=reviewer_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    body = fulfilled.json()
    assert body["status"] == "fulfilled"
    assert body["fulfilment"]["operation"] == "correction"
    assert body["correction"]["original_text"] == original
    assert body["correction"]["corrected_text"].endswith("Tuesday.")

    replay = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-correction",
        headers=reviewer_headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["fulfilment"]["correction_id"] == body["fulfilment"]["correction_id"]

    db = TestSessionLocal()
    try:
        persisted = db.query(Insight).filter(Insight.id == insight_id).one()
        assert persisted.description == "The caregiver reported difficulty walking on Tuesday."
        correction_rows = db.query(Correction).filter(
            Correction.id == body["fulfilment"]["correction_id"]
        ).all()
        assert len(correction_rows) == 1
        assert correction_rows[0].original_text == original
        assert correction_rows[0].corrected_text == persisted.description
    finally:
        db.close()


def test_correction_fulfilment_rejects_unapproved_and_unsupported_targets(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "d" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Correction boundary recipient")
    headers = {
        "Authorization": f"Bearer {session['access_token']}",
        "X-SolenOS-Privacy-Reviewer-Token": "d" * 40,
    }
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=headers,
        json={
            "request_type": "correction",
            "authority_basis": "self",
            "authority_attested": True,
            "correction_target_type": "insight",
            "correction_target_id": "missing-insight",
            "correction_text": "Corrected value",
        },
    )
    # A correction request must resolve its target inside the authenticated
    # care record before it can enter the reviewer workflow.
    assert submitted.status_code == 404, submitted.text
    assert submitted.json()["detail"] == "Correction target was not found in this care record"



def test_approved_export_persists_downloadable_archive_and_excludes_other_caregiver(monkeypatch):
    import io
    import json
    import zipfile
    from datetime import datetime, timedelta, timezone
    from app.models.care import Caregiver, Evidence
    from app.models.collaboration import CommunityPost, CommunityReaction, EvidenceUpload
    from app.models.privacy_export import PrivacyExportArtifact

    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "e" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SOLENOS_PRIVACY_EXPORT_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    client = TestClient(app)
    session = create_session(client, "Export recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {
        **caregiver_headers,
        "X-SolenOS-Privacy-Reviewer-Token": "e" * 40,
    }
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        other_caregiver = Caregiver(
            id="other-caregiver-export-test",
            name="Other caregiver",
            relationship="family caregiver",
            person_id=person_id,
            timezone="UTC",
        )
        db.add(other_caregiver)
        db.flush()
        own_post = CommunityPost(
            id="requester-community-post",
            caregiver_id=caregiver_id,
            content="My own caregiver experience",
        )
        other_post = CommunityPost(
            id="other-caregiver-community-post",
            caregiver_id=other_caregiver.id,
            content="Another caregiver private draft",
        )
        db.add_all([own_post, other_post])
        db.flush()
        db.add_all([
            CommunityReaction(
                id="requester-community-reaction",
                post_id=own_post.id,
                caregiver_id=caregiver_id,
                reaction_type="support",
            ),
            CommunityReaction(
                id="other-caregiver-community-reaction",
                post_id=other_post.id,
                caregiver_id=other_caregiver.id,
                reaction_type="support",
            ),
        ])
        owned = Evidence(
            person_id=person_id,
            type="document",
            source_text="Requester-owned document metadata",
            uploaded_by_caregiver_id=caregiver_id,
            reported_by_caregiver_id=caregiver_id,
        )
        other = Evidence(
            person_id=person_id,
            type="document",
            source_text="Other caregiver private document metadata",
            uploaded_by_caregiver_id=other_caregiver.id,
            reported_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([owned, other])
        db.flush()
        from sqlalchemy import text
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS privacy_export_legacy_probe "
            "(id VARCHAR PRIMARY KEY, person_id VARCHAR, caregiver_id VARCHAR, payload TEXT)"
        ))
        db.execute(text("DELETE FROM privacy_export_legacy_probe"))
        db.execute(text(
            "INSERT INTO privacy_export_legacy_probe (id, person_id, caregiver_id, payload) "
            "VALUES (:id, :person_id, :caregiver_id, :payload)"
        ), [
            {"id": "legacy-owned", "person_id": person_id, "caregiver_id": caregiver_id, "payload": "owned legacy row"},
            {"id": "legacy-other", "person_id": person_id, "caregiver_id": other_caregiver.id, "payload": "other legacy row"},
        ])
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS privacy_export_actor_scope_probe "
            "(id VARCHAR PRIMARY KEY, actor_id VARCHAR, payload TEXT)"
        ))
        db.execute(text("DELETE FROM privacy_export_actor_scope_probe"))
        db.execute(text(
            "INSERT INTO privacy_export_actor_scope_probe (id, actor_id, payload) "
            "VALUES ('actor-only-row', :actor_id, 'must not infer caregiver ownership')"
        ), {"actor_id": caregiver_id})
        db.add_all([
            EvidenceUpload(
                evidence_id=owned.id,
                filename="requester-record.txt",
                mime_type="text/plain",
                content=b"requester document contents",
                size_bytes=len(b"requester document contents"),
                sha256=__import__("hashlib").sha256(b"requester document contents").hexdigest(),
            ),
            EvidenceUpload(
                evidence_id=other.id,
                filename="other-caregiver-private.txt",
                mime_type="text/plain",
                content=b"other caregiver private contents",
                size_bytes=len(b"other caregiver private contents"),
                sha256=__import__("hashlib").sha256(b"other caregiver private contents").hexdigest(),
            ),
        ])
        db.commit()
        owned_id = owned.id
        other_id = other.id
    finally:
        db.close()

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "export",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the export requester's identity and authority.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text

    fulfilled = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-export",
        headers=reviewer_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    result = fulfilled.json()
    assert result["status"] == "fulfilled"
    assert result["fulfilment"]["document_count"] == 1
    assert result["fulfilment"]["size_bytes"] > 0
    assert result["fulfilment"]["sha256"]

    downloaded = client.get(
        f"/api/v1/privacy/requests/{request_id}/export",
        headers=caregiver_headers,
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        unexported = {item["table"]: item["reason"] for item in manifest["unexported_tables"]}
        assert "privacy_export_actor_scope_probe" in unexported
        assert "records/privacy_export_actor_scope_probe.json" not in names
        assert any(name.startswith(f"documents/{owned_id}_") for name in names)
        assert not any(other_id in name for name in names)
        assert not any("other-caregiver-private" in name for name in names)
        evidence_file = json.loads(archive.read("records/evidence.json"))
        exported_ids = {item["id"] for item in evidence_file}
        assert owned_id in exported_ids
        assert other_id not in exported_ids
        assert manifest["document_files"][0]["evidence_id"] == owned_id
        own_posts = json.loads(archive.read("records/community_posts.json"))
        assert [item["id"] for item in own_posts] == ["requester-community-post"]
        assert own_posts[0]["content"] == "My own caregiver experience"
        own_reactions = json.loads(archive.read("records/community_reactions.json"))
        assert [item["id"] for item in own_reactions] == ["requester-community-reaction"]
        legacy_rows = json.loads(archive.read("records/privacy_export_legacy_probe.json"))
        assert [item["id"] for item in legacy_rows] == ["legacy-owned"]
        assert legacy_rows[0]["payload"] == "owned legacy row"

    cleanup_db = TestSessionLocal()
    try:
        from sqlalchemy import text
        cleanup_db.execute(text("DROP TABLE IF EXISTS privacy_export_legacy_probe"))
        cleanup_db.commit()
    finally:
        cleanup_db.close()

    replay = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-export",
        headers=reviewer_headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True

    db = TestSessionLocal()
    try:
        artifact = db.query(PrivacyExportArtifact).filter_by(request_id=request_id).one()
        assert artifact.downloaded_at is not None
        assert artifact.sha256 == result["fulfilment"]["sha256"]
        artifact.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    expired = client.get(
        f"/api/v1/privacy/requests/{request_id}/export",
        headers=caregiver_headers,
    )
    assert expired.status_code == 410


def test_export_download_is_not_available_to_another_caregiver(monkeypatch):
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "f" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SOLENOS_PRIVACY_EXPORT_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    client = TestClient(app)
    first = create_session(client, "First export recipient")
    second = create_session(client, "Second export recipient")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=first_headers,
        json={"request_type": "export", "authority_basis": "self", "authority_attested": True},
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    reviewer_headers = {
        **first_headers,
        "X-SolenOS-Privacy-Reviewer-Token": "f" * 40,
    }
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed identity and authority evidence.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text
    fulfilled = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-export",
        headers=reviewer_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    denied = client.get(
        f"/api/v1/privacy/requests/{request_id}/export",
        headers=second_headers,
    )
    assert denied.status_code == 404



def test_approved_deletion_removes_single_caregiver_record_and_keeps_redacted_receipt(monkeypatch):
    from datetime import datetime, timezone
    from app.models.care import CareEvent, Evidence, Person, Caregiver
    from app.models.collaboration import CommunityPost, CommunityReaction, EvidenceUpload
    from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent
    from app.models.qwen_background_job import QwenBackgroundJob
    from app.models.privacy_delivery import AuditLogEvent
    import json

    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "g" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Deletion recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "g" * 40}
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        evidence = Evidence(
            person_id=person_id,
            type="document",
            source_text="Synthetic deletion test document",
            uploaded_by_caregiver_id=caregiver_id,
        )
        db.add(evidence)
        db.flush()
        upload_bytes = b"synthetic deletion test bytes"
        upload = EvidenceUpload(
            evidence_id=evidence.id,
            filename="delete-me.txt",
            mime_type="text/plain",
            content=upload_bytes,
            size_bytes=len(upload_bytes),
            sha256=__import__("hashlib").sha256(upload_bytes).hexdigest(),
        )
        event = CareEvent(
            person_id=person_id,
            event_type="test_observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Synthetic deletion event",
            description="This record must be erased.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver_id,
        )
        analysis = CaptureAnalysis(
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_evidence_id=evidence.id,
            source_text="Synthetic extracted document text to erase.",
            extraction_status="available",
            candidates={"kind": "document_extraction_v1", "observations": []},
        )
        db.add(analysis)
        db.flush()
        review_event = DocumentReviewEvent(
            analysis_id=analysis.id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_evidence_id=evidence.id,
            candidate_index=0,
            decision_version=1,
            decision="reject",
            rationale="Synthetic caregiver review decision to erase.",
            before_state={"review_status": "pending_review"},
            after_state={"review_status": "rejected"},
        )
        processing_job = QwenBackgroundJob(
            person_id=person_id,
            caregiver_id=caregiver_id,
            task_text="Process saved document for caregiver review.",
            context_text="",
            evidence_ids_json=json.dumps([evidence.id]),
            workflow="document_intake",
            status="queued",
        )
        post = CommunityPost(
            id="deletion-community-post",
            caregiver_id=caregiver_id,
            content="Caregiver-authored content to erase",
        )
        db.add(post)
        db.flush()
        reaction = CommunityReaction(
            id="deletion-community-reaction",
            post_id=post.id,
            caregiver_id=caregiver_id,
            reaction_type="support",
        )
        from sqlalchemy import text
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS privacy_deletion_legacy_probe "
            "(id VARCHAR PRIMARY KEY, person_id VARCHAR, caregiver_id VARCHAR, payload TEXT)"
        ))
        db.execute(text("DELETE FROM privacy_deletion_legacy_probe"))
        db.execute(text(
            "INSERT INTO privacy_deletion_legacy_probe (id, person_id, caregiver_id, payload) "
            "VALUES ('legacy-delete-row', :person_id, :caregiver_id, 'private legacy data')"
        ), {"person_id": person_id, "caregiver_id": caregiver_id})
        # This second legacy table has no direct subject identifier. It must
        # still be erased before its parent because SET NULL would otherwise
        # preserve the dependent personal data after root deletion.
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS privacy_deletion_indirect_probe ("
            "id VARCHAR PRIMARY KEY, parent_ref VARCHAR, payload TEXT, "
            "FOREIGN KEY (parent_ref) REFERENCES privacy_deletion_legacy_probe(id) ON DELETE SET NULL)"
        ))
        db.execute(text("DELETE FROM privacy_deletion_indirect_probe"))
        db.execute(text(
            "INSERT INTO privacy_deletion_indirect_probe (id, parent_ref, payload) "
            "VALUES ('indirect-delete-row', 'legacy-delete-row', 'indirect private data')"
        ))
        db.execute(text(
            "CREATE TABLE IF NOT EXISTS privacy_deletion_fk_probe ("
            "record_key VARCHAR NOT NULL, person_ref VARCHAR NOT NULL, payload TEXT, "
            "PRIMARY KEY (record_key, person_ref), "
            "FOREIGN KEY (person_ref) REFERENCES persons(id))"
        ))
        db.execute(text("DELETE FROM privacy_deletion_fk_probe"))
        db.execute(text(
            "INSERT INTO privacy_deletion_fk_probe (record_key, person_ref, payload) "
            "VALUES ('composite-dependent-row', :person_id, 'private dependent data')"
        ), {"person_id": person_id})
        db.add_all([upload, event, reaction, review_event, processing_job])
        db.commit()
        evidence_id = evidence.id
        event_id = event.id
        analysis_id = analysis.id
        review_event_id = review_event.id
        processing_job_id = processing_job.id
    finally:
        db.close()

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "deletion",
            "authority_basis": "self",
            "authority_attested": True,
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the identity and authority evidence for erasure.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text
    monkeypatch.setenv("SOLENOS_PRIVACY_DELETION_REVIEWER_TOKEN", "g2" * 20)
    monkeypatch.setenv("SOLENOS_PRIVACY_DELETION_REVIEWER_ID", "independent-erasure-reviewer")
    deletion_reviewer_headers = {"X-SolenOS-Privacy-Deletion-Reviewer-Token": "g2" * 20}

    # The approving reviewer's credential must not be accepted for destructive erasure.
    primary_credential_reuse = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-deletion",
        headers={"X-SolenOS-Privacy-Deletion-Reviewer-Token": "g" * 40},
        json={
            "backup_erasure_verified": True,
            "backup_retention_evidence": "Verified all backups containing this synthetic test record have expired.",
        },
    )
    assert primary_credential_reuse.status_code == 401, primary_credential_reuse.text

    fulfilled = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-deletion",
        headers=deletion_reviewer_headers,
        json={
            "backup_erasure_verified": True,
            "backup_retention_evidence": "Verified all backups containing this synthetic test record have expired.",
        },
    )
    assert fulfilled.status_code == 200, fulfilled.text
    body = fulfilled.json()
    assert body["status"] == "fulfilled"
    assert body["fulfilment"]["operation"] == "deletion"
    assert body["fulfilment"]["receipt_id"]

    db = TestSessionLocal()
    try:
        assert db.query(Person).filter(Person.id == person_id).count() == 0
        assert db.query(Caregiver).filter(Caregiver.id == caregiver_id).count() == 0
        assert db.query(Evidence).filter(Evidence.id == evidence_id).count() == 0
        assert db.query(EvidenceUpload).filter(EvidenceUpload.evidence_id == evidence_id).count() == 0
        assert db.query(CaptureAnalysis).filter(CaptureAnalysis.id == analysis_id).count() == 0
        assert db.query(DocumentReviewEvent).filter(DocumentReviewEvent.id == review_event_id).count() == 0
        assert db.query(QwenBackgroundJob).filter(QwenBackgroundJob.id == processing_job_id).count() == 0
        assert db.query(CareEvent).filter(CareEvent.id == event_id).count() == 0
        assert db.query(CommunityPost).filter(CommunityPost.id == "deletion-community-post").count() == 0
        assert db.query(CommunityReaction).filter(CommunityReaction.id == "deletion-community-reaction").count() == 0
        legacy_remaining = db.execute(text(
            "SELECT COUNT(*) FROM privacy_deletion_legacy_probe WHERE id = 'legacy-delete-row'"
        )).scalar_one()
        assert legacy_remaining == 0
        indirect_remaining = db.execute(text(
            "SELECT COUNT(*) FROM privacy_deletion_indirect_probe WHERE id = 'indirect-delete-row'"
        )).scalar_one()
        assert indirect_remaining == 0
        db.execute(text("DROP TABLE IF EXISTS privacy_deletion_indirect_probe"))
        dependent_remaining = db.execute(text(
            "SELECT COUNT(*) FROM privacy_deletion_fk_probe WHERE person_ref = :person_id"
        ), {"person_id": person_id}).scalar_one()
        assert dependent_remaining == 0
        db.execute(text("DROP TABLE IF EXISTS privacy_deletion_fk_probe"))
        db.execute(text("DROP TABLE IF EXISTS privacy_deletion_legacy_probe"))
        receipt = db.query(DataSubjectRequest).filter(DataSubjectRequest.id == request_id).one()
        assert receipt.request_status == "fulfilled"
        assert receipt.person_id is None
        assert receipt.caregiver_id is None
        assert receipt.subject_id is None
        assert receipt.requester_id.startswith("erased:")
        audit = db.query(AuditLogEvent).filter(
            AuditLogEvent.target_id == body["fulfilment"]["receipt_id"]
        ).one()
        assert audit.action == "privacy_request.deletion_fulfilled"
        assert audit.person_id is None
        assert audit.caregiver_id is None
    finally:
        db.close()

    # The erased bearer capability no longer authorizes any caregiver API.
    inaccessible = client.get("/api/v1/privacy/requests", headers=caregiver_headers)
    assert inaccessible.status_code == 401

    # Reviewer-only retry is idempotent and does not recreate or repeat deletion.
    replay = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-deletion",
        headers=deletion_reviewer_headers,
        json={
            "backup_erasure_verified": True,
            "backup_retention_evidence": "Verified all backups containing this synthetic test record have expired.",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["idempotent_replay"] is True


def test_deletion_fails_closed_for_shared_care_record(monkeypatch):
    from app.models.care import Caregiver, Person

    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "h" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Shared deletion recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "h" * 40}
    db = TestSessionLocal()
    try:
        db.add(Caregiver(
            id="second-caregiver-shared-delete",
            name="Second caregiver",
            relationship="family caregiver",
            person_id=session["person"]["id"],
            timezone="UTC",
        ))
        db.commit()
    finally:
        db.close()
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={"request_type": "deletion", "authority_basis": "self", "authority_attested": True},
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the request and authority evidence.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text
    monkeypatch.setenv("SOLENOS_PRIVACY_DELETION_REVIEWER_TOKEN", "h2" * 20)
    monkeypatch.setenv("SOLENOS_PRIVACY_DELETION_REVIEWER_ID", "independent-erasure-reviewer")
    deletion_reviewer_headers = {"X-SolenOS-Privacy-Deletion-Reviewer-Token": "h2" * 20}
    denied = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-deletion",
        headers=deletion_reviewer_headers,
        json={
            "backup_erasure_verified": True,
            "backup_retention_evidence": "Verified all backups containing this synthetic test record have expired.",
        },
    )
    assert denied.status_code == 409
    db = TestSessionLocal()
    try:
        assert db.query(Person).filter(Person.id == session["person"]["id"]).count() == 1
        still_pending = db.query(DataSubjectRequest).filter(DataSubjectRequest.id == request_id).one()
        assert still_pending.request_status == "approved_pending_fulfilment_assessment"
    finally:
        db.close()


def test_deletion_fails_closed_when_legal_hold_is_active(monkeypatch):
    from sqlalchemy import text
    from app.models.care import Person

    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "i" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Legal hold recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {"X-SolenOS-Privacy-Reviewer-Token": "i" * 40}
    db = TestSessionLocal()
    try:
        db.execute(text("DROP TABLE IF EXISTS legal_holds"))
        db.execute(text(
            "CREATE TABLE legal_holds (id VARCHAR PRIMARY KEY, subject_id VARCHAR, hold_status VARCHAR)"
        ))
        db.execute(text(
            "INSERT INTO legal_holds (id, subject_id, hold_status) VALUES ('hold-1', :person_id, 'active')"
        ), {"person_id": session["person"]["id"]})
        db.commit()
    finally:
        db.close()
    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={"request_type": "deletion", "authority_basis": "self", "authority_attested": True},
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed the request and authority evidence.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text
    monkeypatch.setenv("SOLENOS_PRIVACY_DELETION_REVIEWER_TOKEN", "i2" * 20)
    monkeypatch.setenv("SOLENOS_PRIVACY_DELETION_REVIEWER_ID", "independent-erasure-reviewer")
    deletion_reviewer_headers = {"X-SolenOS-Privacy-Deletion-Reviewer-Token": "i2" * 20}
    denied = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-deletion",
        headers=deletion_reviewer_headers,
        json={
            "backup_erasure_verified": True,
            "backup_retention_evidence": "Verified all backups containing this synthetic test record have expired.",
        },
    )
    assert denied.status_code == 409
    db = TestSessionLocal()
    try:
        assert db.query(Person).filter(Person.id == session["person"]["id"]).count() == 1
    finally:
        db.close()



def test_approved_care_event_correction_creates_lcr_revision(monkeypatch):
    import json
    from datetime import datetime, timezone
    from app.models.care import CareEvent, Correction
    from app.models.care_continuity import CareStateRevision

    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "j" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Care event correction recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {
        **caregiver_headers,
        "X-SolenOS-Privacy-Reviewer-Token": "j" * 40,
    }
    db = TestSessionLocal()
    try:
        event = CareEvent(
            person_id=session["person"]["id"],
            event_type="mobility_observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Mobility observation",
            description="Caregiver recorded the observation on Monday.",
            evidence_ids=[],
            created_by_caregiver_id=session["caregiver"]["id"],
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        event_id = event.id
    finally:
        db.close()

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "correction",
            "authority_basis": "self",
            "authority_attested": True,
            "correction_target_type": "care_event_description",
            "correction_target_id": event_id,
            "correction_text": "Caregiver recorded the observation on Tuesday.",
            "correction_reason": "The weekday was entered incorrectly.",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed identity and authority evidence.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text
    fulfilled = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-correction",
        headers=reviewer_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json()["correction"]["target_type"] == "care_event"
    db = TestSessionLocal()
    try:
        persisted = db.query(CareEvent).filter(CareEvent.id == event_id).one()
        assert persisted.description == "Caregiver recorded the observation on Tuesday."
        revisions = db.query(CareStateRevision).filter(
            CareStateRevision.target_type == "care_events",
            CareStateRevision.target_id == event_id,
        ).order_by(CareStateRevision.revision_number.asc()).all()
        assert len(revisions) == 2
        assert revisions[1].change_kind == "updated"
        before = json.loads(revisions[1].before_state)
        after = json.loads(revisions[1].after_state)
        assert before["description"] == "Caregiver recorded the observation on Monday."
        assert after["description"] == persisted.description
        correction = db.query(Correction).filter(
            Correction.id == fulfilled.json()["fulfilment"]["correction_id"]
        ).one()
        assert correction.original_text == before["description"]
    finally:
        db.close()

def test_approved_evidence_source_correction_preserves_immutable_original(monkeypatch):
    from app.models.care import Evidence, Correction

    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_TOKEN", "k" * 40)
    monkeypatch.setenv("SOLENOS_PRIVACY_REVIEWER_ID", "privacy-reviewer")
    client = TestClient(app)
    session = create_session(client, "Evidence correction recipient")
    caregiver_headers = {"Authorization": f"Bearer {session['access_token']}"}
    reviewer_headers = {
        **caregiver_headers,
        "X-SolenOS-Privacy-Reviewer-Token": "k" * 40,
    }
    db = TestSessionLocal()
    try:
        evidence = Evidence(
            person_id=session["person"]["id"],
            type="observation",
            source_text="The caregiver said the appointment was Monday.",
            uploaded_by_caregiver_id=session["caregiver"]["id"],
        )
        db.add(evidence)
        db.commit()
        db.refresh(evidence)
        evidence_id = evidence.id
        original_text = evidence.source_text
        original_hash = evidence.source_text_sha256
    finally:
        db.close()

    submitted = client.post(
        "/api/v1/privacy/requests",
        headers=caregiver_headers,
        json={
            "request_type": "correction",
            "authority_basis": "self",
            "authority_attested": True,
            "correction_target_type": "evidence_source",
            "correction_target_id": evidence_id,
            "correction_text": "The caregiver said the appointment was Tuesday.",
            "correction_reason": "The original weekday was transcribed incorrectly.",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]
    approved = client.post(
        f"/api/v1/privacy/requests/{request_id}/review",
        headers=reviewer_headers,
        json={
            "decision": "approve",
            "review_basis": "Reviewed identity and correction authority.",
            "authority_evidence_checked": "Identity and authority evidence checked.",
        },
    )
    assert approved.status_code == 200, approved.text
    fulfilled = client.post(
        f"/api/v1/privacy/requests/{request_id}/fulfil-correction",
        headers=reviewer_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json()["correction"]["target_type"] == "evidence_source"

    db = TestSessionLocal()
    try:
        persisted = db.query(Evidence).filter(Evidence.id == evidence_id).one()
        assert persisted.source_text == original_text
        assert persisted.source_text_sha256 == original_hash
        correction = db.query(Correction).filter(
            Correction.id == fulfilled.json()["fulfilment"]["correction_id"]
        ).one()
        assert correction.target_type == "evidence_source"
        assert correction.target_id == evidence_id
        assert correction.original_text == original_text
        assert correction.corrected_text == "The caregiver said the appointment was Tuesday."
    finally:
        db.close()



def test_export_manifest_discloses_unscoped_orm_registered_tables():
    import io
    import zipfile
    from sqlalchemy import Column, String, Table
    from app.api.privacy_requests import _build_caregiver_export

    table = Table(
        "privacy_export_orm_unscoped_probe",
        Base.metadata,
        Column("id", String, primary_key=True),
        Column("payload", String),
    )
    table.create(bind=engine)
    db = TestSessionLocal()
    try:
        db.execute(table.insert().values(
            id="unscoped-orm-row",
            payload="must not be silently omitted",
        ))
        db.commit()

        archive_bytes, manifest = _build_caregiver_export(
            db,
            person_id="privacy-export-test-person",
            caregiver_id="privacy-export-test-caregiver",
        )

        unexported = {item["table"] for item in manifest["unexported_tables"]}
        assert "privacy_export_orm_unscoped_probe" in unexported
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            assert "records/privacy_export_orm_unscoped_probe.json" not in archive.namelist()
    finally:
        db.close()
        table.drop(bind=engine, checkfirst=True)
        Base.metadata.remove(table)
