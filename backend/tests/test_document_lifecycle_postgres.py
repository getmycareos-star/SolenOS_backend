"""Authenticated PostgreSQL document upload, extraction, retry and isolation benchmark.

All fixture records are created only in the disposable PostgreSQL CI database.
"""
import hashlib
import json
import math
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires real PostgreSQL DATABASE_URL",
)


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(p: float) -> float:
        position = (len(ordered) - 1) * p
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)

    return {"p50": percentile(0.50), "p95": percentile(0.95), "p99": percentile(0.99)}


def test_postgres_document_upload_extraction_retry_persistence_and_person_isolation():
    """Exercise authenticated HTTP endpoints and measure real PostgreSQL request time."""
    suffix = uuid.uuid4().hex
    sample_count = 20
    evidence_ids: list[str] = []
    analysis_ids: list[str] = []
    originals: list[bytes] = []
    upload_ms: list[float] = []
    extraction_ms: list[float] = []
    analysis_read_ms: list[float] = []
    original_download_ms: list[float] = []

    with TestClient(app) as client:
        owner = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "PostgreSQL document fixture A"})
        other = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "PostgreSQL document fixture B"})
        assert owner.status_code == 201, owner.text
        assert other.status_code == 201, other.text
        headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
        other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
        person_id = owner.json()["person"]["id"]
        caregiver_id = owner.json()["caregiver"]["id"]

        # These are test-only fixture documents in the disposable CI database.
        # Each iteration exercises the actual authenticated multipart upload and
        # persisted extraction HTTP routes; no Qwen inference or LCR assertion occurs.
        for index in range(sample_count):
            original = f"Care note {index}: breakfast was mostly uneaten. Follow-up was requested. Run {suffix}.".encode("utf-8")
            originals.append(original)
            digest = hashlib.sha256(original).hexdigest()

            upload_started = time.perf_counter()
            uploaded = client.post(
                "/api/v1/upload",
                headers=headers,
                data={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "evidence_type": "document",
                },
                files={"file": (f"care-note-{index}.txt", original, "text/plain")},
            )
            upload_ms.append((time.perf_counter() - upload_started) * 1000)
            assert uploaded.status_code == 201, uploaded.text
            upload_receipt = uploaded.json()
            assert upload_receipt["persisted"] is True
            assert upload_receipt["size_bytes"] == len(original)
            assert upload_receipt["sha256"] == digest
            evidence_ids.append(upload_receipt["evidence_id"])

            extraction_started = time.perf_counter()
            extracted = client.post(
                f"/api/v1/documents/{evidence_ids[-1]}/extract",
                headers=headers,
                json={},
            )
            extraction_ms.append((time.perf_counter() - extraction_started) * 1000)
            assert extracted.status_code == 201, extracted.text
            extraction_receipt = extracted.json()
            assert extraction_receipt["source_evidence_id"] == evidence_ids[-1]
            assert extraction_receipt["extraction_method"] == "server_utf8_text"
            assert extraction_receipt["written_to_lcr"] is False
            analysis_ids.append(extraction_receipt["analysis_id"])

        # Retry through HTTP in a fresh request; it must return the same saved analysis.
        retry_started = time.perf_counter()
        retry = client.post(
            f"/api/v1/documents/{evidence_ids[0]}/extract",
            headers=headers,
            json={},
        )
        retry_ms = round((time.perf_counter() - retry_started) * 1000, 3)
        assert retry.status_code == 201, retry.text
        assert retry.json()["analysis_id"] == analysis_ids[0]

        # Read the persisted extraction and original bytes through their authenticated APIs.
        for _ in range(sample_count):
            read_started = time.perf_counter()
            saved = client.get(f"/api/v1/documents/analyses/{analysis_ids[0]}", headers=headers)
            analysis_read_ms.append((time.perf_counter() - read_started) * 1000)
            assert saved.status_code == 200, saved.text
            assert saved.json()["extracted_text"] == originals[0].decode("utf-8")

            download_started = time.perf_counter()
            original_file = client.get(f"/api/v1/upload/{evidence_ids[0]}", headers=headers)
            original_download_ms.append((time.perf_counter() - download_started) * 1000)
            assert original_file.status_code == 200, original_file.text
            assert hashlib.sha256(original_file.content).hexdigest() == hashlib.sha256(originals[0]).hexdigest()

        # A different server-issued session cannot extract or read the owner's document.
        denied_extract = client.post(
            f"/api/v1/documents/{evidence_ids[0]}/extract",
            headers=other_headers,
            json={},
        )
        assert denied_extract.status_code == 404, denied_extract.text
        denied_read = client.get(
            f"/api/v1/documents/analyses/{analysis_ids[0]}",
            headers=other_headers,
        )
        assert denied_read.status_code == 404, denied_read.text
        denied_original = client.get(f"/api/v1/upload/{evidence_ids[0]}", headers=other_headers)
        assert denied_original.status_code == 404, denied_original.text

    print(json.dumps({
        "event": "postgres_document_http_benchmark",
        "sample_count": sample_count,
        "upload_http_duration_ms": _percentiles(upload_ms),
        "extraction_http_duration_ms_text_only": _percentiles(extraction_ms),
        "analysis_read_http_duration_ms": _percentiles(analysis_read_ms),
        "original_download_http_duration_ms": _percentiles(original_download_ms),
        "idempotent_extraction_retry_ms": retry_ms,
        "scope": "disposable_postgresql_ci_only",
    }, separators=(",", ":")))


