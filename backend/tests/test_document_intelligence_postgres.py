"""Real PostgreSQL document upload-to-LCR review persistence test.

The CI job supplies a migrated disposable PostgreSQL DATABASE_URL. Qwen is
stubbed only at the inference boundary so this test isolates database/API
lifecycle guarantees; source-quote grounding is separately unit tested.
"""
import asyncio
import hashlib
import io
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.main import app
import app.main as main_module
import app.api.document_lifecycle as lifecycle_module
from app.api.collaboration import upload_evidence
from app.api.document_lifecycle import extract_saved_document, get_document_extraction
from app.api.document_intelligence import (
    CandidateReview,
    analyze_saved_document,
    list_document_intelligence,
    queue_document_intake,
    review_document_candidate,
)
from app.models.care import Caregiver, Person
from app.models.care_continuity import CareAssertion
from app.models.qwen_background_job import QwenBackgroundJob
from app.models.uncertainty import OpenQuestion
from app.models.collaboration import EvidenceUpload
from app.services.lcr_context import build_lcr_context

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires real PostgreSQL DATABASE_URL",
)


def _request(person_id: str, caregiver_id: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.scoped_person_id = person_id
    request.state.scoped_caregiver_id = caregiver_id
    return request



def _restart_postgres_service_container(engine):
    """Restart the disposable CI PostgreSQL service and prove committed rows survive."""
    if os.getenv("SOLENOS_TEST_POSTGRES_RESTART") != "1":
        return
    containers = subprocess.run(
        ["docker", "ps", "--filter", "ancestor=postgres:17", "--format", "{{.ID}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    containers = [value.strip() for value in containers if value.strip()]
    assert len(containers) == 1, f"Expected one PostgreSQL 17 service container, found {len(containers)}"
    engine.dispose()
    subprocess.run(
        ["docker", "restart", containers[0]],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    deadline = time.monotonic() + 90
    last_error = None
    while time.monotonic() < deadline:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"PostgreSQL did not recover after service restart: {last_error!r}")


def test_postgres_document_upload_extraction_reload_review_and_lcr_reconstruction(monkeypatch):
    from app.api import document_intelligence

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)
    from app.services import qwen_background_worker as worker
    monkeypatch.setattr(worker, "SessionLocal", Session)

    async def idle_document_worker():
        await asyncio.Event().wait()

    monkeypatch.setattr(main_module, "qwen_worker_loop", idle_document_worker)
    api_client = TestClient(app)
    original = (
        b"Discharge summary: Patient walked 20 metres with a frame on 12 October. "
        b"The patient ate half of lunch during the observation period."
    )
    source = original.decode("utf-8")
    quote_one = "Patient walked 20 metres with a frame on 12 October."
    quote_two = "The patient ate half of lunch during the observation period."

    def deterministic_candidates(text, *, source_event_id, source_evidence_id=None):
        assert text == source
        return {"observations": [
            {"subject": "Patient", "observation": quote_one, "source_quote": quote_one, "extraction_method": "test_verbatim"},
            {"subject": "Patient", "observation": quote_two, "source_quote": quote_two, "extraction_method": "test_verbatim"},
        ], "open_questions": ["Was this usual for the person?"]}

    monkeypatch.setattr(document_intelligence, "extract_review_candidates", deterministic_candidates)
    try:
        # Create both identities through the real authenticated-session API.
        # This exercises the production caregiver/person linkage instead of
        # manually constructing rows that can diverge from session invariants.
        first_session = api_client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "PostgreSQL document recipient"},
        )
        assert first_session.status_code == 201, first_session.text
        first_identity = first_session.json()
        person_id = first_identity["person"]["id"]
        caregiver_id = first_identity["caregiver"]["id"]
        token = first_identity["access_token"]

        second_session = api_client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Other PostgreSQL recipient"},
        )
        assert second_session.status_code == 201, second_session.text
        second_identity = second_session.json()
        other_person_id = second_identity["person"]["id"]
        other_caregiver_id = second_identity["caregiver"]["id"]
        other_token = second_identity["access_token"]

        # Exercise the real multipart upload handler and persisted EvidenceUpload bytes.
        with Session() as db:
            uploaded = asyncio.run(upload_evidence(
                _request(person_id, caregiver_id),
                UploadFile(
                    filename="discharge-summary.txt",
                    file=io.BytesIO(original),
                    headers=Headers({"content-type": "text/plain"}),
                ),
                person_id,
                caregiver_id,
                "document",
                "",
                "not_provided",
                "0",
                json.dumps({"filename": "discharge-summary.txt"}),
                "false",
                db,
            ))
            assert uploaded["persisted"] is True
            evidence_id = uploaded["evidence_id"]
            processing_job_id = uploaded["document_processing_job_id"]
            assert processing_job_id
            assert uploaded["processing_status"] == "queued"
            assert uploaded["sha256"] == hashlib.sha256(original).hexdigest()

        with Session() as db:
            processing_job = db.query(QwenBackgroundJob).filter_by(id=processing_job_id).one()
            assert processing_job.workflow == "document_intake"
            assert processing_job.status == "queued"
            assert json.loads(processing_job.evidence_ids_json) == [evidence_id]
            queue_state = list_document_intelligence(_request(person_id, caregiver_id), 50, db)
            assert any(
                item["evidence_id"] == evidence_id and item["status"] == "queued"
                for item in queue_state["processing_jobs"]
            )
            replayed_queue = queue_document_intake(
                evidence_id, _request(person_id, caregiver_id), db
            )
            assert replayed_queue["job_id"] == processing_job_id
            assert replayed_queue["idempotent_replay"] is True

        # Execute the real durable worker against PostgreSQL. Only the candidate
        # model output is deterministic; upload, extraction, persistence and job
        # lifecycle all use the real database and service implementations.
        assert asyncio.run(worker.process_one_qwen_job()) is True
        with Session() as db:
            completed_job = db.query(QwenBackgroundJob).filter_by(id=processing_job_id).one()
            assert completed_job.status == "completed"
            worker_result = json.loads(completed_job.result_text)
            assert worker_result[0]["evidence_id"] == evidence_id
            assert worker_result[0]["intelligence_status"] == "available"
            assert worker_result[0]["candidate_count"] == 2

        # Extraction and candidate generation are persisted in the same analysis row.
        with Session() as db:
            extracted = extract_saved_document(evidence_id, _request(person_id, caregiver_id), db)
            analysis_id = extracted["analysis_id"]
            assert extracted["extraction_method"] == "server_utf8_text"
            assert extracted["written_to_lcr"] is False

        with Session() as db:
            understood = analyze_saved_document(evidence_id, _request(person_id, caregiver_id), db)
            assert understood["analysis_id"] == analysis_id
            assert understood["review_required"] is True
            assert understood["written_to_lcr"] is False
            assert len(understood["observations"]) == 2

        # Extraction questions must be durable, linked to the saved document,
        # and visible through the same OpenQuestion store used by the LCR.
        with Session() as db:
            clarification = db.query(OpenQuestion).filter_by(
                person_id=person_id,
                status="open",
                question="Was this usual for the person?",
            ).one()
            assert clarification.evidence_ids == [evidence_id]
            assert clarification.created_by_caregiver_id == caregiver_id
            assert clarification.context[0]["source_kind"] == "document_extraction"
            assert clarification.context[0]["source_record_refs"] == [
                {"record_type": "evidence", "record_id": evidence_id}
            ]

        # Replaying document understanding must not create a duplicate question.
        with Session() as db:
            replayed = analyze_saved_document(evidence_id, _request(person_id, caregiver_id), db)
            assert replayed["analysis_id"] == analysis_id
        with Session() as db:
            assert db.query(OpenQuestion).filter_by(
                person_id=person_id,
                status="open",
                question="Was this usual for the person?",
            ).count() == 1

        # Persisted extraction reads and reviews must use hashes, not rerun expensive OCR.
        def unexpected_ocr(*args, **kwargs):
            raise AssertionError("Persisted extraction reads must not rerun OCR")
        monkeypatch.setattr(lifecycle_module, "_extract", unexpected_ocr)

        # Simulate reload: retrieve the extraction and candidate state in fresh sessions.
        with Session() as db:
            reloaded = get_document_extraction(analysis_id, _request(person_id, caregiver_id), db)
            assert reloaded["extracted_text"] == source
            assert reloaded["document"]["binary_sha256"] == hashlib.sha256(original).hexdigest()
        with Session() as db:
            reviewed_state = document_intelligence.list_document_intelligence(
                _request(person_id, caregiver_id), 50, db
            )
            assert reviewed_state["count"] == 1
            assert len(reviewed_state["items"][0]["observations"]) == 2

        # Reject one candidate (no assertion), accept the other (one attributed assertion).
        with Session() as db:
            rejected = review_document_candidate(
                analysis_id, CandidateReview(candidate_index=0, decision="reject"),
                _request(person_id, caregiver_id), db,
            )
            assert rejected["written_to_lcr"] is False
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0
        with Session() as db:
            accepted = review_document_candidate(
                analysis_id, CandidateReview(candidate_index=1, decision="accept"),
                _request(person_id, caregiver_id), db,
            )
            assert accepted["written_to_lcr"] is True
            assertion_id = accepted["source_record_ref"]["record_id"]

        # Restart the actual PostgreSQL service after the accepted decision has committed.
        # A fresh SQLAlchemy session alone does not prove database-process durability.
        _restart_postgres_service_container(engine)
        with Session() as db:
            original_upload = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
            assert original_upload.content == original
            assert original_upload.sha256 == hashlib.sha256(original).hexdigest()
            after_restart = get_document_extraction(
                analysis_id, _request(person_id, caregiver_id), db
            )
            assert after_restart["extracted_text"] == source
            assert after_restart["document"]["binary_sha256"] == hashlib.sha256(original).hexdigest()
            assert after_restart["candidate_count"] == 2

        # Restart-equivalent persistence check: a new session reconstructs the LCR assertion.
        with Session() as db:
            assertion = db.query(CareAssertion).filter_by(
                id=assertion_id, person_id=person_id
            ).one()
            assert assertion.assertion_status == "accepted"
            assert assertion.assertion_kind == "document_reviewed_observation"
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quote_two
            assert assertion.created_by_caregiver_id == caregiver_id
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 1
            lcr_context = build_lcr_context(db, person_id, limit_per_source=50)
            assertion_source = lcr_context["sources"]["assertions"]
            assert assertion_source["status"] == "available"
            assert any(row["id"] == assertion_id for row in assertion_source["items"])

        # The canonical LCR read API must reconstruct the accepted assertion.
        lcr_rows = api_client.get(
            f"/api/v1/care-continuity/assertions/{person_id}",
            headers={"Authorization": "Bearer " + token},
        )
        assert lcr_rows.status_code == 200, lcr_rows.text
        assert any(row["id"] == assertion_id and row["assertion_status"] == "accepted" for row in lcr_rows.json())

        summary = api_client.get("/api/v1/care-continuity/summary", headers={"Authorization": "Bearer " + token})
        assert summary.status_code == 200, summary.text
        assert summary.json()["resources"]["assertions"] == 1

        history_response = api_client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers={"Authorization": "Bearer " + token},
        )
        assert history_response.status_code == 200, history_response.text
        assert history_response.json()["count"] == 2
        assert [item["decision"] for item in history_response.json()["items"]] == ["reject", "accept"]

        denied_history = api_client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers={"Authorization": "Bearer " + other_token},
        )
        assert denied_history.status_code == 404, denied_history.text

        with Session() as db:
            event_id = db.execute(
                text(
                    "SELECT id FROM document_review_events "
                    "WHERE analysis_id = :analysis_id AND candidate_index = 0"
                ),
                {"analysis_id": analysis_id},
            ).scalar_one()
            with pytest.raises(DBAPIError):
                db.execute(
                    text("UPDATE document_review_events SET rationale = 'tampered' WHERE id = :event_id"),
                    {"event_id": event_id},
                )
            db.rollback()

        with Session() as db:
            revision_count = db.execute(
                text("SELECT COUNT(*) FROM care_state_revisions WHERE target_type = 'care_assertions' AND target_id = :assertion_id"),
                {"assertion_id": assertion_id},
            ).scalar_one()
            assert revision_count >= 1

        with Session() as db:
            persisted_after_review = get_document_extraction(
                analysis_id, _request(person_id, caregiver_id), db
            )
            assert persisted_after_review["candidate_count"] == 2
            assert persisted_after_review["written_to_lcr"] is True
            assert persisted_after_review["review_required"] is False

        # Cross-person reads and writes are denied even when IDs are known.
        with Session() as db:
            with pytest.raises(HTTPException) as denied:
                get_document_extraction(analysis_id, _request(other_person_id, other_caregiver_id), db)
            assert denied.value.status_code == 404
        with Session() as db:
            with pytest.raises(HTTPException) as denied:
                review_document_candidate(
                    analysis_id, CandidateReview(candidate_index=0, decision="accept"),
                    _request(other_person_id, other_caregiver_id), db,
                )
            assert denied.value.status_code == 404
    finally:
        api_client.close()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()



