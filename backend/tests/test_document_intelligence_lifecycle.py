"""Document intelligence review gates, persistence, and ownership isolation."""
import hashlib
import importlib
import json

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion
from app.models.collaboration import EvidenceUpload
from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent

main_module = importlib.import_module("app.main")
intelligence_module = importlib.import_module("app.api.document_intelligence")
lifecycle_module = importlib.import_module("app.api.document_lifecycle")
app = main_module.app
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


def create_record(client, name):
    response = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": name})
    assert response.status_code == 201, response.text
    return response.json()


def save_original(session_data, source_text):
    data = source_text.encode("utf-8")
    evidence_id = "document-" + hashlib.sha256(data + session_data["person"]["id"].encode()).hexdigest()[:24]
    db = Session()
    try:
        evidence = Evidence(
            id=evidence_id,
            person_id=session_data["person"]["id"],
            type="document",
            source_text="",
            uploaded_by_caregiver_id=session_data["caregiver"]["id"],
            extra_metadata=json.dumps({"filename": "discharge-summary.txt"}),
            source_type="caregiver_upload",
            evidence_status="recorded",
        )
        db.add(evidence)
        db.flush()
        db.add(EvidenceUpload(
            evidence_id=evidence_id,
            filename="discharge-summary.txt",
            mime_type="text/plain",
            content=data,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ))
        db.commit()
    finally:
        db.close()
    return evidence_id


