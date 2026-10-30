"""Document-intelligence-to-LCR persistence and authorization tests."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion

main_module = importlib.import_module("app.main")
document_intelligence = importlib.import_module("app.api.document_intelligence")
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


def setup_client():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_session(client, caregiver_name="Family caregiver"):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": caregiver_name},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data["access_token"], data["person"]["id"], data["caregiver"]["id"]


def save_document(person_id, caregiver_id, text):
    db = TestingSessionLocal()
    try:
        evidence = Evidence(
            person_id=person_id,
            type="document",
            source_text=text,
            original_file_path=None,
            uploaded_by_caregiver_id=caregiver_id,
            extra_metadata=json.dumps({"filename": "discharge-summary.txt"}),
            source_type="caregiver_upload",
            evidence_status="recorded",
        )
        db.add(evidence)
        db.commit()
        db.refresh(evidence)
        return str(evidence.id)
    finally:
        db.close()


def test_document_extraction_requires_review_then_writes_source_linked_lcr_assertion(monkeypatch):
    client = setup_client()
    source = "The discharge note states: Patient walked 20 metres with a frame on 12 October."
    quote = "Patient walked 20 metres with a frame on 12 October."

    def extract_review_candidates(source_text, *, source_event_id, source_evidence_id=None):
        assert source_text == source
        assert source_event_id == ""
        return {
            "observations": [{
                "subject": "Patient",
                "observation": "Walked 20 metres with a frame on 12 October.",
                "source_quote": quote,
                "source_evidence_id": source_evidence_id,
                "review_status": "pending_review",
            }],
            "open_questions": ["Was this the person's usual mobility baseline?"],
        }

    monkeypatch.setattr(document_intelligence, "extract_review_candidates", extract_review_candidates)
    try:
        token, person_id, caregiver_id = create_session(client)
        evidence_id = save_document(person_id, caregiver_id, source)
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=headers)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["review_required"] is True
        assert data["written_to_lcr"] is False
        assert data["source_evidence_id"] == evidence_id
        assert data["document_intelligence"]["observations"][0]["source_quote"] == quote

        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0
        finally:
            db.close()

        reviewed = client.post(
            f"/api/v1/documents/intelligence/{data['analysis_id']}/review",
            headers=headers,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert reviewed.status_code == 200, reviewed.text
        result = reviewed.json()
        assert result["written_to_lcr"] is True
        assert result["source_record_ref"]["record_type"] == "care_assertion"

        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(person_id=person_id).one()
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.assertion_kind == "caregiver_reviewed_document_extraction"
            assert assertion.evidence_ids == [evidence_id]
            assert quote in assertion.source_text
            assert "20 metres" in assertion.asserted_value
        finally:
            db.close()
    finally:
        teardown()


def test_document_analysis_is_idempotent_and_reject_does_not_write_lcr(monkeypatch):
    client = setup_client()
    source = "The care note records: Ate half of lunch today."
    monkeypatch.setattr(
        document_intelligence,
        "extract_review_candidates",
        lambda source_text, **kwargs: {
            "observations": [{
                "subject": "Person",
                "observation": "Ate half of lunch today.",
                "source_quote": "Ate half of lunch today.",
                "review_status": "pending_review",
            }],
            "open_questions": [],
        },
    )
    try:
        token, person_id, caregiver_id = create_session(client)
        evidence_id = save_document(person_id, caregiver_id, source)
        headers = {"Authorization": f"Bearer {token}"}
        first = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=headers)
        second = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=headers)
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["analysis_id"] == second.json()["analysis_id"]

        rejected = client.post(
            f"/api/v1/documents/intelligence/{first.json()['analysis_id']}/review",
            headers=headers,
            json={"candidate_index": 0, "decision": "reject"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["written_to_lcr"] is False
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0
        finally:
            db.close()
    finally:
        teardown()


def test_document_analysis_rejects_empty_extracted_text(monkeypatch):
    client = setup_client()
    try:
        token, person_id, caregiver_id = create_session(client)
        evidence_id = save_document(person_id, caregiver_id, "   ")
        response = client.post(
            f"/api/v1/documents/{evidence_id}/intelligence",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422
        assert "no readable extracted text" in response.json()["detail"].lower()
    finally:
        teardown()


def test_document_analysis_does_not_disclose_another_persons_document(monkeypatch):
    client = setup_client()
    try:
        token_a, person_a, caregiver_a = create_session(client, "Caregiver A")
        token_b, _, _ = create_session(client, "Caregiver B")
        evidence_id = save_document(person_a, caregiver_a, "The source says: slept well.")
        response = client.post(
            f"/api/v1/documents/{evidence_id}/intelligence",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == 404
    finally:
        teardown()