def test_postgres_authenticated_http_document_review_correction_and_lcr(monkeypatch):
    """Exercise the complete caregiver-facing document-to-LCR API on real PostgreSQL."""
    from app.api import document_intelligence

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", Session)

    source = (
        "Discharge summary. Patient walked 20 metres with a frame on 12 October. "
        "The patient ate half of lunch during the observation period."
    )
    quote_reject = "Patient walked 20 metres with a frame on 12 October."
    quote_correct = "The patient ate half of lunch during the observation period."

    def deterministic_candidates(extracted_text, *, source_event_id, source_evidence_id=None):
        assert extracted_text == source
        return {
            "observations": [
                {
                    "subject": "Patient",
                    "observation": quote_reject,
                    "source_quote": quote_reject,
                    "extraction_method": "postgresql_http_test_verbatim",
                },
                {
                    "subject": "Patient",
                    "observation": quote_correct,
                    "source_quote": quote_correct,
                    "extraction_method": "postgresql_http_test_verbatim",
                },
                {
                    "subject": "Patient",
                    "observation": "Invented diagnosis",
                    "source_quote": "The source contains a diagnosis that it does not contain.",
                },
                {
                    "subject": "Patient",
                    "observation": "Patient recovered completely",
                    "source_quote": quote_reject,
                },
            ],
            "open_questions": [],
        }

    monkeypatch.setattr(document_intelligence, "extract_review_candidates", deterministic_candidates)
    client = TestClient(app)
    try:
        owner = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "PostgreSQL end-to-end document caregiver"},
        )
        other = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Different PostgreSQL caregiver"},
        )
        assert owner.status_code == 201, owner.text
        assert other.status_code == 201, other.text
        identity = owner.json()
        auth = {"Authorization": "Bearer " + identity["access_token"]}
        other_auth = {"Authorization": "Bearer " + other.json()["access_token"]}
        person_id = identity["person"]["id"]
        caregiver_id = identity["caregiver"]["id"]

        uploaded = client.post(
            "/api/v1/upload",
            headers=auth,
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "document",
            },
            files={"file": ("postgres-discharge-summary.txt", source.encode("utf-8"), "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        upload_receipt = uploaded.json()
        evidence_id = upload_receipt["evidence_id"]
        assert upload_receipt["persisted"] is True
        assert upload_receipt["sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()

        original = client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
        assert original.status_code == 200, original.text
        assert hashlib.sha256(original.content).hexdigest() == upload_receipt["sha256"]

        extracted = client.post(
            f"/api/v1/documents/{evidence_id}/extract",
            headers=auth,
            json={},
        )
        assert extracted.status_code == 201, extracted.text
        analysis_id = extracted.json()["analysis_id"]
        assert extracted.json()["extraction_method"] == "server_utf8_text"
        assert extracted.json()["written_to_lcr"] is False

        understood = client.post(
            f"/api/v1/documents/{evidence_id}/intelligence",
            headers=auth,
            json={},
        )
        assert understood.status_code == 201, understood.text
        candidates = understood.json()["observations"]
        assert len(candidates) == 2
        assert all(item["review_status"] == "pending_review" for item in candidates)
        assert all(item["source_evidence_id"] == evidence_id for item in candidates)
        assert all(item["source_quote"] in source for item in candidates)
        assert understood.json()["written_to_lcr"] is False
        provenance = understood.json()["model_provenance"]
        assert provenance["provider"] in {"private_ollama", "unavailable_or_test_stub"}
        assert provenance["processing_version"] == "document_intelligence_v1"
        assert provenance["prompt_version"] == "source_grounded_review_candidates_v1"
        assert provenance["extraction_source"] == "persisted_server_extraction"
        assert provenance["source_evidence_id"] == evidence_id
        assert provenance["source_text_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
        assert provenance["model_authority"] == "review_only"
        assert provenance["model_digest"] is None
        assert provenance["model_digest_status"] == "not_exposed_by_ollama_chat_response"
        assert understood.json()["processing_history"][-1] == provenance

        # Rejecting a candidate and correcting another do not create LCR assertions.
        rejected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "reject", "rationale": "Not relevant to current care."},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["written_to_lcr"] is False

        corrected_observation = "Caregiver correction: approximately half of lunch was eaten."
        corrected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Person",
                "corrected_observation": corrected_observation,
                "rationale": "Clarified the wording for the care record.",
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["candidate"]["review_status"] == "corrected_pending_approval"
        assert corrected.json()["written_to_lcr"] is False

        correction_retry = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Person",
                "corrected_observation": corrected_observation,
                "rationale": "Clarified the wording for the care record.",
            },
        )
        assert correction_retry.status_code == 200, correction_retry.text
        assert correction_retry.json()["idempotent_replay"] is True

        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

        # Explicit acceptance of the corrected version creates the sole assertion.
        accepted = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True
        execution = accepted.json()["care_continuity_execution"]
        assert execution["workflow"] == "document_candidate_review"
        assert execution["source_evidence_id"] == evidence_id
        assert execution["source_assertion_id"] is None
        assert execution["stages"]["reconciliation"] == "committed"
        assert execution["transaction_owner"] == "caller"
        assert accepted.json()["change_intelligence_status"] == "available"
        assertion_id = accepted.json()["source_record_ref"]["record_id"]

        accepted_retry = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert accepted_retry.status_code == 200, accepted_retry.text
        assert accepted_retry.json()["source_record_ref"]["record_id"] == assertion_id
        assert accepted_retry.json()["written_to_lcr"] is True

        # A later reconciliation may supersede the accepted assertion. Replaying
        # the original document decision must not claim it is still active.
        with Session() as db:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id, person_id=person_id).one()
            assertion.assertion_status = "superseded"
            db.commit()
        superseded_retry = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert superseded_retry.status_code == 200, superseded_retry.text
        assert superseded_retry.json()["idempotent_replay"] is True
        assert superseded_retry.json()["written_to_lcr"] is False
        assert superseded_retry.json()["source_record_ref"] is None
        with Session() as db:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id, person_id=person_id).one()
            assertion.assertion_status = "accepted"
            db.commit()

        with Session() as db:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id, person_id=person_id).one()
            assert assertion.assertion_status == "accepted"
            assert assertion.assertion_kind == "document_reviewed_observation"
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quote_correct
            assert assertion.asserted_value == (
                f"Subject: Person\nStatement: {corrected_observation}\n"
                "Event date: Not stated or unresolved\n"
                "Source-recorded date: Not stated in document\n"
                "Review warnings: Caregiver-corrected wording requires review against the original source passage."
            )
            assert assertion.created_by_caregiver_id == caregiver_id
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 1

        lcr = client.get(f"/api/v1/care-continuity/assertions/{person_id}", headers=auth)
        assert lcr.status_code == 200, lcr.text
        assert any(
            item["id"] == assertion_id and item["assertion_status"] == "accepted"
            for item in lcr.json()
        )

        # Rotate the server-issued bearer capability, then reconstruct the same
        # accepted assertion through both the LCR API and Companion retrieval context.
        refreshed = client.post("/api/v1/sessions/refresh", headers=auth)
        assert refreshed.status_code == 200, refreshed.text
        refreshed_auth = {"Authorization": "Bearer " + refreshed.json()["access_token"]}
        assert refreshed.json()["person_id"] == person_id
        assert refreshed.json()["caregiver_id"] == caregiver_id
        assert refreshed.json()["access_token"] != identity["access_token"]

        historical_analysis = client.get(
            f"/api/v1/documents/analyses/{analysis_id}", headers=refreshed_auth
        )
        assert historical_analysis.status_code == 200, historical_analysis.text
        historical_lcr = client.get(
            f"/api/v1/care-continuity/assertions/{person_id}", headers=refreshed_auth
        )
        assert historical_lcr.status_code == 200, historical_lcr.text
        assert any(item["id"] == assertion_id for item in historical_lcr.json())
        with Session() as db:
            companion_context = build_lcr_context(db, person_id, limit_per_source=50)
            context_assertions = companion_context["sources"]["assertions"]["items"]
            assert any(item["id"] == assertion_id for item in context_assertions)

        # The rotated token replaces the old capability; it cannot be reused.
        stale_token = client.get(
            f"/api/v1/documents/analyses/{analysis_id}", headers=auth
        )
        assert stale_token.status_code == 401

        auth = refreshed_auth
        history = client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history",
            headers=auth,
        )
        assert history.status_code == 200, history.text
        assert history.json()["count"] == 3
        assert [item["decision"] for item in history.json()["items"]] == ["reject", "correct", "accept"]
        correction_event = history.json()["items"][1]
        assert correction_event["before"]["observation"] == quote_correct
        assert correction_event["after"]["observation"] == corrected_observation
        assert correction_event["after"]["review_status"] == "corrected_pending_approval"

        # The review event is protected by the PostgreSQL trigger, not just ORM hooks.
        with Session() as db:
            event_id = db.execute(
                text(
                    "SELECT id FROM document_review_events "
                    "WHERE analysis_id = :analysis_id AND candidate_index = 1 AND decision_version = 2"
                ),
                {"analysis_id": analysis_id},
            ).scalar_one()
            with pytest.raises(DBAPIError):
                db.execute(
                    text("UPDATE document_review_events SET rationale = 'tampered' WHERE id = :event_id"),
                    {"event_id": event_id},
                )
            db.rollback()
            with pytest.raises(DBAPIError):
                db.execute(
                    text("DELETE FROM document_review_events WHERE id = :event_id"),
                    {"event_id": event_id},
                )
            db.rollback()

        # The append-only trigger permits deletion only inside the transaction-
        # local privacy-erasure boundary used after independent approval.
        with Session() as db:
            db.execute(text("SET LOCAL solenos.privacy_erasure = 'on'"))
            db.execute(
                text("DELETE FROM document_review_events WHERE id = :event_id"),
                {"event_id": event_id},
            )
            db.commit()
        with Session() as db:
            assert db.execute(
                text("SELECT COUNT(*) FROM document_review_events WHERE id = :event_id"),
                {"event_id": event_id},
            ).scalar_one() == 0

        # Another server-issued session cannot read the analysis, history, or source file.
        assert client.get(
            f"/api/v1/documents/analyses/{analysis_id}", headers=other_auth
        ).status_code == 404
        assert client.get(
            f"/api/v1/documents/intelligence/{analysis_id}/review-history", headers=other_auth
        ).status_code == 404
        assert client.get(f"/api/v1/upload/{evidence_id}", headers=other_auth).status_code == 404
        denied_review = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=other_auth,
            json={"candidate_index": 1, "decision": "accept"},
        )
        assert denied_review.status_code == 404

        # The original binary and its checksum remain unchanged after review.
        original_after = client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
        assert original_after.status_code == 200
        assert hashlib.sha256(original_after.content).hexdigest() == upload_receipt["sha256"]

        # Exercise the actual approved-erasure service against PostgreSQL. The
        # review-history trigger must allow deletion only inside this transaction.
        from app.api.privacy_requests import PrivacyDeletionFulfilment, fulfil_approved_deletion
        from app.models.privacy_request import DataSubjectRequest
        from app.models.care import Evidence
        from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent

        with Session() as db:
            deletion_request = DataSubjectRequest(
                requester_id=caregiver_id,
                actor_id=caregiver_id,
                subject_id=person_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                request_type="deletion",
                request_status="approved_pending_fulfilment_assessment",
                requested_at=datetime.now(timezone.utc),
                record_metadata={"review": {"reviewer_id": "independent-approver"}},
            )
            db.add(deletion_request)
            db.commit()
            deletion_request_id = deletion_request.id

        with Session() as db:
            deletion_result = fulfil_approved_deletion(
                deletion_request_id,
                PrivacyDeletionFulfilment(
                    backup_erasure_verified=True,
                    backup_retention_evidence="Verified that all backups containing this synthetic record are expired.",
                ),
                reviewer_id="independent-erasure-reviewer",
                db=db,
            )
            assert deletion_result["status"] == "fulfilled"

        with Session() as db:
            assert db.query(Evidence).filter_by(id=evidence_id).count() == 0
            assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 0
            assert db.query(CaptureAnalysis).filter_by(id=analysis_id).count() == 0
            assert db.query(DocumentReviewEvent).filter_by(analysis_id=analysis_id).count() == 0
            assert db.query(QwenBackgroundJob).filter_by(id=processing_job_id).count() == 0
            assert db.query(CareAssertion).filter_by(id=assertion_id).count() == 0
            receipt = db.query(DataSubjectRequest).filter_by(id=deletion_request_id).one()
            assert receipt.request_status == "fulfilled"
            assert receipt.person_id is None
            assert receipt.caregiver_id is None
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