# Preserve the original direct-route PostgreSQL lifecycle assertions as well.
"""Focused real-PostgreSQL document extraction lifecycle integration test.

Requires the migrated disposable PostgreSQL database supplied by CI. This is
intentionally separate from the 10,000-journey longitudinal benchmark.
"""
import asyncio
import hashlib
import io
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request

from app.models.care import Caregiver, Evidence, Person
from app.models.care_continuity import CareAssertion
from app.models.collaboration import EvidenceUpload
from app.models.qwen_background_job import QwenBackgroundJob
from app.models.open_loop import OpenLoopRecord, OpenLoopEvent, OpenLoopOccurrence
from app.models.uncertainty import OpenQuestion
from app.api.collaboration import (
    upload_evidence,
    delete_uploaded_document,
    list_document_processing_status,
    link_corrected_document_version,
    compare_document_version,
    DocumentVersionLinkRequest,
)
from app.models.capture_analysis import CaptureAnalysis
from app.api.document_lifecycle import extract_saved_document, get_document_extraction
from app.api.document_intelligence import _existing_assertion_for_exact_document_duplicate
from app.api.document_intelligence import _persist_document_follow_up
from app.api.document_intelligence import _persist_medication_document_conflict_question

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


def test_postgres_document_upload_extraction_retry_persistence_and_person_isolation_direct_route_contract():
    """Persist an uploaded binary, extract it, retry, reload, and deny another person."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_a = f"doc-pg-person-a-{suffix}"
    person_b = f"doc-pg-person-b-{suffix}"
    caregiver_a = f"doc-pg-caregiver-a-{suffix}"
    caregiver_b = f"doc-pg-caregiver-b-{suffix}"
    evidence_id = f"doc-pg-evidence-{suffix}"
    original = b"Care note: breakfast was mostly uneaten. Follow-up was requested."
    digest = hashlib.sha256(original).hexdigest()

    try:
        with Session() as db:
            db.add_all([
                Person(id=person_a, name="PostgreSQL document fixture A"),
                Person(id=person_b, name="PostgreSQL document fixture B"),
            ])
            db.flush()
            db.add_all([
                Caregiver(id=caregiver_a, name="Document caregiver A", relationship="family", person_id=person_a),
                Caregiver(id=caregiver_b, name="Document caregiver B", relationship="family", person_id=person_b),
            ])
            db.flush()
            db.commit()

        # Exercise the actual multipart upload route against PostgreSQL. The
        # endpoint creates Evidence and persists the original EvidenceUpload bytes.
        with Session() as db:
            upload_request = _request(person_a, caregiver_a)
            uploaded = asyncio.run(upload_evidence(
                upload_request,
                UploadFile(
                    filename="care-note.txt",
                    file=io.BytesIO(original),
                    headers=Headers({"content-type": "text/plain"}),
                ),
                person_a,
                caregiver_a,
                "document",
                "",
                "not_provided",
                "0",
                "{}",
                "false",
                db,
            ))
            assert uploaded["persisted"] is True
            assert uploaded["size_bytes"] == len(original)
            assert uploaded["sha256"] == digest
            evidence_id = uploaded["evidence_id"]

        # Execute the production extraction route function against PostgreSQL.
        with Session() as db:
            first = extract_saved_document(
                evidence_id, _request(person_a, caregiver_a), db
            )
            assert first["source_evidence_id"] == evidence_id
            assert first["extraction_method"] == "server_utf8_text"
            assert first["written_to_lcr"] is False
            first_analysis_id = first["analysis_id"]

        # Retry in a distinct DB session: it must return the same persisted analysis.
        with Session() as db:
            retry = extract_saved_document(
                evidence_id, _request(person_a, caregiver_a), db
            )
            assert retry["analysis_id"] == first_analysis_id
            assert db.query(CaptureAnalysis).filter(
                CaptureAnalysis.person_id == person_a,
                CaptureAnalysis.caregiver_id == caregiver_a,
                CaptureAnalysis.source_evidence_id == evidence_id,
            ).count() == 1

        # Reload the extracted text through the read route in another DB session.
        with Session() as db:
            saved = get_document_extraction(
                first_analysis_id, _request(person_a, caregiver_a), db
            )
            assert saved["extracted_text"] == original.decode("utf-8")
            assert saved["document"]["binary_sha256"] == digest

        # A different person's identity must not extract or read this evidence.
        with Session() as db:
            with pytest.raises(HTTPException) as denied:
                extract_saved_document(
                    evidence_id, _request(person_b, caregiver_b), db
                )
            assert denied.value.status_code == 404
            with pytest.raises(HTTPException) as read_denied:
                get_document_extraction(
                    first_analysis_id, _request(person_b, caregiver_b), db
                )
            assert read_denied.value.status_code == 404
    finally:
        # CI database is disposable. Leave fixture rows intact if append-only
        # audit dependencies exist; unique IDs prevent collisions between runs.
        engine.dispose()


def _upload_document_for_lifecycle(db, person_id: str, caregiver_id: str, filename: str, content: bytes):
    return asyncio.run(upload_evidence(
        _request(person_id, caregiver_id),
        UploadFile(
            filename=filename,
            file=io.BytesIO(content),
            headers=Headers({"content-type": "text/plain"}),
        ),
        person_id,
        caregiver_id,
        "document",
        "",
        "not_provided",
        "0",
        "{}",
        "false",
        db,
    ))


def test_postgres_document_deletion_erases_original_and_uncommitted_extraction():
    """A caregiver deletion removes the binary and derived text, and stops retries."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-delete-person-{suffix}"
    caregiver_id = f"doc-delete-caregiver-{suffix}"
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document deletion fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Document deletion caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            original = b"Private unrelated document content. " + suffix.encode("ascii")
            uploaded = _upload_document_for_lifecycle(db, person_id, caregiver_id, "unrelated-private.txt", original)
            evidence_id = uploaded["evidence_id"]
            assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 1
            db.add(QwenBackgroundJob(
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Read the uploaded document for a caregiver question.",
                context_text="",
                evidence_ids_json=json.dumps([evidence_id]),
                workflow="living_care_record",
                status="queued",
            ))
            db.commit()

        with Session() as db:
            extraction = extract_saved_document(evidence_id, _request(person_id, caregiver_id), db)
            assert extraction["source_evidence_id"] == evidence_id
            assert db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).count() == 1

        with Session() as db:
            result = delete_uploaded_document(evidence_id, _request(person_id, caregiver_id), db)
            assert result["deleted"] is True
            assert result["original_file_removed"] is True
            assert result["derived_extraction_removed"] is True

        with Session() as db:
            evidence = db.query(Evidence).filter_by(id=evidence_id, person_id=person_id).one()
            assert evidence.evidence_status == "deleted_by_caregiver"
            assert "filename" not in json.loads(evidence.extra_metadata or "{}")
            assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 0
            assert db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).count() == 0
            cancelled_jobs = db.query(QwenBackgroundJob).filter(
                QwenBackgroundJob.person_id == person_id,
                QwenBackgroundJob.evidence_ids_json == json.dumps([evidence_id]),
            ).all()
            assert {job.workflow for job in cancelled_jobs} == {"document_intake", "document_lifecycle", "living_care_record"}
            processing_jobs = [job for job in cancelled_jobs if job.workflow != "document_lifecycle"]
            lifecycle_jobs = [job for job in cancelled_jobs if job.workflow == "document_lifecycle"]
            assert processing_jobs
            assert all(job.status == "cancelled" for job in processing_jobs)
            # The document_lifecycle row is an audit-event container, not a
            # processing job. It remains completed so the deletion receipt is
            # retained after the source bytes and derived extraction are erased.
            assert all(job.status == "completed" for job in lifecycle_jobs)
            from app.models.qwen_background_job import QwenJobEvent
            assert db.query(QwenJobEvent).filter(
                QwenJobEvent.job_id.in_([job.id for job in lifecycle_jobs]),
                QwenJobEvent.event_type == "document_deleted",
            ).count() == 1
            with pytest.raises(HTTPException) as denied:
                extract_saved_document(evidence_id, _request(person_id, caregiver_id), db)
            assert denied.value.status_code == 404
    finally:
        engine.dispose()


