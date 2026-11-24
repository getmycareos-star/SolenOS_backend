"""PostgreSQL HTTP regression for immutable open-question review receipts."""

import os
import uuid
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import SessionLocal
from app.main import app
from app.models.care import Evidence
from app.models.care_continuity import (
    CareAssertion,
    EvidenceClaimAssessment,
    EvidenceClaimDependency,
)
from app.models.uncertainty import OpenQuestionReviewEvent

DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_NAME = urlparse(DATABASE_URL).path.rsplit("/", 1)[-1].lower() if DATABASE_URL else ""
pytestmark = pytest.mark.skipif(
    not DATABASE_URL
    or not DATABASE_URL.startswith("postgresql")
    or not any(token in DATABASE_NAME for token in ("test", "benchmark")),
    reason="requires a migrated, disposable PostgreSQL test database",
)


@pytest.mark.session_security
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

@pytest.mark.session_security
def test_accepted_care_assertion_withdrawal_preserves_history_and_rejects_stale_or_foreign_requests_on_postgres():
    suffix = uuid.uuid4().hex
    with TestClient(app) as client:
        session_response = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": f"Withdrawal caregiver {suffix}"},
        )
        assert session_response.status_code == 201, session_response.text
        session = session_response.json()
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        headers = {"Authorization": f"Bearer {session['access_token']}"}

        assertion_id = str(uuid.uuid4())
        original_value = "Caregiver reports the person walks independently."
        original_source = "The caregiver's original report: walks independently."
        with SessionLocal() as db:
            db.add(CareAssertion(
                id=assertion_id,
                person_id=person_id,
                subject_type="functional",
                subject_id=f"mobility-{suffix}",
                field="mobility",
                assertion_kind="current_state",
                asserted_value=original_value,
                source_label="Caregiver report",
                source_text=original_source,
                evidence_ids=[],
                assertion_status="accepted",
                epistemic_state="reported",
                review_after_days=30,
                created_by_caregiver_id=caregiver_id,
            ))
            db.commit()

        withdrawn = client.patch(
            f"/api/v1/care-continuity/assertions/{assertion_id}/status",
            headers=headers,
            json={
                "assertion_status": "withdrawn",
                "expected_assertion_status": "accepted",
                "reason": "The caregiver confirmed this earlier report was inaccurate.",
            },
        )
        assert withdrawn.status_code == 200, withdrawn.text
        body = withdrawn.json()
        assert body["assertion_status"] == "withdrawn"
        assert body["updated_by_caregiver_id"] == caregiver_id
        assert body["source_text"] == original_source
        assert body["asserted_value"] == original_value
        assert body["created_by_caregiver_id"] == caregiver_id
        assert body["care_continuity_execution"]["stages"]["reconciliation"] == "committed"

        current_care = client.get(
            "/api/v1/care-continuity/current-care?source=assertions",
            headers=headers,
        )
        assert current_care.status_code == 200, current_care.text
        assert all(
            item.get("id") != assertion_id
            for item in current_care.json().get("items", [])
        ), "withdrawn assertions must remain in history but disappear from the current-care projection"

        assertion_history = client.get(
            f"/api/v1/care-continuity/assertions/{person_id}",
            headers=headers,
        )
        assert assertion_history.status_code == 200, assertion_history.text
        withdrawn_row = next(item for item in assertion_history.json() if item["id"] == assertion_id)
        assert withdrawn_row["latest_revision"]["revision_reason"] == (
            "Caregiver withdrew this care detail: The caregiver confirmed this earlier report was inaccurate."
        )

        with SessionLocal() as db:
            persisted = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert persisted.assertion_status == "withdrawn"
            assert persisted.source_text == original_source
            assert persisted.asserted_value == original_value
            assert persisted.created_by_caregiver_id == caregiver_id
            revisions = db.execute(text(
                "SELECT revision_number, previous_payload, current_payload, revision_reason, changed_by_caregiver_id "
                "FROM care_assertion_revisions WHERE person_id = :person_id AND assertion_id = :assertion_id"
            ), {"person_id": person_id, "assertion_id": assertion_id}).mappings().all()
            assert len(revisions) == 1
            revision = revisions[0]
            assert revision["revision_number"] == 1
            assert revision["previous_payload"]["assertion_status"] == "accepted"
            assert revision["current_payload"]["assertion_status"] == "withdrawn"
            assert "earlier report was inaccurate" in revision["revision_reason"]
            assert revision["changed_by_caregiver_id"] == caregiver_id

        stale = client.patch(
            f"/api/v1/care-continuity/assertions/{assertion_id}/status",
            headers=headers,
            json={
                "assertion_status": "withdrawn",
                "expected_assertion_status": "accepted",
                "reason": "A second attempt with an outdated displayed status.",
            },
        )
        assert stale.status_code == 409, stale.text

        foreign_session_response = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": f"Unrelated caregiver {suffix}"},
        )
        assert foreign_session_response.status_code == 201, foreign_session_response.text
        foreign_headers = {"Authorization": f"Bearer {foreign_session_response.json()['access_token']}"}
        foreign = client.patch(
            f"/api/v1/care-continuity/assertions/{assertion_id}/status",
            headers=foreign_headers,
            json={
                "assertion_status": "withdrawn",
                "expected_assertion_status": "withdrawn",
                "reason": "An unrelated caregiver must not alter this record.",
            },
        )
        assert foreign.status_code == 404, foreign.text

        with SessionLocal() as db:
            revisions_after_retries = db.execute(text(
                "SELECT revision_number FROM care_assertion_revisions "
                "WHERE person_id = :person_id AND assertion_id = :assertion_id"
            ), {"person_id": person_id, "assertion_id": assertion_id}).scalars().all()
            assert revisions_after_retries == [1]
