import base64
import importlib
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.collaboration import CommunityPost, ConversationMessage
from app.models.care import CareEvent, Caregiver, Evidence, Person

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


def make_text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
    })
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


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


def test_cors_allows_netlify_preflight_and_rejects_unknown_origin():
    client = TestClient(app)
    allowed = client.options(
        "/api/v1/sessions/anonymous",
        headers={
            "Origin": "https://solenosai.netlify.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers.get("access-control-allow-origin") == "https://solenosai.netlify.app"
    assert allowed.headers.get("access-control-allow-credentials") == "true"
    allowed_headers = allowed.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers

    denied = client.options(
        "/api/v1/sessions/anonymous",
        headers={
            "Origin": "https://unapproved.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_community_post_feed_reaction_persist_and_session_attribution(monkeypatch):
    # Community is intentionally disabled. A valid session must not expose feed,
    # post creation, or reaction endpoints; the feature's implementation remains
    # in source but is not registered on the active API.
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        routes = [
            ("get", "/api/v1/community", None),
            ("post", "/api/v1/community", {"post_type": "experience", "content": "must not publish"}),
            ("post", "/api/v1/community/example-post/reactions", {"reaction_type": "support"}),
            ("get", "/api/v1/community/experience/summary", None),
        ]
        for method, path, payload in routes:
            if payload is None:
                response = getattr(client, method)(path, headers=auth)
            else:
                response = getattr(client, method)(path, headers=auth, json=payload)
            assert response.status_code == 404, f"{method.upper()} {path}: {response.text}"

        db = TestingSessionLocal()
        try:
            assert db.query(CommunityPost).count() == 0
        finally:
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
        assert data["written_to_lcr"] is False
        assert data["persistence"] == "conversation_only"
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
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        original = b"\x89PNG\r\n\x1a\n" + b"test-image-bytes"
        spoofed = client.post(
            "/api/v1/upload",
            headers=auth,
            data={"person_id": person_id, "caregiver_id": caregiver_id},
            files={"file": ("spoof.png", b"not a PNG", "image/png")},
        )
        assert spoofed.status_code == 422, spoofed.text

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

        linked = client.post("/api/situation", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "raw_input": "Caregiver notes a detail that needs later review.",
            "documents": [{
                "id": "client-document-image",
                "name": "medication.png",
                "extracted_text": "",
                "evidence_id": evidence_id,
                "mime_type": "image/png",
            }],
        })
        assert linked.status_code == 200, linked.text
        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=linked.json()["care_event_id"]).first()
        assert event is not None
        assert evidence_id in (event.evidence_ids or [])
        other_caregiver = db.query(Caregiver).filter_by(person_id=other_person_id).first()
        assert other_caregiver is not None
        other_caregiver_id = other_caregiver.id
        db.close()

        cross_record = client.post("/api/situation", headers={"Authorization": f"Bearer {other_token}"}, json={
            "person_id": other_person_id,
            "caregiver_id": other_caregiver_id,
            "raw_input": "This record must not link another person's evidence.",
            "documents": [{
                "id": "foreign-document-image",
                "name": "medication.png",
                "extracted_text": "",
                "evidence_id": evidence_id,
                "mime_type": "image/png",
            }],
        })
        assert cross_record.status_code == 400, cross_record.text

        denied = client.get(
            f"/api/v1/upload/{evidence_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert denied.status_code == 404

        text_bytes = b"Caregiver note text; not yet promoted to a care assertion."
        text_upload = client.post(
            "/api/v1/upload",
            headers=auth,
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "document",
                "extracted_text": text_bytes.decode("utf-8"),
            },
            files={"file": ("care-note.txt", text_bytes, "text/plain")},
        )
        assert text_upload.status_code == 201, text_upload.text
        assert text_upload.json()["persisted"] is True
        assert len(text_upload.json()["sha256"]) == 64
        text_id = text_upload.json()["evidence_id"]
        text_download = client.get(f"/api/v1/upload/{text_id}", headers=auth)
        assert text_download.status_code == 200
        assert text_download.content == text_bytes
        assert text_download.headers["content-type"].startswith("text/plain")
        text_denied = client.get(
            f"/api/v1/upload/{text_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert text_denied.status_code == 404
    finally:
        teardown()



def test_document_extraction_requires_session_and_supports_utf8_text(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        original_text = b"Caregiver note: a follow-up question remains open."
        unauthenticated = client.post(
            "/api/extract",
            files={"file": ("care-note.txt", original_text, "text/plain")},
        )
        assert unauthenticated.status_code == 401, unauthenticated.text

        response = client.post(
            "/api/extract",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("care-note.txt", original_text, "text/plain")},
        )
        assert response.status_code == 200, response.text
        document = response.json()["document"]
        assert response.json()["ok"] is True
        assert document["extracted_text"] == original_text.decode("utf-8")
        assert document["extraction_source"] == "text"

        pdf_bytes = make_text_pdf("Text based care note for extraction")
        pdf_response = client.post(
            "/api/extract",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("care-note.pdf", pdf_bytes, "application/pdf")},
        )
        assert pdf_response.status_code == 200, pdf_response.text
        pdf_document = pdf_response.json()["document"]
        assert "Text based care note for extraction" in pdf_document["extracted_text"]
        assert pdf_document["extraction_source"] == "pdf_text"

        stored_pdf = client.post(
            "/api/v1/upload",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "document",
                "extracted_text": pdf_document["extracted_text"],
            },
            files={"file": ("care-note.pdf", pdf_bytes, "application/pdf")},
        )
        assert stored_pdf.status_code == 201, stored_pdf.text
        pdf_id = stored_pdf.json()["evidence_id"]
        pdf_download = client.get(f"/api/v1/upload/{pdf_id}", headers={"Authorization": f"Bearer {token}"})
        assert pdf_download.status_code == 200
        assert pdf_download.content == pdf_bytes
        assert pdf_download.headers["content-type"] == "application/pdf"

        unsupported = client.post(
            "/api/extract",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("scan.png", b"not-a-real-image", "image/png")},
        )
        assert unsupported.status_code == 415, unsupported.text
        assert "scanned images require OCR" in unsupported.json()["detail"]
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



