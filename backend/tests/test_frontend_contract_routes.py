import base64
import importlib
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.collaboration import CommunityPost, ConversationMessage, ConversationSession, CareNotification, CareNotificationPreference
from app.models.care import CareEvent, Evidence

main_module = importlib.import_module("app.main")
app = main_module.app
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_client(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    first = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Family caregiver"})
    second = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Other caregiver"})
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    return (
        client,
        first.json()["access_token"],
        first.json()["person"]["id"],
        first.json()["caregiver"]["id"],
        second.json()["access_token"],
        second.json()["person"]["id"],
    )


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_community_post_feed_reaction_persist_and_session_attribution(monkeypatch):
    client, token, person_id, caregiver_id, other_token, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        forged = client.post("/api/v1/community", headers=auth, json={
            "caregiver_id": "caller-controlled-value",
            "author_type": "solenos_verified",
            "author_label": "Forged verified identity",
            "post_type": "experience",
            "content": "A forged identity must not be accepted.",
        })
        assert forged.status_code == 403, forged.text

        created = client.post("/api/v1/community", headers=auth, json={
            "post_type": "experience",
            "content": "A quiet routine helped us prepare for bedtime.",
            "tags": ["routine", "sleep"],
        })
        assert created.status_code == 201, created.text
        post = created.json()
        assert post["author_type"] == "peer_caregiver"
        assert post["author_label"] == "Anonymous caregiver"

        feed = client.get("/api/v1/community", headers=auth)
        assert feed.status_code == 200
        assert any(row["id"] == post["id"] for row in feed.json()["posts"])

        reaction = client.post(
            f"/api/v1/community/{post['id']}/reactions",
            headers=auth,
            json={"reaction_type": "support"},
        )
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()["reaction_counts"]["support"] == 1

        other_reaction = client.post(
            f"/api/v1/community/{post['id']}/reactions",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"reaction_type": "like"},
        )
        assert other_reaction.status_code == 200, other_reaction.text
        db = TestingSessionLocal()
        assert db.query(CommunityPost).filter_by(id=post["id"]).count() == 1
        db.close()
    finally:
        teardown()


