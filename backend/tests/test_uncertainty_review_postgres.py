"""PostgreSQL HTTP regression for immutable open-question review receipts."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith("postgresql"),
    reason="requires migrated PostgreSQL DATABASE_URL",
)

from app.core.database import SessionLocal
from app.main import app
from app.models.care import Evidence
from app.models.care_continuity import (
    CareAssertion,
    EvidenceClaimAssessment,
    EvidenceClaimDependency,
)
from app.models.uncertainty import OpenQuestionReviewEvent


def test_accepted_clarification_review_persists_immutable_receipt_on_postgres():
    suffix = uuid.uuid4().hex
    person_id = None
    with TestClient(app) as client:
        session_response = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": f"Review caregiver {suffix}"},
        )
        assert session_response.status_code == 201, session_response.text
        session = session_response.json()
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        headers = {"Authorization": f"Bearer {session['access_token']}"}

        evidence_id = str(uuid.uuid4())
        source_claim_id = str(uuid.uuid4())
        with SessionLocal() as db:
            db.add_all([
                Evidence(
                    id=evidence_id,
                    person_id=person_id,
                    type="caregiver_report",
                    source_text="Caregiver reports the appointment took place on 12 October.",
                    uploaded_by_caregiver_id=caregiver_id,
                ),
                CareAssertion(
                    id=source_claim_id,
                    person_id=person_id,
                    subject_type="appointment",
                    subject_id=f"appointment-{suffix}",
                    field="attendance",
                    assertion_kind="current_state",
                    asserted_value="Attendance not yet established",
                    source_label="Existing care record",
                    source_text="Attendance remains unclear.",
                    evidence_ids=[],
                    assertion_status="reported",
                    created_by_caregiver_id=caregiver_id,
                ),
            ])
            db.commit()

        created = client.post(
            "/api/v1/uncertainty/questions",
            headers=headers,
            json={
                "person_id": person_id,
                "question": "Was the appointment attended?",
                "subject_type": "appointment",
                "subject_id": f"appointment-{suffix}",
                "claim_id": source_claim_id,
                "priority": "high",
                "necessity": "necessary",
                "necessity_rationale": "Attendance changes the follow-up context.",
                "evidence_ids": [evidence_id],
                "context": [{
                    "decision_dependency": "The follow-up plan depends on whether the appointment occurred."
                }],
            },
        )
        assert created.status_code == 200, created.text
        question_id = created.json()["id"]

        answered = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            headers=headers,
            json={
                "status": "answered",
                "answer": "The caregiver reports that the appointment was attended on 12 October.",
                "answer_provenance": "Direct report from the family caregiver.",
                "evidence_ids": [evidence_id],
            },
        )
        assert answered.status_code == 200, answered.text

        accepted = client.post(
            f"/api/v1/uncertainty/questions/{question_id}/reviews",
            headers=headers,
            json={"decision": "accepted"},
        )
        assert accepted.status_code == 200, accepted.text
        review = accepted.json()
        assert review["decision"] == "accepted"
        assert review["reviewer_caregiver_id"] == caregiver_id
        assert review["care_continuity_execution"]["stages"]["reconciliation"] == "committed"

        history = client.get(
            f"/api/v1/uncertainty/questions/{question_id}/review-history",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        assert len(history.json()) == 1
        assert history.json()[0]["id"] == review["id"]
        assert history.json()[0]["care_continuity_execution"] == review["care_continuity_execution"]

        assertions = client.get(
            f"/api/v1/care-continuity/assertions/{person_id}",
            headers=headers,
        )
        assert assertions.status_code == 200, assertions.text
        assert any(
            item["id"] == review["resulting_claim_id"]
            for item in assertions.json()
        )

        with SessionLocal() as db:
            resulting = db.query(CareAssertion).filter_by(
                id=review["resulting_claim_id"]
            ).one()
            assert resulting.person_id == person_id
            assert resulting.assertion_status == "reported"
            assert resulting.review_after_days is None
            assert resulting.created_by_caregiver_id == caregiver_id

            assessment = db.query(EvidenceClaimAssessment).filter_by(
                id=review["claim_assessment_id"]
            ).one()
            assert assessment.status == "unverified"
            assert assessment.reviewed_by_caregiver_id == caregiver_id
            assert assessment.evidence_links[0]["evidence_id"] == evidence_id
            assert assessment.evidence_links[0]["relationship"] == "neutral"

            dependency = db.query(EvidenceClaimDependency).filter_by(
                claim_id=resulting.id,
                depends_on_claim_id=source_claim_id,
                dependency_type="qualifies",
            ).one()
            assert dependency.created_by_caregiver_id == caregiver_id

            review_event = db.query(OpenQuestionReviewEvent).filter_by(
                id=review["id"]
            ).one()
            assert review_event.care_continuity_execution["stages"]["reconciliation"] == "committed"
            with pytest.raises(ValueError, match="append-only"):
                review_event.rationale = "Attempted review history rewrite"
                db.commit()
            db.rollback()
