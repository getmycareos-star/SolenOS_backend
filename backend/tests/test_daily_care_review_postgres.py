"""Real PostgreSQL integration coverage for Daily Care candidate review and LCR writes."""
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.capture_review import review_capture_candidate
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import CareAssertion
from app.schemas.capture_review import CaptureCandidateReview
from app.services.lcr_context import build_lcr_context


def _postgres_url():
    value = os.environ.get("DATABASE_URL", "")
    if value.startswith("postgresql+psycopg://"):
        value = value.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    if not value.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("Daily Care review persistence integration requires PostgreSQL DATABASE_URL")
    return value


def _request(person_id, caregiver_id):
    return SimpleNamespace(state=SimpleNamespace(
        scoped_person_id=person_id,
        scoped_caregiver_id=caregiver_id,
    ))


def test_daily_care_correction_acceptance_rejection_and_isolation_postgres():
    engine = create_engine(_postgres_url(), pool_pre_ping=True)
    suffix = uuid.uuid4().hex
    person_id = f"daily-person-{suffix}"
    caregiver_id = f"daily-caregiver-{suffix}"
    other_person_id = f"daily-other-person-{suffix}"
    other_caregiver_id = f"daily-other-caregiver-{suffix}"
    event_id = f"daily-event-{suffix}"
    evidence_id = f"daily-evidence-{suffix}"
    analysis_id = f"daily-analysis-{suffix}"
    original_text = "Caregiver reported that Mum woke twice overnight."
    original_observation = "Mum woke twice overnight."
    corrected_observation = "Mum woke three times overnight."
    now = datetime.now(UTC)

    try:
        with Session(engine) as db:
            db.add_all([
                Person(id=person_id, name="Daily Care PostgreSQL fixture"),
                Person(id=other_person_id, name="Other Daily Care PostgreSQL fixture"),
            ])
            db.flush()
            db.add_all([
                Caregiver(id=caregiver_id, name="Caregiver", relationship="family", person_id=person_id),
                Caregiver(id=other_caregiver_id, name="Other caregiver", relationship="family", person_id=other_person_id),
            ])
            db.flush()
            evidence = Evidence(
                id=evidence_id, person_id=person_id, type="note", evidence_kind="caregiver_report",
                epistemic_status="caregiver_reported", source_text=json.dumps({"notes": original_text}),
                uploaded_by_caregiver_id=caregiver_id, reported_by_caregiver_id=caregiver_id,
                collected_by_caregiver_id=caregiver_id, reported_by_label="Caregiver",
                source_type="caregiver_report", occurred_at=now - timedelta(hours=2),
            )
            event = CareEvent(
                id=event_id, person_id=person_id, event_type="structured_daily_capture",
                status="recorded", occurred_at=now - timedelta(hours=2), title="Daily Care observation",
                description=json.dumps({"notes": original_text}), evidence_ids=[evidence_id],
                created_by_caregiver_id=caregiver_id, time_provenance="caregiver_reported",
            )
            analysis = CaptureAnalysis(
                id=analysis_id, person_id=person_id, caregiver_id=caregiver_id,
                source_event_id=event_id, source_evidence_id=evidence_id,
                source_text=json.dumps({"notes": original_text}), extraction_status="available",
                candidates={"observations": [
                    {"subject": "sleep", "observation": original_observation,
                     "source_quote": original_text, "review_status": "pending_review"},
                    {"subject": "mood", "observation": "Unconfirmed generated suggestion",
                     "source_quote": original_text, "review_status": "pending_review"},
                ]},
                review_history=[],
            )
            db.add_all([evidence, event, analysis])
            db.commit()

            corrected = review_capture_candidate(
                analysis_id,
                CaptureCandidateReview(
                    candidate_index=0, decision="correct",
                    corrected_observation=corrected_observation,
                ),
                _request(person_id, caregiver_id),
                db,
            )
            assert corrected["written_to_lcr"] is False
            assert corrected["candidate"]["review_status"] == "corrected_pending_approval"
            assert corrected["candidate"]["observation"] == original_observation
            assert corrected["candidate"]["corrected_observation"] == corrected_observation
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0

            accepted = review_capture_candidate(
                analysis_id,
                CaptureCandidateReview(candidate_index=0, decision="accept"),
                _request(person_id, caregiver_id),
                db,
            )
            assert accepted["written_to_lcr"] is True
            assertion_id = accepted["assertion_id"]
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert assertion.person_id == person_id
            assert assertion.source_event_id == event_id
            assert assertion.evidence_ids == [evidence_id]
            assert assertion.created_by_caregiver_id == caregiver_id
            assert assertion.updated_by_caregiver_id == caregiver_id
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.asserted_value == f"sleep: {corrected_observation}"
            assert original_text in assertion.source_text

            repeated = False
            try:
                review_capture_candidate(
                    analysis_id,
                    CaptureCandidateReview(candidate_index=0, decision="accept"),
                    _request(person_id, caregiver_id),
                    db,
                )
            except HTTPException as exc:
                repeated = exc.status_code == 409
            assert repeated, "repeated acceptance must be rejected without creating a duplicate"

            rejected = review_capture_candidate(
                analysis_id,
                CaptureCandidateReview(candidate_index=1, decision="reject"),
                _request(person_id, caregiver_id),
                db,
            )
            assert rejected["written_to_lcr"] is False
            assert rejected["assertion_id"] is None
            assert rejected["candidate"]["review_status"] == "rejected_by_caregiver"

            rejected_retry = False
            try:
                review_capture_candidate(
                    analysis_id,
                    CaptureCandidateReview(candidate_index=1, decision="accept"),
                    _request(person_id, caregiver_id),
                    db,
                )
            except HTTPException as exc:
                rejected_retry = exc.status_code == 409
            assert rejected_retry, "rejected candidate must remain final after retry"

            persisted = db.query(CaptureAnalysis).filter_by(id=analysis_id).one()
            saved_candidate = persisted.candidates["observations"][0]
            assert saved_candidate["observation"] == original_observation
            assert saved_candidate["corrected_observation"] == corrected_observation
            assert persisted.candidates["observations"][1]["review_status"] == "rejected_by_caregiver"
            assert len(persisted.review_history) == 3
            assert persisted.review_history[0]["original_candidate"]["observation"] == original_observation
            assert all(entry["reviewed_by_caregiver_id"] == caregiver_id for entry in persisted.review_history)
            saved_event = db.query(CareEvent).filter_by(id=event_id).one()
            assert saved_event.description == json.dumps({"notes": original_text})
            saved_evidence = db.query(Evidence).filter_by(id=evidence_id).one()
            assert saved_evidence.source_text == json.dumps({"notes": original_text})

            projection = build_lcr_context(db, person_id, caregiver_id=caregiver_id, limit_per_source=50)
            assertion_items = projection["sources"]["assertions"]["items"]
            assert sum(item["id"] == assertion_id for item in assertion_items) == 1
            assert not any(item.get("asserted_value") == "Unconfirmed generated suggestion" for item in assertion_items)
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 1

            denied = False
            try:
                review_capture_candidate(
                    analysis_id,
                    CaptureCandidateReview(candidate_index=0, decision="accept"),
                    _request(other_person_id, other_caregiver_id),
                    db,
                )
            except HTTPException as exc:
                denied = exc.status_code == 404
            assert denied, "another care recipient must not read or review this analysis"
            assert db.query(CareAssertion).filter_by(person_id=other_person_id).count() == 0
    finally:
        engine.dispose()
