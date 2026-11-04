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
from app.models.collaboration import EvidenceUpload
from app.api.collaboration import upload_evidence
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


def test_postgres_document_upload_extraction_retry_persistence_and_person_isolation():
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