def test_postgres_document_deletion_refuses_to_orphan_lcr_assertions():
    """A source document cannot be erased while a live LCR assertion depends on it."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-dependent-person-{suffix}"
    caregiver_id = f"doc-dependent-caregiver-{suffix}"
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document dependency fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Document dependency caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            uploaded = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "source-document.txt",
                b"Source document supports a care assertion.",
            )
            evidence_id = uploaded["evidence_id"]
            db.add(CareAssertion(
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field="observation",
                assertion_kind="document_reviewed_observation",
                asserted_value="A source-grounded assertion.",
                source_label="Caregiver-reviewed document",
                source_text="Source document supports a care assertion.",
                evidence_ids=[evidence_id],
                assertion_status="accepted",
                created_by_caregiver_id=caregiver_id,
                updated_by_caregiver_id=caregiver_id,
            ))
            db.commit()

        with Session() as db:
            with pytest.raises(HTTPException) as blocked:
                delete_uploaded_document(evidence_id, _request(person_id, caregiver_id), db)
            assert blocked.value.status_code == 409
            assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 1
    finally:
        engine.dispose()


def test_postgres_corrected_document_version_link_is_explicit_and_non_destructive():
    """A corrected upload links to its predecessor without replacing either original."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-version-person-{suffix}"
    caregiver_id = f"doc-version-caregiver-{suffix}"
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document version fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Document version caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            previous = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "discharge-summary.txt",
                b"Original discharge date: Monday.",
            )
            replacement = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "discharge-summary-corrected.txt",
                b"Corrected discharge date: Tuesday.",
            )

        with Session() as db:
            linked = link_corrected_document_version(
                previous["evidence_id"],
                DocumentVersionLinkRequest(replacement_evidence_id=replacement["evidence_id"]),
                _request(person_id, caregiver_id),
                db,
            )
            assert linked["relationship"] == "corrected_version_of"
            assert linked["automatic_lcr_replacement"] is False

        with Session() as db:
            current = db.query(Evidence).filter_by(id=replacement["evidence_id"], person_id=person_id).one()
            predecessor = db.query(Evidence).filter_by(id=previous["evidence_id"], person_id=person_id).one()
            assert current.parent_evidence_id == predecessor.id
            assert db.query(EvidenceUpload).filter_by(evidence_id=current.id).count() == 1
            assert db.query(EvidenceUpload).filter_by(evidence_id=predecessor.id).count() == 1
            comparison = compare_document_version(current.id, _request(person_id, caregiver_id), db)
            assert comparison["comparison_status"] == "pending"
            assert comparison["lcr_changed"] is False
    finally:
        engine.dispose()


