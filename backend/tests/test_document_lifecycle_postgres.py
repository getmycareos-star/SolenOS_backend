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
from app.api.collaboration import (
    upload_evidence,
    delete_uploaded_document,
    link_corrected_document_version,
    compare_document_version,
    DocumentVersionLinkRequest,
)
from app.models.capture_analysis import CaptureAnalysis
from app.api.document_lifecycle import extract_saved_document, get_document_extraction

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
            assert {job.workflow for job in cancelled_jobs} == {"document_intake", "living_care_record"}
            assert all(job.status == "cancelled" for job in cancelled_jobs)
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
