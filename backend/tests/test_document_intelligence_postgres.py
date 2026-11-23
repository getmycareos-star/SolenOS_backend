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
import re
import subprocess
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
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
import app.api.collaboration as collaboration_module
from app.api.collaboration import upload_evidence
from app.api.document_lifecycle import extract_saved_document, get_document_extraction
from app.api.document_intelligence import (
    CandidateReview,
    analyze_saved_document,
    list_document_intelligence,
    queue_document_intake,
    review_document_candidate,
    _existing_assertion_for_exact_document_duplicate,
)
from app.models.care import Caregiver, Person, Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care_continuity import CareAssertion, CareDeltaRun, CareStateRevision
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



def test_postgres_completed_and_cancelled_document_jobs_without_analysis_remain_visible_as_failed():
    """Terminal intake jobs without saved extraction must remain visible for retry."""
    from app.api.document_intelligence import list_document_intelligence

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-terminal-gap-person-{suffix}"
    caregiver_id = f"doc-terminal-gap-caregiver-{suffix}"
    completed_evidence_id = f"doc-terminal-completed-evidence-{suffix}"
    cancelled_evidence_id = f"doc-terminal-cancelled-evidence-{suffix}"
    completed_job_id = f"doc-terminal-completed-job-{suffix}"
    cancelled_job_id = f"doc-terminal-cancelled-job-{suffix}"
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Terminal document status fixture"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id, name="Document status caregiver",
                relationship="family", person_id=person_id,
            ))
            db.flush()
            db.add(CaregiverAccessGrant(
                person_id=person_id, caregiver_id=caregiver_id,
                role="primary", access_scope="full_record", status="active",
                can_manage_members=True, granted_by_caregiver_id=caregiver_id,
            ))
            db.add_all([
                Evidence(
                    id=completed_evidence_id, person_id=person_id, type="document",
                    source_text="Original completed-job document remains preserved.",
                    uploaded_by_caregiver_id=caregiver_id,
                ),
                Evidence(
                    id=cancelled_evidence_id, person_id=person_id, type="document",
                    source_text="Original cancelled-job document remains preserved.",
                    uploaded_by_caregiver_id=caregiver_id,
                ),
                QwenBackgroundJob(
                    id=completed_job_id, person_id=person_id, caregiver_id=caregiver_id,
                    task_text="Process saved document for caregiver review.",
                    context_text="", evidence_ids_json=json.dumps([completed_evidence_id]),
                    workflow="document_intake", status="completed",
                ),
                QwenBackgroundJob(
                    id=cancelled_job_id, person_id=person_id, caregiver_id=caregiver_id,
                    task_text="Process saved document for caregiver review.",
                    context_text="", evidence_ids_json=json.dumps([cancelled_evidence_id]),
                    workflow="document_intake", status="cancelled",
                ),
            ])
            db.commit()

        with Session() as db:
            state = list_document_intelligence(_request(person_id, caregiver_id), 50, db)
            by_evidence = {item["evidence_id"]: item for item in state["processing_jobs"]}
            assert by_evidence[completed_evidence_id] == {
                "evidence_id": completed_evidence_id,
                "job_id": completed_job_id,
                "status": "failed",
                "attempt_count": 0,
                "error_code": "completed_without_analysis",
            }
            assert by_evidence[cancelled_evidence_id] == {
                "evidence_id": cancelled_evidence_id,
                "job_id": cancelled_job_id,
                "status": "failed",
                "attempt_count": 0,
                "error_code": "cancelled",
            }
            assert not any(
                item.get("evidence_id") in {completed_evidence_id, cancelled_evidence_id}
                for item in state["items"]
            )
    finally:
        with Session() as db:
            db.query(QwenBackgroundJob).filter(
                QwenBackgroundJob.id.in_([completed_job_id, cancelled_job_id])
            ).delete(synchronize_session=False)
            db.query(Evidence).filter(
                Evidence.id.in_([completed_evidence_id, cancelled_evidence_id])
            ).delete(synchronize_session=False)
            db.query(CareStateRevision).filter_by(person_id=person_id).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()
        engine.dispose()