def test_postgres_exact_duplicate_documents_reuse_one_lcr_assertion():
    """Exact duplicate uploads cannot create a second assertion for the same source passage."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-duplicate-person-{suffix}"
    caregiver_id = f"doc-duplicate-caregiver-{suffix}"
    other_caregiver_id = f"doc-duplicate-other-caregiver-{suffix}"
    source_quote = "The follow-up clinic review is recommended."
    original_bytes = (source_quote + " " + suffix).encode("utf-8")
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document duplicate fixture"))
            db.flush()
            db.add_all([
                Caregiver(id=caregiver_id, name="Document duplicate caregiver", relationship="family", person_id=person_id),
                Caregiver(id=other_caregiver_id, name="Second document duplicate caregiver", relationship="family", person_id=person_id),
            ])
            db.commit()

        with Session() as db:
            first = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "discharge-summary.txt", original_bytes
            )
            second = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "discharge-summary-copy.txt", original_bytes
            )
            assert first["sha256"] == second["sha256"]
            assert second["duplicate_detected"] is True
            assert second["duplicate_of_evidence_id"] == first["evidence_id"]
            db.add(CareAssertion(
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field="appointment",
                assertion_kind="document_reviewed_observation",
                asserted_value="Subject: Follow-up clinic review\nStatement: " + source_quote + "\nEvent date: Not stated or unresolved\nSource-recorded date: Not stated in document",
                source_label="Caregiver-reviewed document",
                source_text=source_quote,
                evidence_ids=[first["evidence_id"]],
                assertion_status="accepted",
                created_by_caregiver_id=caregiver_id,
                updated_by_caregiver_id=caregiver_id,
            ))
            db.commit()

        with Session() as db:
            second_evidence = db.query(Evidence).filter_by(id=second["evidence_id"], person_id=person_id).one()
            second_upload = db.query(EvidenceUpload).filter_by(evidence_id=second["evidence_id"]).one()
            reused = _existing_assertion_for_exact_document_duplicate(
                db,
                person_id=person_id,
                caregiver_id=other_caregiver_id,
                evidence_id=second["evidence_id"],
                upload=second_upload,
                field="appointment",
                source_quote=source_quote,
                asserted_value=db.query(CareAssertion).filter_by(person_id=person_id).one().asserted_value,
            )
            assert reused is not None
            assert first["evidence_id"] in (reused.evidence_ids or [])
            assert second_evidence.id not in (reused.evidence_ids or [])
            with pytest.raises(HTTPException) as conflict:
                _existing_assertion_for_exact_document_duplicate(
                    db,
                    person_id=person_id,
                    caregiver_id=other_caregiver_id,
                    evidence_id=second["evidence_id"],
                    upload=second_upload,
                    field="appointment",
                    source_quote=source_quote,
                    asserted_value="A different interpretation of the same exact source.",
                )
            assert conflict.value.status_code == 409
    finally:
        engine.dispose()


def test_postgres_accepted_document_follow_up_is_persisted_once_and_not_booked():
    """A clear accepted recommendation becomes one existing Open Loop with source evidence."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-followup-person-{suffix}"
    caregiver_id = f"doc-followup-caregiver-{suffix}"
    quote = "The patient should have a follow-up clinic review within two weeks."
    raw = (quote + " " + suffix).encode("utf-8")
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document follow-up fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Document follow-up caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            first = _upload_document_for_lifecycle(db, person_id, caregiver_id, "discharge.pdf.txt", raw)
            duplicate = _upload_document_for_lifecycle(db, person_id, caregiver_id, "discharge-copy.pdf.txt", raw)
            first_upload = db.query(EvidenceUpload).filter_by(evidence_id=first["evidence_id"]).one()
            duplicate_upload = db.query(EvidenceUpload).filter_by(evidence_id=duplicate["evidence_id"]).one()
            assert first_upload.sha256 == duplicate_upload.sha256
            candidate = {
                "field_type": "appointment",
                "page_number": 6,
            }
            first_loop_id = _persist_document_follow_up(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=first["evidence_id"],
                analysis_id="analysis-" + suffix,
                upload=first_upload,
                candidate=candidate,
                subject="Clinic review",
                observation=quote,
                quote=quote,
            )
            duplicate_loop_id = _persist_document_follow_up(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=duplicate["evidence_id"],
                analysis_id="analysis-duplicate-" + suffix,
                upload=duplicate_upload,
                candidate=candidate,
                subject="Clinic review",
                observation=quote,
                quote=quote,
            )
            assert first_loop_id == duplicate_loop_id
            db.commit()

        with Session() as db:
            loop = db.query(OpenLoopRecord).filter_by(id=first_loop_id, person_id=person_id).one()
            assert loop.loop_type == "follow_up"
            assert loop.status == "open"
            assert loop.deadline_at is None
            assert "within two weeks" in loop.description
            assert "does not mean an appointment has been booked" in loop.description
            assert loop.evidence[0]["evidence_id"] == first["evidence_id"]
            assert db.query(OpenLoopOccurrence).filter_by(person_id=person_id, source_id=loop.source_id).count() == 1
            assert db.query(OpenLoopEvent).filter_by(person_id=person_id, loop_id=loop.id).count() == 1
            with pytest.raises(HTTPException) as blocked:
                delete_uploaded_document(first["evidence_id"], _request(person_id, caregiver_id), db)
            assert blocked.value.status_code == 409
    finally:
        engine.dispose()


