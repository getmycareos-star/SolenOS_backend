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


def _accept_consent(client, person_id, caregiver_id, access_token):
    response = client.post(
        "/api/v1/consent",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "medical_disclaimer_acknowledged": True,
            "privacy_model_acknowledged": True,
            "multi_caregiver_acknowledged": True,
            "no_advertising_acknowledged": True,
            "data_improvement_consent": False,
            "accepted_terms_version": "2026-10",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["verified"] is True


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
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
    recipient_headers = {"Authorization": f"Bearer {recipient_token}"}
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    consent_gate_payload = {
        "person_id": owner["person"]["id"],
        "recipient_caregiver_ids": [recipient_id],
        "scope": ["lcr_summary"],
        "expires_at": expiry,
    }
    sender_without_consent = client.post("/api/v1/handoffs", headers=owner_headers, json=consent_gate_payload)
    assert sender_without_consent.status_code == 403, sender_without_consent.text

    _accept_consent(client, owner["person"]["id"], owner["caregiver"]["id"], owner["access_token"])
    recipient_without_consent = client.post("/api/v1/handoffs", headers=owner_headers, json=consent_gate_payload)
    assert recipient_without_consent.status_code == 403, recipient_without_consent.text
    inbox_without_consent = client.get("/api/v1/handoffs/inbox", headers=recipient_headers)
    assert inbox_without_consent.status_code == 403, inbox_without_consent.text

    _accept_consent(client, owner["person"]["id"], recipient_id, recipient_token)

    observation = "Caregiver reports that lunch was only partly eaten."
    daily_evidence_id = str(uuid.uuid4())
    document_id = str(uuid.uuid4())
    shared_document_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    analysis_id = str(uuid.uuid4())
    shared_analysis_id = str(uuid.uuid4())
    original_document = b"Clinic letter: follow-up appointment date is 12 December."
    shared_original_document = b"Older family caregiver document: current mobility support instructions."
    document_sha = hashlib.sha256(original_document).hexdigest()
    shared_document_sha = hashlib.sha256(shared_original_document).hexdigest()
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
            Evidence(
                id=shared_document_id,
                person_id=owner["person"]["id"],
                type="document",
                source_text="Older family caregiver document; extraction stored separately.",
                uploaded_by_caregiver_id=recipient_id,
                original_source_reference="family/mobility-support",
                uploaded_at=datetime.now(timezone.utc) - timedelta(days=45),
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
        db.add_all([
            EvidenceUpload(
                evidence_id=document_id,
                filename="clinic-letter.txt",
                mime_type="text/plain",
                content=original_document,
                size_bytes=len(original_document),
                sha256=document_sha,
            ),
            EvidenceUpload(
                evidence_id=shared_document_id,
                filename="mobility-support.txt",
                mime_type="text/plain",
                content=shared_original_document,
                size_bytes=len(shared_original_document),
                sha256=shared_document_sha,
                created_at=datetime.now(timezone.utc) - timedelta(days=45),
            ),
            CaptureAnalysis(
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
            ),
            CaptureAnalysis(
                id=shared_analysis_id,
                person_id=owner["person"]["id"],
                caregiver_id=recipient_id,
                source_evidence_id=shared_document_id,
                source_text=shared_original_document.decode("utf-8"),
                extraction_status="available",
                candidates={
                    "kind": "document_extraction_v1",
                    "binary_sha256": shared_document_sha,
                    "extraction_method": "server_utf8_text",
                },
                review_history=[],
                created_at=datetime.now(timezone.utc) - timedelta(days=45),
            ),
        ])
        db.commit()
    finally:
        db.close()

    def grounded_qwen(records, purpose, audience):
        daily = next((row for row in records if row["source_type"] == "daily_care_observation"), None)
        document = next((row for row in records if row["source_type"] == "document_extracted_text"), None)
        if daily is None or document is None:
            return {"status": "not_generated_no_fixture_sources", "provider": "ollama", "model": "qwen-postgres-integration-fixture", "sections": []}
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
    assert prepared.json()["draft_persisted"] is True
    assert prepared.json()["read_only"] is True
    assert prepared.json()["updates_lcr"] is False
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
    assert any(row["source_id"] == shared_analysis_id for row in prepared.json()["source_references"])
    assert any(row["source_id"] == shared_document_id for row in prepared.json()["source_references"])

    # The reporting window changes period events, not older same-person context.
    for window_days in (7, 14, 30):
        period_result = client.post(
            "/api/v1/handoff-drafts/prepare",
            headers=owner_headers,
            json={"purpose": "family_transition", "audience": "family", "window_days": window_days},
        )
        assert period_result.status_code == 200, period_result.text
        period_refs = period_result.json()["source_references"]
        assert any(row["source_id"] == shared_document_id for row in period_refs)
        assert any(row["source_id"] == event_id for row in period_refs)

    # An unrelated care recipient cannot retrieve the original person's documents.
    unrelated_handoff = client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=outsider_headers,
        json={"purpose": "family_transition", "audience": "family", "window_days": 30},
    )
    assert unrelated_handoff.status_code == 200, unrelated_handoff.text
    assert unrelated_handoff.json()["source_statuses"]["documents"]["status"] == "empty"
    assert all(
        row["source_id"] != shared_document_id
        for row in unrelated_handoff.json()["source_references"]
    )

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
    assert retrieved.json()["source_freshness"]["status"] == "current"
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
