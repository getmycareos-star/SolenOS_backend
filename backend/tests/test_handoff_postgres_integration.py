"""PostgreSQL-only handoff persistence, approval, sharing, and revocation integration.

The Qwen generator is isolated here; this test proves PostgreSQL persistence and
existing sharing infrastructure, not live model inference. Live Qwen is a separate
image-backed CI gate.
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app


pytestmark = pytest.mark.skipif(
    SessionLocal.kw["bind"].dialect.name != "postgresql",
    reason="Requires the PostgreSQL migration integration job",
)


def _create_session(client, caregiver_name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "PostgreSQL handoff test person", "caregiver_name": caregiver_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _add_linked_recipient(person_id):
    from app.models.anonymous_session import AnonymousSession
    from app.models.care import Caregiver

    caregiver_id = str(uuid.uuid4())
    token = "postgres-handoff-" + str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(Caregiver(
            id=caregiver_id,
            name="PostgreSQL receiving caregiver",
            relationship="family caregiver",
            person_id=person_id,
            timezone="UTC",
        ))
        db.flush()
        db.add(AnonymousSession(
            id=str(uuid.uuid4()),
            person_id=person_id,
            caregiver_id=caregiver_id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=now + timedelta(hours=2),
            created_at=now,
        ))
        db.commit()
    finally:
        db.close()
    return caregiver_id, token


def _accept_consent(person_id, caregiver_id):
    from app.models.collaboration import ConsentRecord

    db = SessionLocal()
    try:
        consent = db.query(ConsentRecord).filter_by(
            person_id=person_id, caregiver_id=caregiver_id,
        ).first()
        if consent is None:
            consent = ConsentRecord(person_id=person_id, caregiver_id=caregiver_id)
            db.add(consent)
        consent.medical_disclaimer_acknowledged = True
        consent.privacy_model_acknowledged = True
        consent.multi_caregiver_acknowledged = True
        consent.no_advertising_acknowledged = True
        consent.accepted_terms_version = "2026-10"
        consent.accepted_at = datetime.now(timezone.utc)
        consent.revoked_at = None
        db.commit()
    finally:
        db.close()


def test_postgres_handoff_sources_persist_approve_share_acknowledge_and_revoke(monkeypatch):
    import app.api.handoff_drafts as handoff_drafts
    from app.models.care import CareEvent, Evidence
    from app.models.capture_analysis import CaptureAnalysis
    from app.models.collaboration import EvidenceUpload
    from app.models.privacy_delivery import (
        AuditLogEvent,
        HandoffDraft,
        HandoffDraftRevision,
        HandoffRecord,
    )
    from app.models.notifications import CareNotification

    client = TestClient(app)
    owner = _create_session(client, "PostgreSQL initiating caregiver")
    recipient_id, recipient_token = _add_linked_recipient(owner["person"]["id"])
    _accept_consent(owner["person"]["id"], owner["caregiver"]["id"])
    _accept_consent(owner["person"]["id"], recipient_id)
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    recipient_headers = {"Authorization": f"Bearer {recipient_token}"}

    observation = "Caregiver reports that lunch was only partly eaten."
    daily_evidence_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    analysis_id = str(uuid.uuid4())
    original_document = b"Clinic letter: follow-up appointment date is 12 December."
    document_sha = hashlib.sha256(original_document).hexdigest()
    db = SessionLocal()
    try:
        db.add_all([
            Evidence(
                id=daily_evidence_id,
                person_id=owner["person"]["id"],
                type="caregiver_observation",
                source_text=observation,
                uploaded_by_caregiver_id=owner["caregiver"]["id"],
                reported_by_caregiver_id=owner["caregiver"]["id"],
                reported_by_label="Family caregiver",
            ),
            Evidence(
                id=document_id,
                person_id=owner["person"]["id"],
                type="document",
                source_text="Uploaded document record; extracted content is stored separately.",
                uploaded_by_caregiver_id=owner["caregiver"]["id"],
                original_source_reference="clinic/letter-12",
            ),
            CareEvent(
                id=event_id,
                person_id=owner["person"]["id"],
                event_type="structured_daily_capture",
                status="recorded",
                occurred_at=datetime.now(timezone.utc) - timedelta(hours=2),
                occurred_at_timezone="Africa/Lagos",
                title="Lunch observation",
                description=observation,
                evidence_ids=[daily_evidence_id],
                created_by_caregiver_id=owner["caregiver"]["id"],
            ),
        ])
        db.flush()
        db.add(EvidenceUpload(
            evidence_id=document_id,
            filename="clinic-letter.txt",
            mime_type="text/plain",
            content=original_document,
            size_bytes=len(original_document),
            sha256=document_sha,
        ))
        db.add(CaptureAnalysis(
            id=analysis_id,
            person_id=owner["person"]["id"],
            caregiver_id=owner["caregiver"]["id"],
            source_evidence_id=document_id,
            source_text=original_document.decode("utf-8"),
            extraction_status="available",
            candidates={
                "kind": "document_extraction_v1",
                "binary_sha256": document_sha,
                "extraction_method": "server_utf8_text",
            },
            review_history=[],
        ))
        db.commit()
    finally:
        db.close()

    def grounded_qwen(records, purpose, audience):
        daily = next(row for row in records if row["source_type"] == "daily_care_observation")
        document = next(row for row in records if row["source_type"] == "document_extracted_text")
        return {
            "status": "generated",
            "provider": "ollama",
            "model": "qwen-postgres-integration-fixture",
            "sections": [
                {
                    "section": "Daily Care information",
                    "source_ref": f"{daily['source_table']}:{daily['source_id']}",
                    "text": daily["text"],
                    "epistemic_status": daily["epistemic_status"],
                    "record_status": daily["record_status"],
                    "event_at": daily["event_at"],
                    "recorded_at": daily["recorded_at"],
                    "stale_warning": daily["stale_warning"],
                },
                {
                    "section": "Document-derived information",
                    "source_ref": f"{document['source_table']}:{document['source_id']}",
                    "text": document["text"],
                    "epistemic_status": document["epistemic_status"],
                    "record_status": document["record_status"],
                    "event_at": document["event_at"],
                    "recorded_at": document["recorded_at"],
                    "stale_warning": document["stale_warning"],
                },
            ],
        }

    monkeypatch.setattr(handoff_drafts, "_generate_grounded_handoff", grounded_qwen)
    prepare_headers = {**owner_headers, "X-Request-ID": "postgres-handoff-prepare-001"}
    prepared = client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=prepare_headers,
        json={"purpose": "family_transition", "audience": "family", "window_days": 30},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["draft_id"]
    outsider = _create_session(client, "Unrelated caregiver")
    outsider_headers = {"Authorization": f"Bearer {outsider['access_token']}"}
    assert client.get(f"/api/v1/handoff-drafts/{draft_id}", headers=outsider_headers).status_code == 404
    assert client.put(f"/api/v1/handoff-drafts/{draft_id}", headers=outsider_headers, json={"content": {}}).status_code == 404
    assert client.post(f"/api/v1/handoff-drafts/{draft_id}/approve", headers=outsider_headers, json={"recipient_caregiver_ids": [owner["caregiver"]["id"]], "scope": ["lcr_summary"], "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}).status_code == 404
    assert prepared.json()["generation_status"] == "generated"
    assert prepared.json()["source_statuses"]["daily_care"]["status"] == "available"
    assert prepared.json()["source_statuses"]["documents"]["status"] == "available"
    assert prepared.json()["source_references"]
    assert any(row["source_id"] == event_id for row in prepared.json()["source_references"])
    assert any(row["source_id"] == analysis_id for row in prepared.json()["source_references"])

    # The persisted identity and revision survive a new database session.
    db = SessionLocal()
    try:
        draft = db.query(HandoffDraft).filter_by(id=draft_id).one()
        assert draft.person_id == owner["person"]["id"]
        assert draft.caregiver_id == owner["caregiver"]["id"]
        assert draft.generation_metadata["provider"] == "ollama"
        assert draft.generation_metadata["model"] == "qwen-postgres-integration-fixture"
        assert db.query(HandoffDraftRevision).filter_by(draft_id=draft_id).count() == 1
        prepare_audit = db.query(AuditLogEvent).filter_by(
            action="handoff_draft.prepare", target_id=draft_id,
        ).one()
        assert prepare_audit.person_id == owner["person"]["id"]
    finally:
        db.close()

    retrieved = client.get(f"/api/v1/handoff-drafts/{draft_id}", headers=owner_headers)
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["source_freshness"]["status"] == "current", retrieved.json()["source_freshness"]
    edited_content = {
        "template_sections": retrieved.json()["template_sections"],
        "sections": retrieved.json()["sections"],
        "generation": retrieved.json()["generation"],
        "caregiver_notes": ["The family will confirm the next appointment date."],
    }
    saved = client.put(
        f"/api/v1/handoff-drafts/{draft_id}",
        headers=owner_headers,
        json={"content": edited_content},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision_number"] == 2
    history = client.get(f"/api/v1/handoff-drafts/{draft_id}", headers=owner_headers).json()["revision_history"]
    assert len(history) == 2
    assert history[0]["content"]["generation"]["sections"]
    assert history[0]["content"].get("caregiver_notes") == []
    assert history[1]["content"]["caregiver_notes"][0]["text"] == "The family will confirm the next appointment date."

    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    approval = client.post(
        f"/api/v1/handoff-drafts/{draft_id}/approve",
        headers=owner_headers,
        json={
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary", "documents"],
            "expires_at": expiry,
        },
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["review_status"] == "approved_for_sharing"
    approval_retry = client.post(
        f"/api/v1/handoff-drafts/{draft_id}/approve",
        headers=owner_headers,
        json={
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary", "documents"],
            "expires_at": expiry,
        },
    )
    assert approval_retry.status_code == 200, approval_retry.text
    assert approval_retry.json()["review_status"] == "approved_for_sharing"

    shared = client.post(
        "/api/v1/handoffs",
        headers={**owner_headers, "X-Request-ID": "postgres-handoff-share-001"},
        json={
            "person_id": owner["person"]["id"],
            "draft_id": draft_id,
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary", "documents"],
            "expires_at": expiry,
        },
    )
    assert shared.status_code == 201, shared.text
    handoff_id = shared.json()["id"]
    shared_retry = client.post(
        "/api/v1/handoffs",
        headers={**owner_headers, "X-Request-ID": "postgres-handoff-share-001"},
        json={
            "person_id": owner["person"]["id"],
            "draft_id": draft_id,
            "recipient_caregiver_ids": [recipient_id],
            "scope": ["lcr_summary", "documents"],
            "expires_at": expiry,
        },
    )
    assert shared_retry.status_code in {200, 201}, shared_retry.text
    assert shared_retry.json()["id"] == handoff_id
    db = SessionLocal()
    try:
        handoff = db.query(HandoffRecord).filter_by(id=handoff_id).one()
        draft = db.query(HandoffDraft).filter_by(id=draft_id).one()
        assert handoff.summary_snapshot["draft_id"] == draft_id
        assert handoff.summary_snapshot["draft_revision"] == 2
        assert handoff.summary_snapshot["review_attestation"] == "server_verified_persisted_draft_approval"
        assert draft.review_status == "shared"
        notification = db.query(CareNotification).filter_by(
            person_id=owner["person"]["id"],
            caregiver_id=recipient_id,
            source_event_id=handoff_id,
            category="handoff",
        ).one()
        assert notification.title == "New care handoff"
        assert observation not in notification.body
    finally:
        db.close()

    assert client.get(f"/api/v1/handoffs/{handoff_id}/summary", headers=outsider_headers).status_code == 404
    recipient_summary = client.get(f"/api/v1/handoffs/{handoff_id}/summary", headers=recipient_headers)
    assert recipient_summary.status_code == 200, recipient_summary.text
    assert observation in recipient_summary.json()["summary"]["sections"]["lcr_summary"]
    assert "not independently verified" in recipient_summary.json()["summary"]["sections"]["lcr_summary"].lower()
    shared_snapshot = recipient_summary.json()["summary"]
    assert shared_snapshot["source_coverage"]["daily_care"]["status"] == "available"
    assert shared_snapshot["source_coverage"]["documents"]["status"] == "available"
    assert len(shared_snapshot["source_references"]) == 2

    accepted = client.post(f"/api/v1/handoffs/{handoff_id}/accept", headers=recipient_headers)
    assert accepted.status_code == 200, accepted.text
    acknowledged = client.post(
        f"/api/v1/handoffs/{handoff_id}/acknowledge",
        headers=recipient_headers,
        json={"acknowledgement": "Received the handoff."},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["acknowledged"] is True

    revoked = client.post(f"/api/v1/handoffs/{handoff_id}/revoke", headers=owner_headers)
    assert revoked.status_code == 200, revoked.text
    blocked = client.get(f"/api/v1/handoffs/{handoff_id}/summary", headers=recipient_headers)
    assert blocked.status_code == 404


def test_postgres_handoff_requires_api_consent_and_follows_recipient_acceptance_flow():
    """Verify consent is accepted through the caregiver API before sharing."""
    client = TestClient(app)
    owner = _create_session(client, "Consent API initiating caregiver")
    recipient_id, recipient_token = _add_linked_recipient(owner["person"]["id"])
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    recipient_headers = {"Authorization": f"Bearer {recipient_token}"}
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    handoff_payload = {
        "person_id": owner["person"]["id"],
        "recipient_caregiver_ids": [recipient_id],
        "scope": ["lcr_summary", "open_loops"],
        "expires_at": expires_at,
    }

    denied = client.post("/api/v1/handoffs", headers=owner_headers, json=handoff_payload)
    assert denied.status_code == 403, denied.text
    assert "consent" in denied.text.casefold()

    def accept_required_consent(headers, caregiver_id):
        response = client.post("/api/v1/consent", headers=headers, json={
            "person_id": owner["person"]["id"],
            "caregiver_id": caregiver_id,
            "medical_disclaimer_acknowledged": True,
            "privacy_model_acknowledged": True,
            "multi_caregiver_acknowledged": True,
            "no_advertising_acknowledged": True,
            "data_improvement_consent": False,
            "accepted_terms_version": "2026-10",
        })
        assert response.status_code == 200, response.text
        return response.json()

    sender_consent = accept_required_consent(owner_headers, owner["caregiver"]["id"])
    recipient_consent = accept_required_consent(recipient_headers, recipient_id)
    assert sender_consent["verified"] is True
    assert recipient_consent["verified"] is True

    directory = client.get("/api/v1/handoffs/directory/recipients", headers=owner_headers)
    assert directory.status_code == 200, directory.text
    assert any(
        item["caregiver_id"] == recipient_id and item["can_receive_handoff"] is True
        for item in directory.json()["items"]
    )

    created = client.post("/api/v1/handoffs", headers=owner_headers, json=handoff_payload)
    assert created.status_code == 201, created.text
    handoff_id = created.json()["id"]

    inbox = client.get("/api/v1/handoffs/inbox", headers=recipient_headers)
    assert inbox.status_code == 200, inbox.text
    assert any(item["id"] == handoff_id for item in inbox.json()["items"])

    accepted = client.post(f"/api/v1/handoffs/{handoff_id}/accept", headers=recipient_headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] in {"accepted", "active", "acknowledged"}