def test_postgres_document_status_reports_queued_upload_without_claiming_processing_success():
    """A persisted upload with queued work is not represented as processed."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-status-person-{suffix}"
    caregiver_id = f"doc-status-caregiver-{suffix}"
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document status fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Document status caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            uploaded = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "hospital-discharge.txt",
                b"Discharge instructions are preserved in the original file.",
            )
            assert uploaded["persisted"] is True

        with Session() as db:
            status = list_document_processing_status(_request(person_id, caregiver_id), 10, db)
            assert status["count"] == 1
            item = status["items"][0]
            assert item["evidence_id"] == uploaded["evidence_id"]
            assert item["processing_status"] == "queued"
            assert item["written_to_lcr"] is False
            assert item["review_required"] is False
            assert item["document_name"] == "hospital-discharge.txt"
    finally:
        engine.dispose()


def test_postgres_document_medication_conflict_creates_professional_question_without_changing_medication():
    """A source-grounded discontinuation candidate creates a question, not a medication change."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-med-conflict-person-{suffix}"
    caregiver_id = f"doc-med-conflict-caregiver-{suffix}"
    medication_id = f"medication-{suffix}"
    quote = "Aspirin was discontinued at hospital discharge."
    raw = (quote + " " + suffix).encode("utf-8")
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Medication conflict fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Medication conflict caregiver", relationship="family", person_id=person_id))
            db.add(CareAssertion(
                person_id=person_id,
                subject_type="medication",
                subject_id=medication_id,
                field="name",
                assertion_kind="current_state",
                asserted_value="Aspirin",
                source_label="Previously reviewed medication record",
                source_text="Caregiver-reviewed medication name.",
                evidence_ids=[],
                assertion_status="accepted",
                epistemic_state="reported",
                review_after_days=30,
                created_by_caregiver_id=caregiver_id,
            ))
            active_status = CareAssertion(
                person_id=person_id,
                subject_type="medication",
                subject_id=medication_id,
                field="medication_status",
                assertion_kind="current_state",
                asserted_value="active",
                source_label="Previously reviewed medication record",
                source_text="Caregiver-reviewed active medication status.",
                evidence_ids=[],
                assertion_status="accepted",
                epistemic_state="reported",
                review_after_days=30,
                created_by_caregiver_id=caregiver_id,
            )
            db.add(active_status)
            db.commit()
            active_status_id = active_status.id

        from app.api.care_continuity import current_medication_records
        with Session() as db:
            projection = current_medication_records(_request(person_id, caregiver_id), db)
            assert any(item.get("medication_id") == medication_id for item in projection.get("medications", []))

        with Session() as db:
            uploaded = _upload_document_for_lifecycle(db, person_id, caregiver_id, "discharge-summary.txt", raw)
            duplicate = _upload_document_for_lifecycle(db, person_id, caregiver_id, "discharge-summary-copy.txt", raw)
            first_upload = db.query(EvidenceUpload).filter_by(evidence_id=uploaded["evidence_id"]).one()
            duplicate_upload = db.query(EvidenceUpload).filter_by(evidence_id=duplicate["evidence_id"]).one()
            candidate = {
                "field_type": "medication",
                "subject": "Aspirin",
                "observation": quote,
                "source_quote": quote,
                "page_number": 4,
            }
            active_medication = {
                "medication_id": medication_id,
                "name": "Aspirin",
                "dose": "81 mg",
                "evidence_ids": [],
                "review_after_days": 30,
            }
            first_question_id = _persist_medication_document_conflict_question(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=uploaded["evidence_id"],
                analysis_id="analysis-" + suffix,
                upload=first_upload,
                candidate=candidate,
                active_medication=active_medication,
            )
            duplicate_question_id = _persist_medication_document_conflict_question(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=duplicate["evidence_id"],
                analysis_id="analysis-copy-" + suffix,
                upload=duplicate_upload,
                candidate=candidate,
                active_medication=active_medication,
            )
            assert first_question_id == duplicate_question_id
            db.commit()

        with Session() as db:
            question = db.query(OpenQuestion).filter_by(id=first_question_id, person_id=person_id).one()
            assert question.subject_type == "medication"
            assert question.subject_id == medication_id
            assert question.claim_id == active_status_id
            assert question.intended_answerer_role == "professional"
            assert question.necessity == "necessary"
            assert question.status == "open"
            assert question.priority.value == "high"
            from app.services.care_continuity import has_valid_caregiver_necessity
            assert has_valid_caregiver_necessity(question, db, person_id) is True
            assert uploaded["evidence_id"] in question.evidence_ids
            status = list_document_processing_status(_request(person_id, caregiver_id), 10, db)
            assert status["items"][0]["medication_conflict"] is True
            assert status["items"][0]["medication_report_saved"] is False
            assert question.context[0]["source_sha256"] == first_upload.sha256
            assert question.context[0]["existing_medication_review_after_days"] == 30
            assert "prescribing clinician or pharmacist" in question.question
            assert "has not advised stopping" in question.question
            assert db.query(CareAssertion).filter_by(
                person_id=person_id,
                subject_id=medication_id,
                field="medication_status",
            ).count() == 1
    finally:
        engine.dispose()