def test_social_greeting_cannot_be_saved_as_care_evidence(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        response = client.post(
            "/api/situation",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "client_care_key": "browser-care-key",
                "raw_input": "hi",
                "care_session_id": "greeting-only-session",
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "conversation_only"
        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        assert db.query(Evidence).filter_by(person_id=person_id).count() == 0
        db.close()
    finally:
        teardown()


def test_companion_greeting_uses_qwen_review_and_never_writes_lcr(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    captured = []

    def fake_ollama_chat(base, model, messages):
        captured.append({"base": base, "model": model, "messages": messages})
        if len(captured) == 1:
            return "Saba, saba, saba."
        return "Hi! I'm here. What would you like to talk about?"

    import app.api.collaboration as collaboration_api
    monkeypatch.setattr(collaboration_api, "_ollama_url", lambda: ("http://ollama:11434", "qwen2.5:1.5b"))
    monkeypatch.setattr(collaboration_api, "_ollama_chat", fake_ollama_chat)
    try:
        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "hi"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ai_generated"] is True
        assert data["reply"] == "Hi! I'm here. What would you like to talk about?"
        assert len(captured) == 2
        system_prompt = captured[0]["messages"][0]["content"].lower()
        assert "do not use a fixed or hardcoded greeting template" in system_prompt
        assert "do not automatically ask 'how are you doing?'" in system_prompt
        assert "plain, clear language" in system_prompt
        assert "unfamiliar foreign terms" in system_prompt
        review_prompt = captured[1]["messages"][0]["content"].lower()
        assert "remove unfamiliar foreign terms" in review_prompt
        assert "saba" not in data["reply"].lower()
        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        assert db.query(Evidence).filter_by(person_id=person_id).count() == 0
        assert db.query(ConversationMessage).filter_by(session_id=data["session_id"]).count() == 2
        db.close()
    finally:
        teardown()


def test_companion_emergency_response_is_scoped_and_persisted_without_model(monkeypatch):
    client, token, person_id, _, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        denied = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": other_person_id,
            "text": "What happened today?",
        })
        assert denied.status_code == 403, denied.text

        response = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": person_id,
            "text": "They are unconscious and not breathing",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["guard_verdict"] == "blocked"
        assert "urgent medical help" in data["reply"].lower()
        assert data["ai_generated"] is False
        assert len(data["messages"]) == 2

        history = client.get(f"/api/v1/conversation/{data['session_id']}", headers=auth)
        assert history.status_code == 200
        assert len(history.json()["messages"]) == 2

        cross_record = client.get(
            f"/api/v1/conversation/{data['session_id']}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert cross_record.status_code == 404, cross_record.text

        db = TestingSessionLocal()
        rows = db.query(ConversationMessage).filter_by(session_id=data["session_id"]).all()
        assert [row.role for row in rows] == ["user", "assistant"]
        db.close()
    finally:
        teardown()



def test_original_upload_download_and_cross_record_denial(monkeypatch):
    client, token, person_id, caregiver_id, other_token, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        original = bytes([137, 80, 78, 71, 13, 10, 26, 10]) + b"test-image-bytes"
        uploaded = client.post(
            "/api/v1/upload",
            headers=auth,
            data={"person_id": person_id, "caregiver_id": caregiver_id},
            files={"file": ("medication.png", original, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        assert uploaded.json()["persisted"] is True

        downloaded = client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
        assert downloaded.status_code == 200
        assert downloaded.content == original
        assert downloaded.headers["content-type"] == "image/png"

        denied = client.get(
            f"/api/v1/upload/{evidence_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert denied.status_code == 404
    finally:
        teardown()



def test_care_delta_routes_bind_person_and_caregiver_to_session(monkeypatch):
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    try:
        import app.api.care_delta as care_delta_api
        monkeypatch.setattr(care_delta_api, "reconcile_event", lambda db, **kwargs: {
            "event_summary": "Caregiver report reconciled.",
            "caregiver_summary": "Review the source before acting.",
            "clinician_summary": "",
            "medications": [], "conditions": [], "symptoms": [], "tests": [],
            "follow_ups": [], "contradictions": [], "clinical_gaps": [],
            "persisted": bool(kwargs["persist"]),
            "created_by_caregiver_id": kwargs["created_by_caregiver_id"],
        })
        auth = {"Authorization": f"Bearer {token}"}
        ok = client.post("/api/v1/care-delta/reconcile", headers=auth, json={
            "person_id": person_id,
            "event_date": "2026-10-24T09:00:00Z",
            "source": "Caregiver report",
            "content": "Care recipient slept poorly last night.",
            "evidence_ids": [],
            "persist": True,
        })
        assert ok.status_code == 200, ok.text
        assert ok.json()["persisted"] is True
        assert ok.json()["created_by_caregiver_id"] == caregiver_id

        denied = client.post("/api/v1/care-delta/reconcile", headers=auth, json={
            "person_id": other_person_id,
            "source": "Caregiver report",
            "content": "Cross-record access must fail.",
        })
        assert denied.status_code == 403

        history = client.get(f"/api/v1/care-delta/runs/{other_person_id}", headers=auth)
        assert history.status_code == 403
        other_history = client.get(
            f"/api/v1/care-delta/runs/{person_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert other_history.status_code == 403
    finally:
        teardown()



def test_daily_capture_persists_and_reads_back_under_same_care_record(monkeypatch):
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        payload = {
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "medications": [{
                "medication_name": "Caregiver-reported medication",
                "status": "administered",
                "dose": "reported dose",
                "scheduled_for": None,
                "administered_at": "2026-10-24T08:00:00Z",
                "photo_evidence_id": None,
                "note": "Recorded by caregiver",
            }],
            "vitals": [{"vital_type": "heart_rate", "value": 72, "unit": "bpm"}],
            "scales": [{"axis": "mood", "score": 3}],
            "notes": "Daily capture contract test",
        }
        saved = client.post("/api/v1/daily/log", headers=auth, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["success"] is True

        events = client.get(f"/api/v1/events/{person_id}", headers=auth)
        assert events.status_code == 200, events.text
        row = next(event for event in events.json() if event["id"] == saved.json()["id"])
        import json as json_module
        stored = json_module.loads(row["description"])
        assert stored["notes"] == "Daily capture contract test"
        assert stored["medications"][0]["medication_name"] == "Caregiver-reported medication"

        denied = client.get(
            f"/api/v1/events/{person_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert denied.status_code == 403
    finally:
        teardown()



def test_companion_provider_unavailable_returns_503_without_orphan_session(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    try:
        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "How can I prepare for bedtime?"},
        )
        assert response.status_code == 503, response.text
        db = TestingSessionLocal()
        assert db.query(ConversationSession).count() == 0
        assert db.query(ConversationMessage).count() == 0
        db.close()
    finally:
        teardown()



def test_original_document_upload_is_reused_by_situation_capture(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        original = b"%PDF-1.4 test fixture"
        extracted_text = "Discharge document: follow-up appointment in two weeks."
        uploaded = client.post(
            "/api/v1/upload",
            headers=auth,
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "document",
                "extracted_text": extracted_text,
            },
            files={"file": ("discharge.pdf", original, "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        assert uploaded.json()["evidence_type"] == "document"

        saved = client.post("/api/situation", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "client_care_key": "local-workspace-key",
            "raw_input": "",
            "documents": [{
                "id": "local-document-id",
                "evidence_id": evidence_id,
                "name": "discharge.pdf",
                "extracted_text": extracted_text,
                "mime_type": "application/pdf",
                "ocr_confidence": 0.92,
            }],
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["evidence_id"] == evidence_id
        event_id = saved.json()["care_event_id"]

        events = client.get(f"/api/v1/events/{person_id}", headers=auth)
        assert events.status_code == 200, events.text
        event = next(row for row in events.json() if row["id"] == event_id)
        assert evidence_id in event["evidence_ids"]

        downloaded = client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
        assert downloaded.status_code == 200
        assert downloaded.content == original

        db = TestingSessionLocal()
        assert db.query(Evidence).filter_by(id=evidence_id).count() == 1
        db.close()
    finally:
        teardown()



def test_share_intake_is_persisted_and_claimed_once_without_account_session(monkeypatch):
    client, _, _, _, _, _ = setup_client(monkeypatch)
    try:
        original = b"shared-care-document"
        created = client.post("/api/v1/share-intake", json={
            "title": "Shared care note",
            "text": "Follow-up is scheduled.",
            "url": "https://example.org/care-note",
            "files": [{
                "name": "care-note.txt",
                "mimeType": "text/plain",
                "base64": base64.b64encode(original).decode("ascii"),
            }],
        })
        assert created.status_code == 201, created.text
        share_id = created.json()["id"]
        assert len(share_id) >= 40

        claimed = client.get(f"/api/v1/share-intake/{share_id}")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["text"] == "Follow-up is scheduled."
        assert claimed.json()["files"][0]["base64"] == base64.b64encode(original).decode("ascii")

        second_claim = client.get(f"/api/v1/share-intake/{share_id}")
        assert second_claim.status_code == 404
    finally:
        teardown()



def test_consent_acceptance_preferences_revocation_and_scope(monkeypatch):
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        status = client.get(f"/api/v1/consent?person_id={person_id}", headers=auth)
        assert status.status_code == 200
        assert status.json()["required"] is True

        accepted = client.post("/api/v1/consent", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "medical_disclaimer_acknowledged": True,
            "privacy_model_acknowledged": True,
            "multi_caregiver_acknowledged": True,
            "no_advertising_acknowledged": True,
            "data_improvement_consent": False,
            "accepted_terms_version": "2026-10",
        })
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["verified"] is True
        assert accepted.json()["profile"]["data_improvement_consent"] is False

        changed = client.post("/api/v1/consent", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "action": "update_data_improvement",
            "data_improvement_consent": True,
        })
        assert changed.status_code == 200, changed.text
        assert changed.json()["profile"]["data_improvement_consent"] is True

        revoked = client.post("/api/v1/consent", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "action": "revoke",
        })
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["verified"] is False
        assert revoked.json()["profile"]["limited_mode"] is True

        blocked_write = client.post("/api/v1/daily/log", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "raw_input": "This must not be persisted after consent revocation.",
        })
        assert blocked_write.status_code == 403
        assert blocked_write.json()["code"] == "consent_revoked"

        cross_record = client.get(
            f"/api/v1/consent?person_id={person_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert cross_record.status_code == 403
    finally:
        teardown()



def test_feedback_json_contract_requires_scoped_bearer_identity(monkeypatch):
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        feedback_payload = {
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "category": "human_validation",
            "detail": "{\"interaction_id\":\"interaction-1\",\"helpful_yes_no\":true,\"reduced_confusion_yes_no\":true}",
        }
        no_consent = client.post("/api/v1/feedback", headers=auth, json=feedback_payload)
        assert no_consent.status_code == 403
        assert "system-improvement consent" in no_consent.json()["detail"]

        accepted = client.post("/api/v1/consent", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "medical_disclaimer_acknowledged": True,
            "privacy_model_acknowledged": True,
            "multi_caregiver_acknowledged": True,
            "no_advertising_acknowledged": True,
            "data_improvement_consent": True,
        })
        assert accepted.status_code == 200, accepted.text

        response = client.post("/api/v1/feedback", headers=auth, json=feedback_payload)
        assert response.status_code == 200, response.text
        assert response.json()["event_type"] == "feedback"

        denied = client.post("/api/v1/feedback", headers=auth, json={
            "person_id": other_person_id,
            "caregiver_id": caregiver_id,
            "category": "human_validation",
            "detail": "cross-record feedback must be denied",
        })
        assert denied.status_code == 403
    finally:
        teardown()


def test_notifications_are_scoped_persisted_and_mark_read(monkeypatch):
    client, token, person_id, caregiver_id, other_token, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        other_auth = {"Authorization": f"Bearer {other_token}"}
        db = TestingSessionLocal()
        row = CareNotification(
            person_id=person_id,
            caregiver_id=caregiver_id,
            title="Care note needs review",
            body="A newly recorded care note is awaiting review.",
            category="care_update",
            priority="normal",
        )
        db.add(row)
        db.commit()
        notification_id = row.id
        db.close()

        feed = client.get("/api/v1/notifications?limit=100&offset=0", headers=auth)
        assert feed.status_code == 200, feed.text
        assert len(feed.json()["items"]) == 1
        assert feed.json()["items"][0]["id"] == notification_id
        assert feed.json()["items"][0]["is_read"] is False

        unread = client.get("/api/v1/notifications/unread-count", headers=auth)
        assert unread.status_code == 200 and unread.json()["unread_count"] == 1

        cross_record = client.patch(f"/api/v1/notifications/{notification_id}/read", headers=other_auth, json={})
        assert cross_record.status_code == 404

        marked = client.patch(f"/api/v1/notifications/{notification_id}/read", headers=auth, json={})
        assert marked.status_code == 200 and marked.json()["is_read"] is True
        unread_after = client.get("/api/v1/notifications/unread-count", headers=auth)
        assert unread_after.json()["unread_count"] == 0
    finally:
        teardown()


def test_notification_preferences_round_trip_and_session_revocation(monkeypatch):
    client, token, _, _, _, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        saved = client.put("/api/v1/notifications/preferences", headers=auth, json={
            "quiet_hours_enabled": True,
            "reminders_enabled": False,
        })
        assert saved.status_code == 200, saved.text
        loaded = client.get("/api/v1/notifications/preferences", headers=auth)
        assert loaded.status_code == 200
        assert loaded.json() == {"quiet_hours_enabled": True, "reminders_enabled": False}

        revoked = client.post("/api/v1/sessions/revoke", headers=auth, json={})
        assert revoked.status_code == 200 and revoked.json()["revoked"] is True
        after_revoke = client.get("/api/v1/notifications/unread-count", headers=auth)
        assert after_revoke.status_code == 401
    finally:
        teardown()



def test_qwen_support_assist_is_scoped_read_only_and_never_claims_authority(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        monkeypatch.setattr(
            collaboration,
            "qwen_chat",
            lambda messages, **kwargs: "You could write down the question you want to ask at the appointment.",
        )
        headers = {"Authorization": f"Bearer {token}"}
        before = client.get("/api/v1/events/" + person_id, headers=headers)
        assert before.status_code == 200, before.text
        before_ids = {row["id"] for row in before.json()}

        response = client.post("/api/v1/assistant/assist", headers=headers, json={
            "workflow": "appointment",
            "text": "Help me prepare for the next appointment.",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ai_generated"] is True
        assert data["authority"] == "solenos_care_record_and_caregiver"
        assert data["written_to_lcr"] is False
        assert data["persistence"] == "read_only_assistance"
        assert data["reply"] == "You could write down the question you want to ask at the appointment."

        after = client.get("/api/v1/events/" + person_id, headers=headers)
        assert after.status_code == 200, after.text
        assert {row["id"] for row in after.json()} == before_ids

        wrong_scope = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {token}"},
            json={"workflow": "handoff", "text": "Help me with this handoff.", "evidence_ids": ["not-owned"]},
        )
        assert wrong_scope.status_code == 403, wrong_scope.text
    finally:
        teardown()


def test_community_summary_uses_qwen_only_when_a_model_response_is_generated(monkeypatch):
    client, token, _, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        monkeypatch.setattr(
            collaboration,
            "qwen_chat",
            lambda messages, **kwargs: "Caregiver experiences differ, so use these posts as peer perspectives rather than medical advice.",
        )
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/community", headers=headers, json={
            "post_type": "experience",
            "content": "A written evening routine helped us remember the bedtime steps.",
            "tags": ["routine"],
        })
        assert created.status_code == 201, created.text
        response = client.post("/api/v1/community/experience/summary", headers=headers, json={
            "observation": "I am struggling to remember the evening routine.",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ai_generated"] is True
        assert data["provider_status"] == "available"
        assert "not authoritative" in data["notice"]
    finally:
        teardown()



def test_post_save_qwen_candidate_analysis_uses_persisted_source_and_does_not_write_lcr(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        source = "The caregiver reports that the evening routine was difficult."
        saved = client.post("/api/situation", headers=headers, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "client_care_key": "local-workspace-key",
            "raw_input": source,
        })
        assert saved.status_code == 200, saved.text
        event_id = saved.json()["care_event_id"]

        import app.services.qwen_gateway as qwen_gateway

        monkeypatch.setattr(
            qwen_gateway,
            "qwen_chat",
            lambda messages, **kwargs: __import__("json").dumps({
                "observations": [{
                    "subject": "evening routine",
                    "observation": "The caregiver reports difficulty with the evening routine.",
                    "source_quote": source,
                }],
                "open_questions": ["Which part of the routine was difficult?"],
            }),
        )
        before = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert before.status_code == 200, before.text
        before_ids = {row["id"] for row in before.json()}

        analysis = client.post("/api/v1/assistant/extract-candidates", headers=headers, json={"event_id": event_id})
        assert analysis.status_code == 200, analysis.text
        data = analysis.json()
        assert data["qwen_extraction_status"] == "pending_review"
        assert data["ai_generated"] is True
        assert data["written_to_lcr"] is False
        assert data["qwen_candidates"]["observations"][0]["source_quote"] == source
        assert data["qwen_candidates"]["observations"][0]["review_status"] == "pending_review"

        after = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert after.status_code == 200, after.text
        assert {row["id"] for row in after.json()} == before_ids

        denied = client.post(
            "/api/v1/assistant/extract-candidates",
            headers={"Authorization": f"Bearer {token}"},
            json={"event_id": "not-in-this-record"},
        )
        assert denied.status_code == 404
    finally:
        teardown()


def test_daily_capture_save_does_not_wait_for_qwen(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        import app.services.qwen_gateway as qwen_gateway

        def model_must_not_run(*args, **kwargs):
            raise AssertionError("Qwen must run only after the daily capture save receipt")

        monkeypatch.setattr(qwen_gateway, "qwen_chat", model_must_not_run)
        response = client.post(
            "/api/v1/daily/log",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Caregiver recorded a quiet afternoon.",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        assert response.json()["id"]
    finally:
        teardown()



def test_situation_hydration_projects_real_persisted_event_without_fabricating_observations(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        db = TestingSessionLocal()
        event = CareEvent(
            person_id=person_id,
            event_type="caregiver_note",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Persisted note",
            description="A caregiver recorded a note.",
            evidence_ids=[],
            created_by_caregiver_id=caregiver_id,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        event_id = event.id
        db.close()

        response = client.get(
            "/api/situation",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["has_context_root"] is True
        assert data["active_care_situation"]["root_event_id"] == event_id
        assert data["active_care_situation"]["observations"] == []
        assert any(row["id"] == event_id for row in data["events"])
    finally:
        teardown()



def test_qwen_runtime_status_is_authenticated_and_reports_actual_provider_contract(monkeypatch):
    client, token, _, _, _, _ = setup_client(monkeypatch)
    try:
        unauthenticated = client.get("/api/v1/assistant/status")
        assert unauthenticated.status_code == 401

        import app.api.collaboration as collaboration

        monkeypatch.setattr(collaboration, "qwen_runtime_status", lambda: {
            "available": True,
            "model": "qwen2.5:1.5b",
            "provider": "local_ollama",
            "inference_api": "ollama_native_http",
            "hosted_inference_api": False,
            "runtime": "bundled_railway_sidecar",
        })
        response = client.get(
            "/api/v1/assistant/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["available"] is True
        assert data["provider"] == "local_ollama"
        assert data["hosted_inference_api"] is False
    finally:
        teardown()
