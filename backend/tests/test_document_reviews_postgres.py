"""Real PostgreSQL tests for document candidate decisions and LCR reconciliation.

Run only against the disposable PostgreSQL service in document-intelligence CI.
"""
import os
import uuid
import asyncio
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from starlette.datastructures import Headers, UploadFile

from app.api.document_reviews import DocumentCandidateDecision, review_document_candidate
from app.api.collaboration import upload_evidence
from app.api.document_lifecycle import extract_saved_document
from app.models.care import Caregiver, Evidence, Person
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import CareAssertion
from app.models.document_review import DocumentCandidateReview
from app.models.collaboration import EvidenceUpload

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires disposable PostgreSQL",
)


def _request(person_id: str, caregiver_id: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.scoped_person_id = person_id
    request.state.scoped_caregiver_id = caregiver_id
    return request


def _fixture(Session):
    suffix = uuid.uuid4().hex
    person_id = f"doc-review-person-{suffix}"
    caregiver_id = f"doc-review-caregiver-{suffix}"
    evidence_id = f"doc-review-evidence-{suffix}"
    analysis_id = f"doc-review-analysis-{suffix}"
    source_text = "Caregiver note: breakfast was mostly uneaten on Tuesday."
    quote = "breakfast was mostly uneaten on Tuesday."
    candidate = {
        "subject": "Care recipient",
        "observation": "Breakfast was mostly uneaten.",
        "source_quote": quote,
        "source_span_start": source_text.index(quote),
        "source_span_end": source_text.index(quote) + len(quote),
        "review_status": "pending_review",
    }
    with Session() as db:
        db.add(Person(id=person_id, name="Document review test recipient"))
        db.flush()
        db.add(Caregiver(
            id=caregiver_id, name="Document review test caregiver",
            relationship="family", person_id=person_id,
        ))
        db.flush()
        db.add(Evidence(
            id=evidence_id, person_id=person_id, type="document",
            source_text="", uploaded_by_caregiver_id=caregiver_id,
            source_type="caregiver_upload",
        ))
        db.flush()
        db.add(CaptureAnalysis(
            id=analysis_id, person_id=person_id, caregiver_id=caregiver_id,
            source_event_id=None, source_evidence_id=evidence_id,
            source_text=source_text, extraction_status="available",
            candidates={
                "kind": "document_extraction_v1",
                "binary_sha256": "a" * 64,
                "observations": [candidate],
                "open_questions": [],
            },
            review_history=[],
        ))
        db.commit()
    return person_id, caregiver_id, evidence_id, analysis_id, quote


def test_accept_reject_correct_isolation_idempotency_and_fresh_connection():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    person_id, caregiver_id, evidence_id, analysis_id, quote = _fixture(Session)
    try:
        # Accept: exactly one reported, source-linked LCR assertion.
        with Session() as db:
            accepted = review_document_candidate(
                analysis_id,
                DocumentCandidateDecision(
                    candidate_index=0, decision="accept",
                    idempotency_key="document-review-accept-0001",
                ),
                _request(person_id, caregiver_id), db,
            )
            assert accepted["written_to_lcr"] is True
            assertion_id = accepted["resulting_assertion_id"]
        with Session() as db:
            assertion = db.query(CareAssertion).filter(CareAssertion.id == assertion_id).one()
            assert assertion.person_id == person_id
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quote
            assert assertion.assertion_status == "reported"
            assert db.query(CareAssertion).filter(CareAssertion.person_id == person_id).count() == 1


        # PostgreSQL triggers prohibit direct SQL mutation/deletion of audit history.
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE document_candidate_reviews SET rationale = 'tampered' WHERE id = :id"),
                    {"id": accepted["review_id"]},
                )
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM document_candidate_reviews WHERE id = :id"),
                    {"id": accepted["review_id"]},
                )

        # Another authorized caregiver in the same person record can review the source.
        person_shared, caregiver_owner, _, analysis_shared, _ = _fixture(Session)
        caregiver_shared = f"doc-review-shared-{uuid.uuid4().hex}"
        with Session() as db:
            db.add(Caregiver(
                id=caregiver_shared, name="Second family caregiver",
                relationship="family", person_id=person_shared,
            ))
            db.commit()
        with Session() as db:
            shared_review = review_document_candidate(
                analysis_shared,
                DocumentCandidateDecision(
                    candidate_index=0, decision="reject",
                    idempotency_key=f"shared-review-{uuid.uuid4().hex}",
                ),
                _request(person_shared, caregiver_shared), db,
            )
            assert shared_review["written_to_lcr"] is False
        with Session() as db:
            shared_event = db.query(DocumentCandidateReview).filter(
                DocumentCandidateReview.id == shared_review["review_id"]
            ).one()
            assert shared_event.caregiver_id == caregiver_shared


        # A later correction appends a new review version and supersedes, but preserves, the old assertion.
        with Session() as db:
            corrected_existing = review_document_candidate(
                analysis_id,
                DocumentCandidateDecision(
                    candidate_index=0, decision="correct",
                    corrected_observation="The caregiver corrected the report: breakfast was partly eaten.",
                    rationale="The caregiver rechecked the original document.",
                    idempotency_key="document-review-correct-after-accept-0001",
                ),
                _request(person_id, caregiver_id), db,
            )
            assert corrected_existing["written_to_lcr"] is True
        with Session() as db:
            prior_assertion = db.query(CareAssertion).filter(CareAssertion.id == assertion_id).one()
            new_assertion = db.query(CareAssertion).filter(
                CareAssertion.id == corrected_existing["resulting_assertion_id"]
            ).one()
            correction_event = db.query(DocumentCandidateReview).filter(
                DocumentCandidateReview.id == corrected_existing["review_id"]
            ).one()
            assert prior_assertion.assertion_status == "superseded"
            assert correction_event.superseded_assertion_id == assertion_id
            assert "partly eaten" in new_assertion.asserted_value

        # Retrying the same idempotency key returns the same review and assertion.
        with Session() as db:
            retry = review_document_candidate(
                analysis_id,
                DocumentCandidateDecision(
                    candidate_index=0, decision="accept",
                    idempotency_key="document-review-accept-0001",
                ),
                _request(person_id, caregiver_id), db,
            )
            assert retry["review_id"] == accepted["review_id"]
            assert retry["resulting_assertion_id"] == assertion_id

        # Accepted decisions cannot be silently overwritten.
        with Session() as db:
            with pytest.raises(HTTPException) as duplicate:
                review_document_candidate(
                    analysis_id,
                    DocumentCandidateDecision(candidate_index=0, decision="reject"),
                    _request(person_id, caregiver_id), db,
                )
            assert duplicate.value.status_code == 409

        # Append-only corrections preserve the original candidate and corrected value.
        # Use a separate analysis to keep the first accepted assertion unchanged.
        person_b, caregiver_b, evidence_b, analysis_b, quote_b = _fixture(Session)
        with Session() as db:
            corrected = review_document_candidate(
                analysis_b,
                DocumentCandidateDecision(
                    candidate_index=0, decision="correct",
                    corrected_observation="The caregiver corrected this to: half of breakfast was eaten.",
                    rationale="Caregiver checked the original document.",
                    idempotency_key="document-review-correct-0001",
                ),
                _request(person_b, caregiver_b), db,
            )
            assert corrected["written_to_lcr"] is True
        with Session() as db:
            review = db.query(DocumentCandidateReview).filter(
                DocumentCandidateReview.id == corrected["review_id"]
            ).one()
            assert review.original_candidate["source_quote"] == quote_b
            assert review.corrected_observation.startswith("The caregiver corrected")
            assertion = db.query(CareAssertion).filter(
                CareAssertion.id == corrected["resulting_assertion_id"]
            ).one()
            assert "half of breakfast was eaten" in assertion.asserted_value

        # A foreign care-record identity cannot review the source.
        foreign_person, foreign_caregiver, _, _, _ = _fixture(Session)
        with Session() as db:
            with pytest.raises(HTTPException) as denied:
                review_document_candidate(
                    analysis_id,
                    DocumentCandidateDecision(candidate_index=0, decision="reject"),
                    _request(foreign_person, foreign_caregiver), db,
                )
            assert denied.value.status_code == 404


        # Candidate text with an incorrect source span is never accepted into the LCR.
        person_bad, caregiver_bad, _, analysis_bad, _ = _fixture(Session)
        with Session() as db:
            bad_analysis = db.query(CaptureAnalysis).filter(
                CaptureAnalysis.id == analysis_bad
            ).one()
            bad_analysis.candidates["observations"][0]["source_span_start"] += 1
            db.commit()
        with Session() as db:
            with pytest.raises(HTTPException) as invalid_quote:
                review_document_candidate(
                    analysis_bad,
                    DocumentCandidateDecision(candidate_index=0, decision="accept"),
                    _request(person_bad, caregiver_bad), db,
                )
            assert invalid_quote.value.status_code == 409
        with Session() as db:
            assert db.query(CareAssertion).filter(
                CareAssertion.person_id == person_bad,
                CareAssertion.field == "document_observation",
            ).count() == 0

        # Rejected candidates never create a CareAssertion.
        person_rejected, caregiver_rejected, _, rejected_analysis, _ = _fixture(Session)
        with Session() as db:
            rejected = review_document_candidate(
                rejected_analysis,
                DocumentCandidateDecision(
                    candidate_index=0, decision="reject",
                    idempotency_key="document-review-reject-0001",
                ),
                _request(person_rejected, caregiver_rejected), db,
            )
            assert rejected["written_to_lcr"] is False
            assert rejected["resulting_assertion_id"] is None
        with Session() as db:
            assert db.query(CareAssertion).filter(
                CareAssertion.person_id == person_id,
                CareAssertion.field == "document_observation",
            ).count() == 1
            assert db.query(CareAssertion).filter(
                CareAssertion.person_id == person_rejected,
                CareAssertion.field == "document_observation",
            ).count() == 0

        # A new SQLAlchemy engine/session reconstructs the persisted review and LCR state.
        engine.dispose()
        restarted_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        RestartedSession = sessionmaker(bind=restarted_engine, autoflush=False)
        try:
            with RestartedSession() as db:
                restored = db.query(CareAssertion).filter(CareAssertion.id == assertion_id).one()
                restored_review = db.query(DocumentCandidateReview).filter(
                    DocumentCandidateReview.id == accepted["review_id"]
                ).one()
                assert restored.evidence_ids == [evidence_id]
                assert restored.assertion_status == "superseded"
                assert restored_review.source_quote == quote
        finally:
            restarted_engine.dispose()
    finally:
        engine.dispose()


