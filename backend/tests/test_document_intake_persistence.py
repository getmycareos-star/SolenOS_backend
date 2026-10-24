"""End-to-end tests for private document storage, provenance, candidate review and access scope."""
import json
import importlib
from io import BytesIO
from unittest.mock import patch
import pytest

pytestmark = pytest.mark.session_security
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from PIL import Image

from app.core.database import Base, get_db
from app.main import app
main_module = importlib.import_module("app.main")
from app.models.care import CareEvent, Caregiver, Evidence
from app.models.document_asset import DocumentAsset

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
_original_session_local = main_module.SessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestingSessionLocal


def teardown_db():
    app.dependency_overrides.pop(get_db, None)
    main_module.SessionLocal = _original_session_local
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def create_session(client: TestClient, name: str):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Document Caregiver"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}, data


def test_intake_preserves_original_source_and_extracts_review_only_candidates():
    setup_db()
    try:
        source = (
            "DISCHARGE SUMMARY\n"
            "Date: 2026-10-20\n"
            "Medication list: Medication A 5 mg daily.\n"
            "Follow-up appointment: Call the clinic next week.\n"
            "Test result: potassium result is high."
        ).encode()
        with TestClient(app) as client:
            headers, session = create_session(client, "Document Intake Recipient")
            response = client.post(
                "/api/v1/documents/intake",
                headers=headers,
                files={"file": ("discharge.txt", source, "text/plain")},
            )
            assert response.status_code == 201, response.text
            result = response.json()
            assert result["source_file_preserved"] is True
            assert result["extraction_status"] == "extracted"
            assert result["document_type_candidate"] == "discharge_instructions"
            assert result["document_date_candidate"] == "2026-10-20"
            assert result["candidate_count"] >= 3
            assert {item["candidate_type"] for item in result["document_candidates"]} >= {
                "medication", "follow_up", "result"
            }
            assert all(item["review_status"] == "pending_review" for item in result["document_candidates"])
            assert all(item["source_quote"] in source.decode() for item in result["document_candidates"])
            assert all(source.decode()[item["source_span_start"]:item["source_span_end"]] == item["source_quote"] for item in result["document_candidates"])

            with TestingSessionLocal() as db:
                evidence = db.query(Evidence).filter_by(id=result["evidence_id"]).one()
                event = db.query(CareEvent).filter_by(id=result["care_event_id"]).one()
                asset = db.query(DocumentAsset).filter_by(evidence_id=evidence.id).one()
                assert evidence.person_id == session["person"]["id"]
                assert evidence.uploaded_by_caregiver_id == session["caregiver"]["id"]
                assert evidence.source_type == "document_upload"
                assert evidence.evidence_status == "uploaded"
                assert evidence.source_text == source.decode()
                assert event.evidence_ids == [evidence.id]
                assert event.time_provenance == "document_upload_time_not_document_date"
                assert asset.content == source
                assert asset.sha256 == result["sha256"]
                metadata = json.loads(evidence.extra_metadata)
                assert metadata["extraction_version"] == "document-intake-v1"
                assert metadata["document_date_review_status"] == "pending_review"

            download = client.get(
                f"/api/v1/documents/{result['evidence_id']}/file",
                headers=headers,
            )
            assert download.status_code == 200, download.text
            assert download.content == source
            assert download.headers["cache-control"] == "private, no-store"

            review = client.post(
                f"/api/v1/documents/{result['evidence_id']}/candidates/0/review",
                headers=headers,
                json={"decision": "accept"},
            )
            assert review.status_code == 200, review.text
            assert review.json()["authoritative_lcr_write"] is False
            assert review.json()["candidate"]["review_status"] == "accepted_by_caregiver"
            assert review.json()["candidate"]["reviewed_by_caregiver_id"] == session["caregiver"]["id"]

            with TestingSessionLocal() as db:
                saved = db.query(Evidence).filter_by(id=result["evidence_id"]).one()
                metadata = json.loads(saved.extra_metadata)
                assert metadata["document_candidates"][0]["review_status"] == "accepted_by_caregiver"
                assert len(metadata["document_review_history"]) == 1
    finally:
        teardown_db()