def test_qwen_support_covers_all_care_workflows_without_lcr_writes(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        monkeypatch.setattr(
            collaboration,
            "qwen_chat",
            lambda messages, **kwargs: "I can help organize the recorded information and identify what still needs review.",
        )
        headers = {"Authorization": f"Bearer {token}"}
        workflows = [
            "daily_capture", "add_record", "document", "living_care_record",
            "care_history_search", "care_situation_change", "delta_engine",
            "evidence_provenance", "uncertainty_contradictions", "returning_caregiver",
            "appointment", "after_appointment", "handoff", "professional_summary",
            "open_loops", "two_week_review", "decision_outcome", "medication_day",
            "reminders_notifications", "care_coordination", "companion",
            "caregiver_feedback", "sharing_export", "roles_access", "account_recovery",
            "settings_security", "record_lifecycle", "offline_sync", "ai_reliability",
            "audit_review",
        ]
        before = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert before.status_code == 200, before.text
        before_ids = {row["id"] for row in before.json()}

        for workflow in workflows:
            response = client.post(
                "/api/v1/assistant/assist",
                headers=headers,
                json={"workflow": workflow, "text": f"Help me organize the {workflow.replace('_', ' ')} details."},
            )
            assert response.status_code == 200, f"{workflow}: {response.text}"
            data = response.json()
            assert data["workflow"] == workflow
            assert data["ai_generated"] is True
            assert data["review_required"] is True
            assert data["written_to_lcr"] is False
            assert data["persistence"] == "read_only_assistance"
            assert data["authority"] == "solenos_care_record_and_caregiver"

        after = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert after.status_code == 200, after.text
        assert {row["id"] for row in after.json()} == before_ids
    finally:
        teardown()


def test_community_qwen_assist_is_disabled_and_does_not_call_model(monkeypatch):
    import app.api.collaboration as collaboration

    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        calls = []
        monkeypatch.setattr(collaboration, "qwen_chat", lambda *args, **kwargs: calls.append(args) or "should not run")
        response = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {token}"},
            json={"workflow": "community", "text": "bedtime routine"},
        )
        assert response.status_code == 422, response.text
        assert calls == []
        events = client.get(f"/api/v1/events/{person_id}", headers={"Authorization": f"Bearer {token}"})
        assert events.status_code == 200
        assert events.json() == []
    finally:
        teardown()