def test_postgres_corrected_filename_suggests_predecessor_without_auto_linking():
    """A normalized filename match is a suggestion only; the original relationship remains unchanged."""
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-version-suggestion-person-{suffix}"
    caregiver_id = f"doc-version-suggestion-caregiver-{suffix}"
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Document version suggestion fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Document version suggestion caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            original = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "Hospital discharge package.pdf",
                b"Original discharge date: Monday. Medication status: active.",
            )
            corrected = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "Hospital discharge package corrected.pdf",
                b"Corrected discharge date: Tuesday. Medication status: discontinued.",
            )
            assert corrected["duplicate_detected"] is False
            assert corrected["possible_version_of_evidence_id"] == original["evidence_id"]

        with Session() as db:
            original_row = db.query(Evidence).filter_by(id=original["evidence_id"], person_id=person_id).one()
            corrected_row = db.query(Evidence).filter_by(id=corrected["evidence_id"], person_id=person_id).one()
            assert corrected_row.parent_evidence_id is None
            metadata = json.loads(corrected_row.extra_metadata or "{}")
            assert metadata["possible_version_of_evidence_id"] == original_row.id
            assert metadata["possible_version_suggestion_reason"] == "normalized_filename_match"
    finally:
        engine.dispose()


def test_postgres_transient_page_failure_is_counted_and_eventually_stops_retrying(monkeypatch):
    """Repeated identical OCR timeouts remain partial and exhaust a bounded page retry budget."""
    from app.api import document_lifecycle

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-page-retry-person-{suffix}"
    caregiver_id = f"doc-page-retry-caregiver-{suffix}"
    calls = {"count": 0}
    timeout_marker = "[Page 1 OCR]\n[Page not processed: OCR engine error]"

    def fail_ocr(_upload, _content, _evidence, **_kwargs):
        calls["count"] += 1
        return timeout_marker, "fault-injected-ocr-timeout"

    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Page retry fixture"))
            db.flush()
            db.add(Caregiver(id=caregiver_id, name="Page retry caregiver", relationship="family", person_id=person_id))
            db.commit()

        with Session() as db:
            uploaded = _upload_document_for_lifecycle(
                db, person_id, caregiver_id, "page-failure.txt",
                b"Original document bytes remain unchanged during OCR timeout retries.",
            )

        monkeypatch.setattr(document_lifecycle, "_extract", fail_ocr)
        with Session() as db:
            first = extract_saved_document(uploaded["evidence_id"], _request(person_id, caregiver_id), db)
            assert first["extraction_status"] == "partial"
            assert first["retryable_page_numbers"] == [1]
            assert first["page_retry_attempts"] == {"1": 1}

        for expected_attempt in range(2, 8):
            with Session() as db:
                result = extract_saved_document(uploaded["evidence_id"], _request(person_id, caregiver_id), db)
                assert result["extraction_status"] == "partial"
                assert result["page_retry_attempts"] == {"1": expected_attempt}
                if expected_attempt < 7:
                    assert result["retryable_page_numbers"] == [1]
                    assert result["retry_exhausted_page_numbers"] == []
                else:
                    assert result["retryable_page_numbers"] == []
                    assert result["retry_exhausted_page_numbers"] == [1]

        with Session() as db:
            exhausted = extract_saved_document(uploaded["evidence_id"], _request(person_id, caregiver_id), db)
            assert exhausted["page_retry_attempts"] == {"1": 7}
            assert exhausted["retry_exhausted_page_numbers"] == [1]
            assert calls["count"] == 7
            stored_upload = db.query(EvidenceUpload).filter_by(evidence_id=uploaded["evidence_id"]).one()
            assert bytes(stored_upload.content) == b"Original document bytes remain unchanged during OCR timeout retries."
    finally:
        engine.dispose()