def test_real_postgres_upload_extract_review_and_reconstruct_after_new_connection(monkeypatch):
    from app.api import document_lifecycle

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    person_id = f"doc-e2e-person-{suffix}"
    caregiver_id = f"doc-e2e-caregiver-{suffix}"
    original = b"Care note: breakfast was mostly uneaten on Tuesday."
    quote = "breakfast was mostly uneaten on Tuesday."

    with Session() as db:
        db.add(Person(id=person_id, name="Document E2E recipient"))
        db.flush()
        db.add(Caregiver(
            id=caregiver_id, name="Document E2E caregiver",
            relationship="family", person_id=person_id,
        ))
        db.commit()

    with Session() as db:
        uploaded = asyncio.run(upload_evidence(
            _request(person_id, caregiver_id),
            UploadFile(
                filename="care-note.txt",
                file=io.BytesIO(original),
                headers=Headers({"content-type": "text/plain"}),
            ),
            person_id, caregiver_id, "document", "", "not_provided", "0", "{}", "false", db,
        ))
        assert uploaded["persisted"] is True
        evidence_id = uploaded["evidence_id"]

    def grounded_candidates(source_text, *, source_event_id, source_evidence_id):
        start = source_text.index(quote)
        return {
            "observations": [{
                "subject": "Care recipient",
                "observation": "Breakfast was mostly uneaten.",
                "source_quote": quote,
                "source_span_start": start,
                "source_span_end": start + len(quote),
                "source_evidence_id": source_evidence_id,
                "review_status": "pending_review",
            }],
            "open_questions": [],
        }

    monkeypatch.setattr(document_lifecycle, "extract_review_candidates", grounded_candidates)

    def extract_once():
        with Session() as db:
            return extract_saved_document(evidence_id, _request(person_id, caregiver_id), db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        extraction_results = list(pool.map(lambda _: extract_once(), range(2)))
    assert extraction_results[0]["analysis_id"] == extraction_results[1]["analysis_id"]
    extracted = extraction_results[0]
    assert extracted["extraction_method"] == "server_utf8_text"
    with Session() as db:
        assert db.query(CaptureAnalysis).filter(
            CaptureAnalysis.person_id == person_id,
            CaptureAnalysis.source_evidence_id == evidence_id,
        ).count() == 1
    analysis_id = extracted["analysis_id"]

    with Session() as db:
        reviewed = review_document_candidate(
            analysis_id,
            DocumentCandidateDecision(
                candidate_index=0, decision="accept",
                idempotency_key=f"doc-e2e-{suffix}-accept",
            ),
            _request(person_id, caregiver_id), db,
        )
        assert reviewed["written_to_lcr"] is True
        assertion_id = reviewed["resulting_assertion_id"]

    engine.dispose()
    restarted = create_engine(DATABASE_URL, pool_pre_ping=True)
    RestartedSession = sessionmaker(bind=restarted, autoflush=False)
    try:
        with RestartedSession() as db:
            binary = db.query(EvidenceUpload).filter(EvidenceUpload.evidence_id == evidence_id).one()
            analysis = db.query(CaptureAnalysis).filter(CaptureAnalysis.id == analysis_id).one()
            review = db.query(DocumentCandidateReview).filter(
                DocumentCandidateReview.id == reviewed["review_id"]
            ).one()
            assertion = db.query(CareAssertion).filter(CareAssertion.id == assertion_id).one()
            assert bytes(binary.content) == original
            assert analysis.source_text == original.decode("utf-8")
            assert review.source_quote == quote
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.source_text == quote
    finally:
        restarted.dispose()


def test_concurrent_acceptance_creates_one_assertion_and_one_review():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    person_id, caregiver_id, _, analysis_id, _ = _fixture(Session)

    def accept(key):
        with Session() as db:
            return review_document_candidate(
                analysis_id,
                DocumentCandidateDecision(
                    candidate_index=0, decision="accept", idempotency_key=key,
                ),
                _request(person_id, caregiver_id), db,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(accept, "document-review-concurrent-0001"),
                pool.submit(accept, "document-review-concurrent-0002"),
            ]
            outcomes = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except HTTPException as exc:
                    outcomes.append(exc.status_code)
        assert sum(isinstance(item, dict) for item in outcomes) == 1
        assert 409 in outcomes
        with Session() as db:
            assert db.query(DocumentCandidateReview).filter(
                DocumentCandidateReview.analysis_id == analysis_id,
                DocumentCandidateReview.candidate_index == 0,
            ).count() == 1
            assert db.query(CareAssertion).filter(
                CareAssertion.person_id == person_id,
                CareAssertion.field == "document_observation",
            ).count() == 1
    finally:
        engine.dispose()
