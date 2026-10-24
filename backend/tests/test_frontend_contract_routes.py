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
from app.models.care import CareEvent, Caregiver, Evidence
import app.api.collaboration as collaboration_api

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
    client, token, person_id, caregiver_id, other_token, other_person_id = setup_client(monkeypatch)
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



def test_social_greeting_is_not_saved_as_care_and_companion_uses_qwen_prompt(monkeypatch):
    client, token, person_id, caregiver_id, _, _ = setup_client(monkeypatch)
    try:
        auth = {"Authorization": f"Bearer {token}"}
        capture = client.post("/api/situation", headers=auth, json={
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "care_session_id": "greeting-only-session",
            "raw_input": "hi",
            "documents": [],
        })
        assert capture.status_code == 422, capture.text
        detail = capture.json()["detail"]
        assert detail["code"] == "conversation_only"

        db = TestingSessionLocal()
        assert db.query(Evidence).filter_by(person_id=person_id).count() == 0
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        db.close()

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.railway.internal:11434")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:1.5b")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
        seen = {}

        def qwen_reply(base, model, messages):
            seen["base"] = base
            seen["model"] = model
            seen["messages"] = messages
            return "Hi! I'm doing well, thanks for asking. How are you?"

        monkeypatch.setattr(collaboration_api, "_ollama_chat", qwen_reply)
        response = client.post("/api/v1/conversation/message", headers=auth, json={
            "person_id": person_id,
            "text": "hi",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ai_generated"] is True
        assert data["reply"] == "Hi! I'm doing well, thanks for asking. How are you?"
        system_prompt = seen["messages"][0]["content"].lower()
        assert "fixed greeting templates" in system_prompt
        assert "unexplained slang" in system_prompt
        assert "respond to the user's actual message first" in system_prompt
        assert seen["model"] == "qwen2.5:1.5b"

        db = TestingSessionLocal()
        assert db.query(Evidence).filter_by(person_id=person_id).count() == 0
        assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
        db.close()
    finally:
        teardown()