def test_companion_greeting_uses_qwen_and_never_writes_greeting_to_lcr(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        calls = []
        monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://private-ollama", "qwen2.5:1.5b"))
        def generated_reply(base, model, messages, options=None):
            calls.append(messages)
            return "Hi there. I'm here if you need help."
        monkeypatch.setattr(collaboration, "_ollama_chat", generated_reply)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={"person_id": person_id, "text": "hi"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ai_generated"] is True
        assert len(calls) == 1, "A greeting must be generated dynamically by Qwen without a canned second-pass response"
        events = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert events.status_code == 200
        assert events.json() == []
        assert response.json()["reply"] == "Hi there. I'm here if you need help."
    finally:
        teardown()


def test_casual_greeting_is_rejected_before_situation_capture_writes(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        response = client.post(
            "/api/situation",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "raw_input": "hi",
                "documents": [],
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "conversation_only"
        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        assert db.query(Evidence).filter_by(person_id=person_id).count() == 0
        db.close()
    finally:
        teardown()



def test_share_intake_persists_across_requests_and_cannot_be_replayed(monkeypatch):
    client, _, _, _, _, _ = setup_client(monkeypatch)
    try:
        original = b"shared-care-document"
        encoded = base64.b64encode(original).decode("ascii")
        created = client.post("/api/v1/share-intake", json={
            "title": "Shared care note",
            "text": "Follow-up is scheduled.",
            "url": "https://example.org/care-note",
            "files": [{
                "name": "care-note.txt",
                "mimeType": "text/plain",
                "base64": encoded,
            }],
        })
        assert created.status_code == 201, created.text
        share_id = created.json()["id"]
        assert len(share_id) >= 40

        claimed = client.get(f"/api/v1/share-intake/{share_id}")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["text"] == "Follow-up is scheduled."
        assert claimed.json()["files"][0]["base64"] == encoded

        replay = client.get(f"/api/v1/share-intake/{share_id}")
        assert replay.status_code == 404
    finally:
        teardown()



def test_upload_preserves_medication_photo_default_and_accepts_word_documents(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"medication image bytes"
        medication = client.post(
            "/api/v1/upload",
            headers=headers,
            data={"person_id": person_id, "caregiver_id": caregiver_id},
            files={"file": ("medication.png", png_bytes, "image/png")},
        )
        assert medication.status_code == 201, medication.text
        medication_id = medication.json()["evidence_id"]

        docx_bytes = b"PK\x03\x04" + b"minimal docx fixture"
        document = client.post(
            "/api/v1/upload",
            headers=headers,
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "document",
                "extracted_text": "Discharge note source text",
            },
            files={"file": (
                "discharge.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )},
        )
        assert document.status_code == 201, document.text
        document_id = document.json()["evidence_id"]

        db = TestingSessionLocal()
        try:
            assert db.query(Evidence).filter_by(id=medication_id).one().type == "medication_photo"
            assert db.query(Evidence).filter_by(id=document_id).one().type == "document"
        finally:
            db.close()

        downloaded = client.get(f"/api/v1/upload/{document_id}", headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.content == docx_bytes
    finally:
        teardown()



def test_companion_rejects_unrequested_saba_in_any_reply_without_saving(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://private-ollama", "qwen2.5:1.5b"))
        monkeypatch.setattr(
            collaboration,
            "_ollama_chat",
            lambda base, model, messages, options=None: "Saba, saba. I can help organize the appointment details.",
        )
        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "Help me prepare questions for the appointment."},
        )
        assert response.status_code == 503, response.text
        assert "clear" in response.json()["detail"].lower()

        db = TestingSessionLocal()
        assert db.query(ConversationMessage).count() == 0
        db.close()
    finally:
        teardown()


def test_read_only_qwen_assist_rejects_unrequested_saba_without_lcr_write(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        monkeypatch.setattr(
            collaboration,
            "qwen_chat",
            lambda messages, **kwargs: "Saba is the key thing to remember. Saba, saba.",
        )
        headers = {"Authorization": f"Bearer {token}"}
        before = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert before.status_code == 200, before.text
        before_ids = {row["id"] for row in before.json()}

        response = client.post(
            "/api/v1/assistant/assist",
            headers=headers,
            json={"workflow": "appointment", "text": "Help organize my appointment questions."},
        )
        assert response.status_code == 503, response.text

        after = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert after.status_code == 200, after.text
        assert {row["id"] for row in after.json()} == before_ids
    finally:
        teardown()



def test_qwen_status_is_public_health_and_assist_is_session_scoped(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        monkeypatch.setattr(collaboration, "qwen_runtime_status", lambda: {
            "available": True,
            "model": "qwen2.5:1.5b",
            "provider": "local_ollama",
            "inference_api": "ollama_native_http",
            "hosted_inference_api": False,
            "runtime": "bundled_railway_sidecar",
        })
        monkeypatch.setattr(
            collaboration,
            "qwen_chat",
            lambda messages, **kwargs: "I can help organize the available appointment details and identify unanswered questions.",
        )
        headers = {"Authorization": f"Bearer {token}"}
        db = TestingSessionLocal()
        persons_before = db.query(Person).count()
        caregivers_before = db.query(Caregiver).count()
        db.close()

        # Runtime health is infrastructure-only and must not mint a synthetic care record.
        status = client.get("/api/v1/assistant/status")
        assert status.status_code == 200, status.text
        assert status.json()["provider"] == "local_ollama"
        assert status.json()["hosted_inference_api"] is False

        db = TestingSessionLocal()
        assert db.query(Person).count() == persons_before
        assert db.query(Caregiver).count() == caregivers_before
        db.close()

        assist = client.post("/api/v1/assistant/assist", headers=headers, json={
            "workflow": "appointment",
            "text": "Help organize the appointment details and questions.",
        })
        assert assist.status_code == 200, assist.text
        assert assist.json()["written_to_lcr"] is False
        assert assist.json()["authority"] == "solenos_care_record_and_caregiver"

        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        db.close()
    finally:
        teardown()



def test_qwen_assist_uses_local_model_to_repair_unclear_draft_without_canned_fallback(monkeypatch):
    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        import app.api.collaboration as collaboration

        replies = iter([
            "Saba, saba. I can help organize the recorded handoff information and identify what remains unknown.",
            "I can organize the recorded handoff details and flag missing information without inventing a handoff.",
        ])
        calls = []
        def qwen_chat(messages, **kwargs):
            calls.append(messages)
            return next(replies)

        monkeypatch.setattr(collaboration, "qwen_chat", qwen_chat)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/api/v1/assistant/assist",
            headers=headers,
            json={"workflow": "handoff", "text": "Help me organize the handoff."},
        )
        assert response.status_code == 200, response.text
        assert len(calls) == 2
        assert response.json()["reply"] == "I can organize the recorded handoff details and flag missing information without inventing a handoff."
        assert response.json()["ai_generated"] is True
        assert response.json()["written_to_lcr"] is False
        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        db.close()
    finally:
        teardown()



def test_qwen_gateway_bounds_context_and_keeps_model_loaded(monkeypatch):
    import json
    from app.services import qwen_gateway

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"message": {"content": "A real generated reply."}}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", fake_urlopen)

    reply = qwen_gateway.qwen_chat(
        [{"role": "user", "content": "hi"}],
        options={"temperature": 0},
    )

    assert reply == "A real generated reply."
    assert captured["url"] == "http://ollama:11434/api/chat"
    assert captured["payload"]["model"] == "qwen2.5:1.5b"
    assert captured["payload"]["keep_alive"] == "30m"
    assert captured["payload"]["options"]["num_ctx"] == 1024
    assert captured["payload"]["options"]["num_predict"] == 96
    assert captured["payload"]["options"]["num_thread"] == 2
    assert captured["payload"]["options"]["temperature"] == 0


def test_persistent_in_app_reminders_are_scoped_and_do_not_claim_delivery(monkeypatch):
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
    auth = {"Authorization": f"Bearer {token}"}
    other_auth = {"Authorization": f"Bearer {other_token}"}
    due_at = "2030-01-01T12:00:00+00:00"
    try:
        created = client.post(
            "/api/v1/reminders",
            headers=auth,
            json={
                "person_id": person_id,
                "title": "Review the appointment questions",
                "note": "Confirm which questions still need an answer.",
                "due_at": due_at,
            },
        )
        assert created.status_code == 201, created.text
        reminder = created.json()
        reminder_id = reminder["id"]
        assert reminder["status"] == "open"
        assert reminder["delivery_mode"] == "in_app_list_only"
        assert reminder["notification_delivery_status"] == "not_sent"
        assert reminder["created_by_caregiver_id"] == caregiver_id
        assert reminder["due_at"].endswith(("Z", "+00:00")), reminder["due_at"]

        listed = client.get(f"/api/v1/reminders/{person_id}", headers=auth)
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == reminder_id for item in listed.json())

        own_other_list = client.get(f"/api/v1/reminders/{other_person_id}", headers=other_auth)
        assert own_other_list.status_code == 200, own_other_list.text
        assert own_other_list.json() == []
        foreign_list = client.get(f"/api/v1/reminders/{person_id}", headers=other_auth)
        assert foreign_list.status_code == 403, foreign_list.text
        foreign_complete = client.patch(
            f"/api/v1/reminders/{reminder_id}/complete",
            headers=other_auth,
            json={},
        )
        assert foreign_complete.status_code == 404, foreign_complete.text

        completed = client.patch(
            f"/api/v1/reminders/{reminder_id}/complete",
            headers=auth,
            json={"completion_note": "Questions reviewed."},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["completed_by_caregiver_id"] == caregiver_id
        assert completed.json()["completion_note"] == "Questions reviewed."
        assert completed.json()["notification_delivery_status"] == "not_sent"

        cannot_cancel_completed = client.patch(
            f"/api/v1/reminders/{reminder_id}/cancel",
            headers=auth,
        )
        assert cannot_cancel_completed.status_code == 409, cannot_cancel_completed.text

        cancelled_create = client.post(
            "/api/v1/reminders",
            headers=auth,
            json={"person_id": person_id, "title": "Check transport", "due_at": due_at},
        )
        assert cancelled_create.status_code == 201, cancelled_create.text
        cancelled_id = cancelled_create.json()["id"]
        cancelled = client.patch(f"/api/v1/reminders/{cancelled_id}/cancel", headers=auth)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["cancelled_by_caregiver_id"] == caregiver_id
        cannot_complete_cancelled = client.patch(
            f"/api/v1/reminders/{cancelled_id}/complete",
            headers=auth,
            json={},
        )
        assert cannot_complete_cancelled.status_code == 409, cannot_complete_cancelled.text

        naive_due_date = client.post(
            "/api/v1/reminders",
            headers=auth,
            json={"person_id": person_id, "title": "Ambiguous time", "due_at": "2030-01-01T12:00:00"},
        )
        assert naive_due_date.status_code == 422, naive_due_date.text
    finally:
        teardown()


def test_qwen_grounding_guard_rejects_unsupported_care_status_claims():
    from app.api.collaboration import _contains_unsupported_care_claim

    assert _contains_unsupported_care_claim(
        "The clinician reported that the patient had a positive response to the treatment.",
        "The appointment is over, but I have not provided what the clinician said.",
    )
    assert _contains_unsupported_care_claim(
        "Follow-up appointment scheduled in six weeks.",
        "Follow-up appointment requested in six weeks.",
    )
    assert not _contains_unsupported_care_claim(
        "The appointment was scheduled for Monday.",
        "The appointment was scheduled for Monday.",
    )


def test_qwen_support_fails_closed_after_repeated_unsupported_clinical_claim(monkeypatch):
    import app.api.collaboration as collaboration

    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        generated = []
        hallucinated = "The clinician reported that the patient had a positive response to the treatment."

        def qwen_response(messages, **kwargs):
            generated.append(messages)
            return hallucinated

        monkeypatch.setattr(collaboration, "qwen_chat", qwen_response)
        before = client.get(f"/api/v1/events/{person_id}", headers={"Authorization": f"Bearer {token}"})
        assert before.status_code == 200, before.text

        response = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "workflow": "after_appointment",
                "text": "The appointment is over, but I have not provided what the clinician said.",
            },
        )
        assert response.status_code == 503, response.text
        assert "grounded" in response.json()["detail"].lower()
        assert len(generated) == 2, "Unsupported output should receive one repair attempt, then fail closed."

        after = client.get(f"/api/v1/events/{person_id}", headers={"Authorization": f"Bearer {token}"})
        assert after.status_code == 200, after.text
        assert after.json() == before.json(), "Qwen assistance must not write to the Living Care Record."
    finally:
        teardown()


def test_qwen_grounding_guard_rejects_unsupported_dementia_inference_and_handoff_assignment():
    from app.api.collaboration import _contains_unsupported_care_claim

    assert _contains_unsupported_care_claim(
        "Repeated questions can be a sign of dementia.",
        "My father asked the same question several times today.",
    )
    assert _contains_unsupported_care_claim(
        "Lunch is unconfirmed. Handoff to the caregiver.",
        "Lunch not yet confirmed.",
    )
    assert not _contains_unsupported_care_claim(
        "The caregiver reported a dementia diagnosis.",
        "The caregiver reported a dementia diagnosis.",
    )


def test_qwen_grounding_guard_rejects_invented_first_person_community_experience():
    from app.api.collaboration import _contains_unsupported_care_claim

    source = (
        "I feel exhausted because my father asks the same question several times each morning. "
        "I would like to hear what helped other caregivers."
    )
    assert _contains_unsupported_care_claim(
        "I've found that a few strategies can help. These methods have made my day more manageable.",
        source,
    )
    assert not _contains_unsupported_care_claim(
        "I feel exhausted because my father asks the same question several times each morning.",
        source,
    )


def test_qwen_community_experience_is_disabled_without_inference_or_lcr_write(monkeypatch):
    import app.api.collaboration as collaboration

    client, token, person_id, _, _, _ = setup_client(monkeypatch)
    try:
        generated = []
        monkeypatch.setattr(collaboration, "qwen_chat", lambda *args, **kwargs: generated.append(args) or "not called")
        headers = {"Authorization": f"Bearer {token}"}
        before = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert before.status_code == 200, before.text
        response = client.post(
            "/api/v1/assistant/assist",
            headers=headers,
            json={
                "workflow": "community",
                "text": "I feel exhausted and want to hear other caregivers' experiences.",
            },
        )
        assert response.status_code == 422, response.text
        assert generated == []
        after = client.get(f"/api/v1/events/{person_id}", headers=headers)
        assert after.status_code == 200, after.text
        assert after.json() == before.json()
    finally:
        teardown()
