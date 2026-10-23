"""Authenticated document intake preserves source files and keeps extracted facts review-only."""
from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.capture import EvidenceFile
from app.models.care import CareEvent, Caregiver, Evidence
from app.models.capture_analysis import CaptureAnalysis

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def create_owner(client: TestClient, suffix: str):
    email = f"{suffix}@example.com"
    password = "a-long-test-password-2026"
    signup = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "display_name": "Document Test Caregiver"},
    )
    assert signup.status_code == 201, signup.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    context = client.post(
        "/api/v1/auth/care-contexts",
        headers=headers,
        json={"name": "Document Test Care Recipient"},
    )
    assert context.status_code == 201, context.text
    with TestingSessionLocal() as db:
        caregiver = db.query(Caregiver).filter_by(person_id=context.json()["person_id"]).one()
        caregiver_id = caregiver.id
    return headers, context.json()["person_id"], caregiver_id


def tiny_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def tiny_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), color=(180, 40, 20)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_pdf_intake_preserves_binary_and_records_no_text_state_explicitly():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_owner(client, "document-pdf")
            response = client.post(
                "/api/v1/documents/intake",
                headers=headers,
                data={"person_id": person_id, "caregiver_id": caregiver_id},
                files={"file": ("discharge.pdf", tiny_pdf(), "application/pdf")},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["source_file_preserved"] is True
            assert body["content_type"] == "application/pdf"
            assert body["extraction_status"] == "no_text_detected"
            assert body["qwen_extraction_status"] == "not_run_no_text"
            assert body["review_required"] is True

            with TestingSessionLocal() as db:
                evidence = db.query(Evidence).filter_by(id=body["evidence_id"]).one()
                event = db.query(CareEvent).filter_by(id=body["care_event_id"]).one()
                stored = db.query(EvidenceFile).filter_by(evidence_id=evidence.id).one()
                assert evidence.person_id == person_id
                assert evidence.source_type == "document_upload"
                assert event.evidence_ids == [evidence.id]
                assert stored.content == tiny_pdf()
                assert stored.sha256 == body["sha256"]

            record = client.get(f"/api/v1/documents/{body['evidence_id']}", headers=headers)
            assert record.status_code == 200, record.text
            assert record.json()["metadata"]["review_status"] == "pending_review"
            download = client.get(f"/api/v1/upload/{body['evidence_id']}", headers=headers)
            assert download.status_code == 200
            assert download.content == tiny_pdf()
    finally:
        teardown_db()


def test_image_intake_detects_document_type_and_date_and_returns_review_only_candidates():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_owner(client, "document-image")
            extracted_text = (
                "DISCHARGE SUMMARY\nDate: 2026-10-20\n"
                "Medication list: Medication A, as listed by the clinic.\n"
                "Follow-up appointment: Contact the clinic next week."
            )
            analysis = {
                "qwen_extraction_status": "pending_review",
                "qwen_candidates": {
                    "observations": [{
                        "candidate_type": "follow_up",
                        "subject": "follow-up appointment",
                        "observation": "Contact the clinic next week.",
                        "source_quote": "Follow-up appointment: Contact the clinic next week.",
                        "evidence_ids": ["source-evidence"],
                        "review_status": "pending_review",
                    }],
                    "open_questions": [],
                    "possible_safety_flags": [],
                },
                "qwen_analysis_id": "analysis-test-id",
                "analysis_status": "complete",
                "analysis_error": None,
                "what_changed": None,
                "attention_candidate": False,
                "follow_up_candidate": True,
                "situations": [],
            }
            with patch("app.api.capture.analyze_saved_care_event", return_value=analysis):
                response = client.post(
                    "/api/v1/documents/intake",
                    headers=headers,
                    data={
                        "person_id": person_id,
                        "caregiver_id": caregiver_id,
                        "extracted_text": extracted_text,
                        "extraction_source": "client_ocr_test",
                    },
                    files={"file": ("discharge.png", tiny_png(), "image/png")},
                )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["document_type_candidate"] == "discharge_instructions"
            assert body["document_date_candidate"] == "2026-10-20"
            assert body["document_date_review_status"] == "pending_review"
            assert body["extraction_status"] == "extracted"
            assert body["qwen_candidates"]["observations"][0]["candidate_type"] == "follow_up"
            assert body["review_required"] is True
            with TestingSessionLocal() as db:
                evidence = db.query(Evidence).filter_by(id=body["evidence_id"]).one()
                assert evidence.source_text == extracted_text
                assert evidence.evidence_status == "uploaded"
                metadata = __import__("json").loads(evidence.extra_metadata)
                assert metadata["extraction_engine"] == "client_ocr_test"
                assert metadata["extraction_version"] == "document-intake-v1"
                assert metadata["sha256"] == body["sha256"]
                assert db.query(CaptureAnalysis).filter_by(care_event_id=body["care_event_id"]).count() == 0
    finally:
        teardown_db()


def test_document_intake_rejects_content_type_spoofing():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_owner(client, "document-spoof")
            response = client.post(
                "/api/v1/documents/intake",
                headers=headers,
                data={"person_id": person_id, "caregiver_id": caregiver_id},
                files={"file": ("fake.pdf", b"not a PDF", "application/pdf")},
            )
            assert response.status_code == 415, response.text
    finally:
        teardown_db()