def test_postgres_completed_document_analysis_outside_visible_limit_is_not_marked_failed():
    """A saved extraction beyond the visible page limit is still a completed job."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-list-limit-person-{suffix}"
    caregiver_id = f"doc-list-limit-caregiver-{suffix}"
    visible_evidence_id = f"doc-list-limit-visible-{suffix}"
    hidden_evidence_id = f"doc-list-limit-hidden-{suffix}"
    job_id = f"doc-list-limit-job-{suffix}"
    now = datetime.now(timezone.utc)
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document list limit fixture"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id, name="Document list caregiver",
                relationship="family", person_id=person_id,
            ))
            db.flush()
            db.add(CaregiverAccessGrant(
                person_id=person_id, caregiver_id=caregiver_id,
                role="primary", access_scope="full_record", status="active",
                can_manage_members=True, granted_by_caregiver_id=caregiver_id,
            ))
            for evidence_id, text_value in (
                (visible_evidence_id, "Most recent saved document."),
                (hidden_evidence_id, "Earlier saved document."),
            ):
                db.add(Evidence(
                    id=evidence_id, person_id=person_id, type="document",
                    source_text=text_value, uploaded_by_caregiver_id=caregiver_id,
                ))
            db.flush()
            db.add(CaptureAnalysis(
                person_id=person_id, caregiver_id=caregiver_id,
                source_evidence_id=visible_evidence_id,
                source_text="Most recent saved document.",
                extraction_status="available",
                candidates={"kind": "document_extraction_v1", "filename": "new.txt"},
                created_at=now,
            ))
            db.add(CaptureAnalysis(
                person_id=person_id, caregiver_id=caregiver_id,
                source_evidence_id=hidden_evidence_id,
                source_text="Earlier saved document.",
                extraction_status="available",
                candidates={"kind": "document_extraction_v1", "filename": "old.txt"},
                created_at=now - timedelta(days=1),
            ))
            db.add(QwenBackgroundJob(
                id=job_id, person_id=person_id, caregiver_id=caregiver_id,
                task_text="Process saved document for caregiver review.",
                context_text="", evidence_ids_json=json.dumps([hidden_evidence_id]),
                workflow="document_intake", status="completed",
            ))
            db.commit()

        with Session() as db:
            state = list_document_intelligence(_request(person_id, caregiver_id), 1, db)
            assert len(state["items"]) == 1
            assert state["items"][0]["source_evidence_id"] == visible_evidence_id
            assert state["processing_jobs"] == []
    finally:
        with Session() as db:
            db.query(QwenBackgroundJob).filter_by(id=job_id).delete(synchronize_session=False)
            db.query(CaptureAnalysis).filter_by(person_id=person_id).delete(synchronize_session=False)
            db.query(Evidence).filter(Evidence.id.in_([visible_evidence_id, hidden_evidence_id])).delete(synchronize_session=False)
            db.query(CareStateRevision).filter_by(person_id=person_id).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()
        engine.dispose()


def _restart_postgres_service_container(engine):
    """Restart the disposable CI PostgreSQL service and prove committed rows survive."""
    if os.getenv("SOLENOS_TEST_POSTGRES_RESTART") != "1":
        return
    containers = subprocess.run(
        ["docker", "ps", "--filter", "ancestor=postgres:18", "--format", "{{.ID}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    containers = [value.strip() for value in containers if value.strip()]
    assert len(containers) == 1, f"Expected one PostgreSQL 18 service container, found {len(containers)}"
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
        b"The patient reports eating half of lunch on 2026-11-10 during the observation period."
    )
    source = original.decode("utf-8")
    quote_one = "Patient walked 20 metres with a frame on 12 October."
    quote_two = "The patient reports eating half of lunch on 2026-11-10 during the observation period."

    def deterministic_candidates(text, *, source_event_id, source_evidence_id=None):
        # The durable worker processes bounded page/chunk windows. Each call
        # must return only verbatim candidates present in that exact chunk;
        # requiring every quote in every chunk incorrectly fails valid splits.
        observations = []
        for quote in (quote_one, quote_two):
            if quote in text:
                observations.append({
                    "subject": "Patient",
                    "observation": quote,
                    "source_quote": quote,
                    "event_date": "2026-11-10" if quote == quote_two else None,
                    "extraction_method": "test_verbatim",
                })
        return {
            "observations": observations,
            "open_questions": ["Was this usual for the person?"] if observations else [],
        }

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

        # A worker can report completed even when its extraction/analysis write
        # did not survive. That must not disappear from the caregiver's document
        # queue or be presented as "waiting"; the original remains retryable.
        with Session() as db:
            processing_job = db.query(QwenBackgroundJob).filter_by(id=processing_job_id).one()
            processing_job.status = "completed"
            db.commit()
        with Session() as db:
            incomplete_state = list_document_intelligence(
                _request(person_id, caregiver_id), 50, db
            )
            incomplete_job = next(
                item for item in incomplete_state["processing_jobs"]
                if item["evidence_id"] == evidence_id
            )
            assert incomplete_job["status"] == "failed"
            assert incomplete_job["error_code"] == "completed_without_analysis"
            retried_incomplete_job = queue_document_intake(
                evidence_id, _request(person_id, caregiver_id), db
            )
            assert retried_incomplete_job["job_id"] == processing_job_id
            assert retried_incomplete_job["status"] == "queued"
            assert retried_incomplete_job["idempotent_replay"] is False

        # Execute the real durable worker against PostgreSQL. Only the candidate
        # model output is deterministic; upload, extraction, persistence and job
        # lifecycle all use the real database and service implementations.
        assert asyncio.run(worker.process_one_qwen_job(processing_job_id)) is True
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

        # A generic extraction suggestion is not automatically caregiver work.
        # Only source-grounded wording is retained as a review-only question;
        # no caregiver task is created without an unambiguous, evidence-backed
        # LCR gap that establishes decision necessity.
        with Session() as db:
            assert db.query(OpenQuestion).filter_by(
                person_id=person_id,
                status="open",
                question="Was this usual for the person?",
            ).count() == 0

        # Replaying document understanding must not promote that same generic
        # suggestion into an open question.
        with Session() as db:
            replayed = analyze_saved_document(evidence_id, _request(person_id, caregiver_id), db)
            assert replayed["analysis_id"] == analysis_id
        with Session() as db:
            assert db.query(OpenQuestion).filter_by(
                person_id=person_id,
                status="open",
                question="Was this usual for the person?",
            ).count() == 0

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

        # Reject a stale UI decision before it can create an LCR assertion.
        # The candidate index still exists, but its expected source quotation
        # does not match the persisted suggestion currently at that position.
        candidate_for_review = understood["observations"][1]
        with Session() as db:
            with pytest.raises(HTTPException) as stale_decision:
                review_document_candidate(
                    analysis_id,
                    CandidateReview(
                        candidate_index=1,
                        decision="accept",
                        expected_source_quote="A stale source quotation from an earlier render.",
                        expected_subject=candidate_for_review["subject"],
                    ),
                    _request(person_id, caregiver_id),
                    db,
                )
            assert stale_decision.value.status_code == 409
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

        # Reject one candidate (no assertion), accept the other (one attributed assertion).
        candidate_to_reject = understood["observations"][0]
        with Session() as db:
            rejected = review_document_candidate(
                analysis_id,
                CandidateReview(
                    candidate_index=0,
                    decision="reject",
                    expected_source_quote=candidate_to_reject["source_quote"],
                    expected_subject=candidate_to_reject["subject"],
                ),
                _request(person_id, caregiver_id),
                db,
            )
            assert rejected["written_to_lcr"] is False
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0
        with Session() as db:
            accepted = review_document_candidate(
                analysis_id,
                CandidateReview(
                    candidate_index=1,
                    decision="accept",
                    expected_source_quote=candidate_for_review["source_quote"],
                    expected_subject=candidate_for_review["subject"],
                ),
                _request(person_id, caregiver_id),
                db,
            )
            assert accepted["written_to_lcr"] is True
            assert accepted["longitudinal_retrieval_status"] == "verified"
            assert accepted["delta_reconciliation_status"] == "completed"
            delta_run_id = accepted["delta_run_id"]
            assert delta_run_id
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
            delta_run = db.query(CareDeltaRun).filter_by(
                id=delta_run_id, person_id=person_id
            ).one()
            delta_payload = json.loads(delta_run.delta_json)
            assert delta_run.source == "SolenOS Document Intelligence"
            assert delta_run.source_event_id is None
            assert delta_run.evidence_ids == [evidence_id]
            assert delta_payload["provenance"]["evidence_ids"] == [evidence_id]
            assert delta_payload["provenance"]["persisted"] is True
            assert assertion.assertion_status == "accepted"
            assert assertion.assertion_kind == "time_bound_observation"
            assert assertion.observed_at == datetime(2026, 11, 10, tzinfo=timezone.utc)
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quote_two
            assert assertion.created_by_caregiver_id == caregiver_id
            assert assertion.source_context["kind"] == "caregiver_reviewed_document_candidate"
            assert assertion.source_context["authority"] == "caregiver_reviewed_report_not_independently_verified"
            assert assertion.source_context["document_candidate"]["source_attribution"] == "patient_reported"
            assert assertion.source_context["document_candidate"]["page_number"] == 1
            assert assertion.source_context["document_candidate"]["event_date_precision"] == "day"
            assert "source_excerpt" not in assertion.source_context["document_candidate"]
            assertion_revisions = db.query(CareStateRevision).filter_by(
                person_id=person_id,
                target_type="care_assertions",
                target_id=assertion_id,
            ).all()
            assert len(assertion_revisions) == 1, "document qualifiers must be present in the initial assertion audit snapshot"
            assert json.loads(assertion_revisions[0].after_state)["source_context"]["document_candidate"]["source_attribution"] == "patient_reported"
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 1
            lcr_context = build_lcr_context(db, person_id, limit_per_source=50)
            assertion_source = lcr_context["sources"]["assertions"]
            assert assertion_source["status"] == "available"
            lcr_assertion = next(row for row in assertion_source["items"] if row["id"] == assertion_id)
            assert lcr_assertion["source_context"]["document_candidate"]["source_attribution"] == "patient_reported"

        # The canonical LCR read API must reconstruct the accepted assertion.
        lcr_rows = api_client.get(
            f"/api/v1/care-continuity/assertions/{person_id}",
            headers={"Authorization": "Bearer " + token},
        )
        assert lcr_rows.status_code == 200, lcr_rows.text
        api_assertion = next(row for row in lcr_rows.json() if row["id"] == assertion_id)
        assert api_assertion["assertion_status"] == "accepted"
        assert api_assertion["source_context"]["document_candidate"]["source_attribution"] == "patient_reported"
        current_projection = api_client.get(
            "/api/v1/care-continuity/current-care?limit_per_source=50",
            headers={"Authorization": "Bearer " + token},
        )
        assert current_projection.status_code == 200, current_projection.text
        current_document_assertion = next(
            item for item in current_projection.json()["items"]
            if item["source_collection"] == "assertions" and item["id"] == assertion_id
        )
        assert current_document_assertion["source_context"]["document_candidate"]["source_attribution"] == "patient_reported"

        # Companion must retrieve the accepted document assertion through the
        # authoritative LCR context. Force inference unavailable so this proves
        # the exact-source fallback, not a model-generated guess.
        monkeypatch.setattr(
            collaboration_module,
            "_ollama_url",
            lambda: ("http://ollama.test", "qwen-test"),
        )
        def unavailable_companion_model(*args, **kwargs):
            raise ConnectionError("PostgreSQL document Companion test has no live model")
        monkeypatch.setattr(
            collaboration_module,
            "_ollama_chat",
            unavailable_companion_model,
        )
        document_answer = api_client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": "Bearer " + token},
            json={
                "person_id": person_id,
                "text": "What does the uploaded document say about eating lunch?",
                "client_turn_id": "document-lcr-companion-readback-0001",
            },
        )
        assert document_answer.status_code == 200, document_answer.text
        document_answer_body = document_answer.json()
        assert quote_two in document_answer_body["reply"]
        assert document_answer_body["ai_generated"] is False
        assert document_answer_body["written_to_lcr"] is False
        assert evidence_id in document_answer_body["source_evidence_ids"]

        missing_document_answer = api_client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": "Bearer " + token},
            json={
                "person_id": person_id,
                "text": "What does the uploaded document say about medication dosage?",
                "client_turn_id": "document-lcr-companion-missing-0001",
            },
        )
        assert missing_document_answer.status_code == 200, missing_document_answer.text
        missing_answer_body = missing_document_answer.json()
        assert "does not clearly answer that question" in missing_answer_body["reply"]
        assert missing_answer_body["ai_generated"] is False
        assert missing_answer_body["written_to_lcr"] is False

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
                    analysis_id, CandidateReview(candidate_index=0, decision="accept", expected_source_quote="", expected_subject=""),
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
        processing_job_id = upload_receipt["document_processing_job_id"]
        assert processing_job_id, "document upload must return its durable processing job ID"
        with Session() as db:
            processing_job = db.query(QwenBackgroundJob).filter_by(
                person_id=person_id,
                caregiver_id=caregiver_id,
                workflow="document_intake",
                evidence_ids_json=json.dumps([evidence_id]),
            ).one()
            processing_job_id = processing_job.id
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

        # Rejecting a candidate and attempting to change it beyond the source quote
        # must not create an LCR assertion. New caregiver information belongs in
        # a separate caregiver-authored update, not a document-derived correction.
        rejected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={"candidate_index": 0, "decision": "reject", "rationale": "Not relevant to current care."},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["written_to_lcr"] is False

        unsupported_correction = "Caregiver correction: approximately half of lunch was eaten."
        corrected = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": "Person",
                "corrected_observation": unsupported_correction,
                "rationale": "Clarified the wording for the care record.",
            },
        )
        assert corrected.status_code == 409, corrected.text
        assert "preserve the complete source quotation" in corrected.json()["detail"]
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

        caregiver_label = "Meal intake"
        assert caregiver_label.casefold() not in quote_correct.casefold()
        corrected_valid = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "correct",
                "corrected_subject": caregiver_label,
                "corrected_observation": quote_correct,
                "rationale": "Used a clear caregiver label while keeping the document wording unchanged.",
            },
        )
        assert corrected_valid.status_code == 200, corrected_valid.text
        assert corrected_valid.json()["written_to_lcr"] is False
        assert corrected_valid.json()["candidate"]["review_status"] == "corrected_pending_approval"
        assert corrected_valid.json()["candidate"]["subject"] == caregiver_label
        assert corrected_valid.json()["candidate"]["observation"] == quote_correct
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

        # A caregiver decision from an older rendered candidate must not apply
        # after the candidate subject has been corrected in another review.
        stale_snapshot = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "accept",
                "expected_source_quote": quote_correct,
                "expected_subject": "Person",
            },
        )
        assert stale_snapshot.status_code == 409, stale_snapshot.text
        assert "suggestion changed after it was displayed" in stale_snapshot.json()["detail"]
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

        current_candidate_id = corrected_valid.json()["candidate"]["canonical_candidate"]["candidate_id"]
        stale_candidate_identity = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "accept",
                "expected_source_quote": quote_correct,
                "expected_subject": caregiver_label,
                "expected_candidate_id": "candidate-from-an-older-document-analysis",
            },
        )
        assert stale_candidate_identity.status_code == 409, stale_candidate_identity.text
        assert "suggestion changed after it was displayed" in stale_candidate_identity.json()["detail"]
        with Session() as db:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

        # Accepting the exact current statement creates the sole source-linked assertion.
        accepted = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "accept",
                "expected_source_quote": quote_correct,
                "expected_subject": caregiver_label,
                "expected_candidate_id": current_candidate_id,
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True
        assert accepted.json()["longitudinal_retrieval_status"] == "verified"
        execution = accepted.json()["care_continuity_execution"]
        assert execution["workflow"] == "document_candidate_review"
        assert execution["source_evidence_id"] == evidence_id
        assertion_id = accepted.json()["source_record_ref"]["record_id"]
        assert execution["source_assertion_id"] == assertion_id
        assert execution["stages"]["reconciliation"] == "committed"
        assert execution["transaction_owner"] == "caller"
        assert accepted.json()["change_intelligence_status"] == "available"

        accepted_retry = client.post(
            f"/api/v1/documents/intelligence/{analysis_id}/review",
            headers=auth,
            json={
                "candidate_index": 1,
                "decision": "accept",
                "expected_source_quote": quote_correct,
                "expected_subject": caregiver_label,
                "expected_candidate_id": current_candidate_id,
            },
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
            assert assertion.asserted_value.startswith(
                f"Subject: Meal intake\nStatement: {quote_correct}\n"
                "Event date: Not stated or unresolved\n"
                "Source-recorded date: Not stated in document"
            )
            assert "Caregiver correction:" not in assertion.asserted_value
            assert "Caregiver-corrected wording" not in assertion.asserted_value
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
        # Rejection, correction and acceptance are three distinct caregiver
        # decisions. The correction must remain in the append-only history.
        assert history.json()["count"] == 3
        history_items = history.json()["items"]
        assert [item["decision"] for item in history_items] == ["reject", "correct", "accept"]
        correction_event = history_items[1]
        assert correction_event["after"]["subject"] == caregiver_label
        assert correction_event["after"]["review_status"] == "corrected_pending_approval"
        accepted_event = history_items[2]
        assert accepted_event["before"]["observation"] == quote_correct
        assert accepted_event["after"]["observation"] == quote_correct
        assert accepted_event["after"]["review_status"] == "accepted_by_caregiver"

        # The review event is protected by the PostgreSQL trigger, not just ORM hooks.
        with Session() as db:
            event_id = db.execute(
                text(
                    "SELECT id FROM document_review_events "
                    "WHERE analysis_id = :analysis_id AND candidate_index = 1 AND decision_version = 1"
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


def test_postgres_document_intelligence_resumes_all_page_windows_and_preserves_provenance(monkeypatch):
    from app.api import document_intelligence
    from app.models.capture_analysis import CaptureAnalysis
    from app.services import qwen_background_worker as worker

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
    monkeypatch.setattr(worker, "SessionLocal", Session)

    async def idle_document_worker():
        await asyncio.Event().wait()

    monkeypatch.setattr(main_module, "qwen_worker_loop", idle_document_worker)
    monkeypatch.setattr(
        document_intelligence,
        "get_ollama_config",
        lambda: (_ for _ in ()).throw(HTTPException(status_code=503, detail="test model unavailable")),
    )
    seen_pages = []

    def deterministic_page_candidates(page_text, *, source_event_id, source_evidence_id=None):
        match = re.search(r"Patient report: page (\d+) observation recorded\.", page_text)
        assert match, page_text
        page_number = int(match.group(1))
        seen_pages.append(page_number)
        quote = f"Patient report: page {page_number} observation recorded."
        return {
            "observations": [{
                "subject": "Patient",
                "observation": quote,
                "source_quote": quote,
                "extraction_method": "postgresql_page_window_test",
            }],
            "open_questions": [],
        }

    monkeypatch.setattr(document_intelligence, "extract_review_candidates", deterministic_page_candidates)
    api_client = TestClient(app)
    try:
        identity_response = api_client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "PostgreSQL multi-page document recipient"},
        )
        assert identity_response.status_code == 201, identity_response.text
        identity = identity_response.json()
        headers = {"Authorization": "Bearer " + identity["access_token"]}
        content = "\n\n".join(
            f"[Page {page_number}]\nPatient report: page {page_number} observation recorded."
            for page_number in range(1, 27)
        ).encode("utf-8")
        uploaded = api_client.post(
            "/api/v1/upload",
            headers=headers,
            data={
                "person_id": identity["person"]["id"],
                "caregiver_id": identity["caregiver"]["id"],
                "evidence_type": "document",
            },
            files={"file": ("twenty-six-page-care-record.txt", content, "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]

        extraction = api_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=headers)
        assert extraction.status_code == 201, extraction.text
        analysis_id = extraction.json()["analysis_id"]

        first = api_client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=headers)
        assert first.status_code == 201, first.text
        first_body = first.json()
        assert first_body["intelligence_status"] == "partial"
        assert first_body["processed_page_count"] == 25
        assert first_body["total_page_count"] == 26
        assert first_body["unprocessed_page_numbers"] == [26]
        assert first_body["model_provenance"]["source_evidence_id"] == evidence_id
        assert first_body["model_provenance"]["model_authority"] == "review_only"
        assert first_body["model_provenance"]["provider"] == "unavailable_or_test_stub"
        assert first_body["model_provenance"]["source_text_sha256"] == hashlib.sha256(content.decode("utf-8").encode("utf-8")).hexdigest()

        second = api_client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=headers)
        assert second.status_code == 201, second.text
        second_body = second.json()
        assert second_body["intelligence_status"] == "available"
        assert second_body["processed_page_count"] == 26
        assert second_body["total_page_count"] == 26
        assert second_body["unprocessed_page_numbers"] == []
        assert sorted(set(seen_pages)) == list(range(1, 27))
        assert len(second_body["observations"]) == 26
        assert len(second_body["processing_history"]) == 2
        assert second_body["processing_history"][-1] == second_body["model_provenance"]
        assert second_body["model_provenance"]["source_evidence_id"] == evidence_id
        assert second_body["model_provenance"]["model_authority"] == "review_only"

        with Session() as db:
            saved = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
            intelligence = (saved.candidates or {}).get("document_intelligence") or {}
            assert intelligence["status"] == "available"
            assert len(intelligence["processed_page_numbers"]) == 26
            assert intelligence["unprocessed_page_numbers"] == []
            assert len(intelligence["processing_history"]) == 2
    finally:
        api_client.close()
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_postgres_concurrent_duplicate_document_reviews_create_one_assertion():
    """Cross-analysis duplicate reviews serialize on the document-source advisory lock."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Concurrent duplicate review evaluator"},
        )
        assert created.status_code == 201, created.text
        identity = created.json()
        person_id = identity["person"]["id"]
        caregiver_id = identity["caregiver"]["id"]
        original = b"[Page 1]\\nPatient walked 20 metres with a frame."
        digest = hashlib.sha256(original).hexdigest()
        evidence_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        with Session() as db:
            for index, evidence_id in enumerate(evidence_ids):
                db.add(Evidence(
                    id=evidence_id,
                    person_id=person_id,
                    type="document",
                    source_text="",
                    uploaded_by_caregiver_id=caregiver_id,
                    extra_metadata=json.dumps({
                        "filename": f"duplicate-{index}.txt",
                        "duplicate_of_evidence_id": evidence_ids[0] if index else None,
                    }),
                    source_type="caregiver_upload",
                    evidence_status="recorded",
                ))
                db.add(EvidenceUpload(
                    evidence_id=evidence_id,
                    filename=f"duplicate-{index}.txt",
                    mime_type="text/plain",
                    content=original,
                    size_bytes=len(original),
                    sha256=digest,
                ))
            db.commit()

        barrier = threading.Barrier(2)
        asserted_value = (
            "Subject: mobility\\n"
            "Statement: Patient walked 20 metres with a frame.\\n"
            "Event date: Not stated or unresolved\\n"
            "Source-recorded date: Not stated in document"
        )

        def accept_same_source(evidence_id):
            with Session() as db:
                upload = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
                barrier.wait(timeout=15)
                existing = _existing_assertion_for_exact_document_duplicate(
                    db,
                    person_id=person_id,
                    caregiver_id=caregiver_id,
                    evidence_id=evidence_id,
                    upload=upload,
                    field="observation",
                    source_quote="Patient walked 20 metres with a frame.",
                    asserted_value=asserted_value,
                    subject_type="person",
                    subject_id=person_id,
                    assertion_kind="document_reviewed_observation",
                )
                if existing is None:
                    existing = CareAssertion(
                        person_id=person_id,
                        subject_type="person",
                        subject_id=person_id,
                        field="observation",
                        assertion_kind="document_reviewed_observation",
                        asserted_value=asserted_value,
                        source_label="Caregiver-reviewed document",
                        source_text="Patient walked 20 metres with a frame.",
                        evidence_ids=[evidence_id],
                        created_by_caregiver_id=caregiver_id,
                        updated_by_caregiver_id=caregiver_id,
                    )
                    db.add(existing)
                    db.flush()
                assertion_id = existing.id
                db.commit()
                return assertion_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            assertion_ids = list(pool.map(accept_same_source, evidence_ids))
        assert assertion_ids[0] == assertion_ids[1]
        with Session() as db:
            rows = db.query(CareAssertion).filter(
                CareAssertion.person_id == person_id,
                CareAssertion.field == "observation",
                CareAssertion.source_text == "Patient walked 20 metres with a frame.",
            ).all()
            assert len(rows) == 1
            assert rows[0].id == assertion_ids[0]
    finally:
        client.close()
        engine.dispose()