def test_document_understanding_review_accept_reject_reload_and_cross_person(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = (
        "The discharge summary states: Patient walked 20 metres with a frame on 12 October. "
        "The patient ate half of lunch during the observation period."
    )
    quotes = [
        "Patient walked 20 metres with a frame on 12 October.",
        "The patient ate half of lunch during the observation period.",
    ]

    def fake_extract(text, *, source_event_id, source_evidence_id=None):
        assert text == source
        return {"observations": [
            *[
                {"subject": "Patient", "observation": quote, "source_quote": quote,
                 "extraction_method": "test_verbatim"} for quote in quotes
            ],
            {"subject": "Patient", "observation": "Invented diagnosis", "source_quote": "Patient has a confirmed diagnosis that is not in this document."},
            {"subject": "Patient", "observation": "Patient recovered completely", "source_quote": "Patient walked 20 metres with a frame on 12 October."},
        ], "open_questions": ["Was this typical for the person?"]}

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        first = create_record(client, "Caregiver A")
        auth = {"Authorization": "Bearer " + first["access_token"]}
        evidence_id = save_original(first, source)

        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        analysis_id = extracted.json()["analysis_id"]

        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 201, understood.text
        assert understood.json()["review_required"] is True
        assert understood.json()["written_to_lcr"] is False
        assert all(item["source_quote"] in source for item in understood.json()["observations"])

        # Re-running understanding is idempotent and does not create a second analysis.
        repeated = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert repeated.status_code == 201
        assert repeated.json()["analysis_id"] == analysis_id
        assert len(repeated.json()["observations"]) == 2
        assert all(item["source_quote"] in source for item in repeated.json()["observations"])

        # Reload both the analysis and the extracted text through authenticated reads.
        details = client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=auth)
        assert details.status_code == 200, details.text
        assert details.json()["extracted_text"] == source
        library = client.get("/api/v1/documents/intelligence", headers=auth)
        assert library.status_code == 200
        assert len(library.json()["items"]) == 1

        # Once extraction is persisted with source hashes, reload/review must not rerun OCR.
        def unexpected_ocr(*args, **kwargs):
            raise AssertionError("Persisted extraction reads must not rerun OCR")
        monkeypatch.setattr(lifecycle_module, "_extract", unexpected_ocr)

        rejected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "reject"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["written_to_lcr"] is False

        corrected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Person",
                "corrected_observation": "Caregiver-reviewed correction: ate approximately half of lunch.",
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["written_to_lcr"] is False
        corrected_again = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Person",
                "corrected_observation": "Caregiver-reviewed correction: ate approximately half of lunch.",
            },
        )
        assert corrected_again.status_code == 200, corrected_again.text
        assert corrected_again.json()["idempotent_replay"] is True
        db = Session()
        try:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0
        finally:
            db.close()

        accepted = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True

        # Repeating the same acceptance returns the existing assertion; it never duplicates it.
        accepted_again = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert accepted_again.status_code == 200
        assert accepted_again.json()["source_record_ref"] == accepted.json()["source_record_ref"]

        history_response = client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers=auth,
        )
        assert history_response.status_code == 200, history_response.text
        history = history_response.json()
        assert history["count"] == 3
        assert [item["decision"] for item in history["items"]] == ["reject", "correct", "accept"]
        assert history["history_authority"] == "append_only_document_review_events"

        db = Session()
        try:
            event_row = db.query(DocumentReviewEvent).filter_by(
                analysis_id=analysis_id, candidate_index=1, decision_version=1
            ).one()
            try:
                event_row.rationale = "attempted history rewrite"
                db.commit()
                raise AssertionError("Document review history mutation unexpectedly succeeded")
            except ValueError as exc:
                assert "append-only" in str(exc)
                db.rollback()
        finally:
            db.close()

        db = Session()
        try:
            # Reload the assertion after the SQL Core review transition.
            db.expire_all()
            assertions = db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).all()
            assert len(assertions) == 1
            assertion = assertions[0]
            assert assertion.assertion_status == "accepted"
            assert assertion.assertion_kind == "document_reviewed_observation"
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quotes[1]
            assert assertion.created_by_caregiver_id == first["caregiver"]["id"]
        finally:
            db.close()
        lcr_assertions = client.get(
            f"/api/v1/care-continuity/assertions/{first['person']['id']}", headers=auth
        )
        assert lcr_assertions.status_code == 200, lcr_assertions.text
        assert any(
            item["id"] == accepted.json()["source_record_ref"]["record_id"]
            and item["assertion_status"] == "accepted"
            for item in lcr_assertions.json()
        )

        # A tampered persisted extraction must not be served after candidate generation.
        db = Session()
        try:
            row = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
            row.source_text = row.source_text + " Tampered after analysis."
            db.commit()
        finally:
            db.close()
        tampered = client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=auth)
        assert tampered.status_code == 409

        second = create_record(client, "Caregiver B")
        foreign = {"Authorization": "Bearer " + second["access_token"]}
        denied_analysis = client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=foreign)
        assert denied_analysis.status_code == 404
        denied_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=foreign,
            json={"candidate_index": 0, "decision": "accept"},
        )
        assert denied_review.status_code == 404
        denied_history = client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers=foreign,
        )
        assert denied_history.status_code == 404
        denied_source = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=foreign)
        assert denied_source.status_code == 404
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_lcr_assertion_and_review_event_roll_back_together(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    client = TestClient(app)
    source = "Discharge note: Patient walked 20 metres with a frame."
    quote = "Patient walked 20 metres with a frame."

    def fake_extract(text_value, *, source_event_id, source_evidence_id=None):
        return {"observations": [{
            "subject": "Patient",
            "observation": quote,
            "source_quote": quote,
            "extraction_method": "test_verbatim",
        }], "open_questions": []}

    monkeypatch.setattr(intelligence_module, "extract_review_candidates", fake_extract)
    try:
        caregiver = create_record(client, "Atomic review caregiver")
        auth = {"Authorization": "Bearer " + caregiver["access_token"]}
        evidence_id = save_original(caregiver, source)
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
        assert extracted.status_code == 201, extracted.text
        analysis_id = extracted.json()["analysis_id"]
        understood = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert understood.status_code == 201, understood.text

        with Session() as db:
            db.execute(text("""
                CREATE TRIGGER fail_document_review_event_insert
                BEFORE INSERT ON document_review_events
                BEGIN
                    SELECT RAISE(ABORT, 'forced review event insert failure');
                END
            """))
            db.commit()

        with pytest.raises(IntegrityError):
            client.post(
                f"/api/v1/documents/intelligence/{analysis_id}/review",
                headers=auth,
                json={"candidate_index": 0, "decision": "accept"},
            )

        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=caregiver["person"]["id"]).count() == 0
            assert db.query(DocumentReviewEvent).filter_by(analysis_id=analysis_id).count() == 0
            persisted = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
            observation = persisted.candidates["document_intelligence"]["observations"][0]
            assert observation["review_status"] == "pending_review"
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