def test_document_binary_and_review_are_scoped_to_the_authenticated_record():
    setup_db()
    try:
        with TestClient(app) as client:
            owner_headers, owner = create_session(client, "Document Owner")
            other_headers, _other = create_session(client, "Other Recipient")
            response = client.post(
                "/api/v1/documents/intake",
                headers=owner_headers,
                files={"file": ("care.txt", b"Follow-up appointment next week.", "text/plain")},
            )
            assert response.status_code == 201, response.text
            evidence_id = response.json()["evidence_id"]

            download = client.get(f"/api/v1/documents/{evidence_id}/file", headers=other_headers)
            assert download.status_code in {403, 404}, download.text
            review = client.post(
                f"/api/v1/documents/{evidence_id}/candidates/0/review",
                headers=other_headers,
                json={"decision": "accept"},
            )
            assert review.status_code in {403, 404}, review.text
    finally:
        teardown_db()


def test_document_extraction_rejects_mime_and_signature_mismatch():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, _session = create_session(client, "Document Spoof Check")
            response = client.post(
                "/api/v1/documents/intake",
                headers=headers,
                files={"file": ("fake.pdf", b"not a PDF", "application/pdf")},
            )
            assert response.status_code == 415, response.text
    finally:
        teardown_db()


def test_image_intake_uses_ocr_text_but_keeps_it_as_unverified_source_candidates():
    setup_db()
    try:
        image_buffer = BytesIO()
        Image.new("RGB", (3, 3), color=(100, 120, 140)).save(image_buffer, format="PNG")
        image_bytes = image_buffer.getvalue()
        ocr_text = (
            "MEDICATION LIST\n"
            "Take Medication B 10 mg daily.\n"
            "Follow-up appointment: Call the clinic in two weeks."
        )
        with TestClient(app) as client:
            headers, session = create_session(client, "Image Document Recipient")
            with patch("app.api.document_extraction._ocr_image", return_value=(ocr_text, 0.91)):
                response = client.post(
                    "/api/v1/documents/intake",
                    headers=headers,
                    files={"file": ("medication.png", image_bytes, "image/png")},
                )
            assert response.status_code == 201, response.text
            result = response.json()
            assert result["extraction_status"] == "extracted"
            assert result["extraction_source"] == "image_ocr"
            assert result["ocr_confidence"] == 0.91
            assert result["source_file_preserved"] is True
            assert any(candidate["candidate_type"] == "medication" for candidate in result["document_candidates"])
            assert any(candidate["candidate_type"] == "follow_up" for candidate in result["document_candidates"])
            with TestingSessionLocal() as db:
                asset = db.query(DocumentAsset).filter_by(evidence_id=result["evidence_id"]).one()
                evidence = db.query(Evidence).filter_by(id=result["evidence_id"]).one()
                assert asset.content == image_bytes
                assert evidence.source_text == ocr_text
                assert evidence.confidence == 0.91
                assert evidence.evidence_status == "uploaded"
                assert all(candidate["review_status"] == "pending_review" for candidate in json.loads(evidence.extra_metadata)["document_candidates"])
    finally:
        teardown_db()



def test_candidate_review_fails_closed_on_malformed_persisted_metadata():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, session = create_session(client, "Malformed Review Metadata")
            response = client.post(
                "/api/v1/documents/intake",
                headers=headers,
                files={"file": ("care.txt", b"Follow-up appointment: call the clinic next week.", "text/plain")},
            )
            assert response.status_code == 201, response.text
            evidence_id = response.json()["evidence_id"]

            with TestingSessionLocal() as db:
                evidence = db.query(Evidence).filter_by(id=evidence_id).one()
                metadata = json.loads(evidence.extra_metadata)
                metadata["document_candidates"] = [None]
                metadata["document_review_history"] = "not-a-list"
                evidence.extra_metadata = json.dumps(metadata)
                db.commit()

            malformed_candidate = client.post(
                f"/api/v1/documents/{evidence_id}/candidates/0/review",
                headers=headers,
                json={"decision": "accept"},
            )
            assert malformed_candidate.status_code == 409, malformed_candidate.text
            assert "malformed" in malformed_candidate.json()["detail"].lower()

            with TestingSessionLocal() as db:
                evidence = db.query(Evidence).filter_by(id=evidence_id).one()
                evidence.extra_metadata = json.dumps(["unexpected", "metadata", "shape"])
                db.commit()

            malformed_metadata = client.post(
                f"/api/v1/documents/{evidence_id}/candidates/0/review",
                headers=headers,
                json={"decision": "accept"},
            )
            assert malformed_metadata.status_code == 409, malformed_metadata.text
            assert "metadata" in malformed_metadata.json()["detail"].lower()
    finally:
        teardown_db()
