from fastapi.testclient import TestClient
import pytest
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver, Evidence
from app.models.care_continuity import CareAssertion
from app.models.anonymous_session import AnonymousSession

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()



def authorized_client(person_id: str) -> TestClient:
    """Issue a real test bearer capability scoped to an existing test person."""
    db = TestingSessionLocal()
    try:
        caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
        assert caregiver is not None, f"test caregiver missing for {person_id}"
        token = f"test-token-for-{person_id}"
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=caregiver.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        db.commit()
    finally:
        db.close()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client

def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def test_create_information_gap():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="caregiver-1",
            name="Test Caregiver",
            email="test@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-1")
        response = client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-1",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "dose",
                "epistemic_state": "unknown",
                "gap_reason": "not_documented",
                "priority": "high",
                "description": "Medication dose is unknown",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["person_id"] == "person-1"
        assert data["field"] == "dose"
        assert data["epistemic_state"] == "unknown"
        assert data["gap_reason"] == "not_documented"
        assert data["lifecycle_status"] == "open"
    finally:
        teardown_db()


def test_unknown_vs_false():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-2", name="Test Person 2")
        caregiver = Caregiver(
            id="caregiver-2",
            name="Test Caregiver 2",
            email="test2@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-2")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-2",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "allergies",
                "epistemic_state": "unknown",
                "gap_reason": "not_assessed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "unknown"
        
        response = client.get(
            "/api/v1/uncertainty/view/person-2/medication/med-1"
        )
        assert response.status_code == 200
        view = response.json()
        assert view["epistemic_state"] == "unknown"
        assert view["known_value"] is None
    finally:
        teardown_db()


def test_contradiction_not_collapsed_to_unknown():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-3", name="Test Person 3")
        caregiver = Caregiver(
            id="caregiver-3",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add_all([
            Evidence(id="ev-1", person_id=person.id, type="document", source_text="Medication list states discontinued.", uploaded_by_caregiver_id=caregiver.id, source_type="document"),
            Evidence(id="ev-2", person_id=person.id, type="document", source_text="Current medication list includes metformin.", uploaded_by_caregiver_id=caregiver.id, source_type="document"),
        ])
        db.commit()
        db.close()

        client = authorized_client("person-3")
        
        response = client.post(
            "/api/v1/uncertainty/contradictions",
            json={
                "person_id": "person-3",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "status",
                "description": "One document says metformin is discontinued, another lists it as current medication",
                "evidence_ids": ["ev-1", "ev-2"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["resolution_status"] == "unresolved"
        
        response = client.get(
            "/api/v1/uncertainty/view/person-3/medication/med-1"
        )
        assert response.status_code == 200
        view = response.json()
        assert len(view["contradictions"]) == 1
    finally:
        teardown_db()


def test_ambiguous_not_silently_resolved():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-4", name="Test Person 4")
        caregiver = Caregiver(
            id="caregiver-4",
            name="Test Caregiver 4",
            email="test4@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-4")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-4",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "instruction",
                "epistemic_state": "ambiguous",
                "gap_reason": "ambiguous",
                "known_value": "Continue current medications",
                "context": [{"note": "No medication list attached"}],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "ambiguous"
        assert data["known_value"] == "Continue current medications"
    finally:
        teardown_db()


def test_open_question_lifecycle():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-5", name="Test Person 5")
        caregiver = Caregiver(
            id="caregiver-5",
            name="Test Caregiver 5",
            email="test5@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="person-5-other", name="Other Test Person")
        other_caregiver = Caregiver(
            id="caregiver-5-other",
            name="Other Test Caregiver",
            email="test5-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-5")
        other_client = authorized_client("person-5-other")
        
        response = client.post(
            "/api/v1/uncertainty/questions",
            json={
                "person_id": "person-5",
                "question": "Did the patient attend the cardiology appointment?",
                "subject_type": "appointment",
                "subject_id": "appt-1",
                "priority": "high",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "open"
        assert data["answer"] is None
        
        question_id = data["id"]

        first_answer = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "answered",
                "answer": "The caregiver reports that the appointment was attended.",
                "answer_provenance": "Caregiver report recorded during the current session.",
            },
        )
        assert first_answer.status_code == 200, first_answer.text
        assert first_answer.json()["answered_by_caregiver_id"] == "caregiver-5"

        first_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert first_history.status_code == 200, first_history.text
        assert len(first_history.json()) == 1
        assert first_history.json()[0]["answer"] == "The caregiver reports that the appointment was attended."
        assert first_history.json()[0]["answered_by_caregiver_id"] == "caregiver-5"

        reopened = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"status": "open"},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["answer"] == "The caregiver reports that the appointment was attended."

        corrected_answer = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "answered",
                "answer": "The caregiver reports attendance and says follow-up was discussed.",
                "answer_provenance": "Caregiver clarification updated after checking the visit note.",
            },
        )
        assert corrected_answer.status_code == 200, corrected_answer.text

        second_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert second_history.status_code == 200, second_history.text
        assert len(second_history.json()) == 2
        assert second_history.json()[0]["answer"] != second_history.json()[1]["answer"]
        assert all(row["person_id"] == "person-5" for row in second_history.json())
        assert other_client.get(
            f"/api/v1/uncertainty/questions/{question_id}/answer-history"
        ).status_code == 404

        from app.models.uncertainty import OpenQuestionAnswerEvent
        audit_db = TestingSessionLocal()
        try:
            answer_event = audit_db.query(OpenQuestionAnswerEvent).order_by(
                OpenQuestionAnswerEvent.created_at.asc()
            ).first()
            answer_event.answer = "Attempted history rewrite"
            with pytest.raises(ValueError, match="append-only"):
                audit_db.commit()
            audit_db.rollback()
        finally:
            audit_db.close()

        response = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"status": "closed"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["answer"] == "The caregiver reports attendance and says follow-up was discussed."
    finally:
        teardown_db()



def test_clarification_question_requires_same_record_claim_and_matching_subject_scope():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="question-claim-person", name="Question Claim Person")
        caregiver = Caregiver(
            id="question-claim-caregiver",
            name="Question Claim Caregiver",
            email="question-claim@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="question-claim-other-person", name="Other Question Claim Person")
        other_caregiver = Caregiver(
            id="question-claim-other-caregiver",
            name="Other Question Claim Caregiver",
            email="question-claim-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.flush()
        db.add_all([
            CareAssertion(
                id="question-claim-own",
                person_id=person.id,
                subject_type="daily_care",
                subject_id="breakfast",
                field="intake",
                assertion_kind="reported_state",
                asserted_value="Breakfast intake was lower than usual.",
                source_label="caregiver report",
                source_text="Breakfast intake was lower than usual.",
                evidence_ids=[],
                assertion_status="reported",
            ),
            CareAssertion(
                id="question-claim-foreign",
                person_id=other_person.id,
                subject_type="daily_care",
                subject_id="breakfast",
                field="intake",
                assertion_kind="reported_state",
                asserted_value="Other person's breakfast intake.",
                source_label="caregiver report",
                source_text="Other person's breakfast intake.",
                evidence_ids=[],
                assertion_status="reported",
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client("question-claim-person")
        base = {
            "person_id": "question-claim-person",
            "question": "Was the lower intake also observed at lunch?",
            "subject_type": "daily_care",
            "subject_id": "breakfast",
            "claim_id": "question-claim-own",
            "priority": "informational",
        }
        created = client.post("/api/v1/uncertainty/questions", json=base)
        assert created.status_code == 200, created.text
        assert created.json()["claim_id"] == "question-claim-own"

        mismatched_subject = client.post(
            "/api/v1/uncertainty/questions",
            json={**base, "subject_id": "sleep", "question": "Was sleep affected?"},
        )
        assert mismatched_subject.status_code == 422, mismatched_subject.text

        foreign_claim = client.post(
            "/api/v1/uncertainty/questions",
            json={**base, "claim_id": "question-claim-foreign"},
        )
        assert foreign_claim.status_code == 422, foreign_claim.text

        missing_necessity_rationale = client.post(
            "/api/v1/uncertainty/questions",
            json={
                **base,
                "question": "Who can confirm the medication list?",
                "necessity": "necessary",
                "intended_answerer_role": "professional",
            },
        )
        assert missing_necessity_rationale.status_code == 422, missing_necessity_rationale.text

        with_intent = client.post(
            "/api/v1/uncertainty/questions",
            json={
                **base,
                "question": "Who can confirm the medication list?",
                "intended_answerer_role": "professional",
                "intended_answerer_label": "Cardiology clinic",
                "necessity": "necessary",
                "necessity_rationale": "The current medication status is disputed and is needed for the appointment handoff.",
            },
        )
        assert with_intent.status_code == 200, with_intent.text
        assert with_intent.json()["intended_answerer_role"] == "professional"
        assert with_intent.json()["intended_answerer_label"] == "Cardiology clinic"
        assert with_intent.json()["necessity"] == "necessary"
        assert "needed for the appointment handoff" in with_intent.json()["necessity_rationale"]

        invalid_answerer = client.post(
            "/api/v1/uncertainty/questions",
            json={**base, "question": "Who can confirm this?", "intended_answerer_role": "ai_model"},
        )
        assert invalid_answerer.status_code == 422, invalid_answerer.text
    finally:
        teardown_db()


def test_partial_knowledge_representation():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-6", name="Test Person 6")
        caregiver = Caregiver(
            id="caregiver-6",
            name="Test Caregiver 6",
            email="test6@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-6")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-6",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "medication_details",
                "epistemic_state": "partially_known",
                "gap_reason": "missing_context",
                "known_value": "Metformin",
                "context": [
                    {"field": "dose", "state": "unknown"},
                    {"field": "frequency", "state": "unknown"},
                    {"field": "current_status", "state": "unknown"},
                    {"field": "adherence", "state": "unknown"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "partially_known"
        assert data["known_value"] == "Metformin"
        assert len(data["context"]) == 4
        assert data["context"][0]["field"] == "dose"
    finally:
        teardown_db()


def test_epistemic_view_summary():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-7", name="Test Person 7")
        caregiver = Caregiver(
            id="caregiver-7",
            name="Test Caregiver 7",
            email="test7@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add(Evidence(
            id="evidence-person-7-med-name",
            person_id=person.id,
            type="caregiver_report",
            source_text="Caregiver reported the medication name as Metformin.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        ))
        db.commit()
        db.close()

        client = authorized_client("person-7")
        
        client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "name",
                "epistemic_state": "known",
                "known_value": "Metformin",
                "evidence_ids": ["evidence-person-7-med-name"],
            },
        )
        
        client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "dose",
                "epistemic_state": "unknown",
                "gap_reason": "not_documented",
                "priority": "high",
            },
        )
        
        client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "dose",
                "epistemic_state": "unknown",
                "gap_reason": "not_documented",
            },
        )
        
        client.post(
            "/api/v1/uncertainty/questions",
            json={
                "person_id": "person-7",
                "question": "Is the patient still taking this medication?",
                "subject_type": "medication",
                "subject_id": "med-1",
                "priority": "medium",
            },
        )
        
        response = client.get(
            "/api/v1/uncertainty/summary/person-7/medication/med-1"
        )
        assert response.status_code == 200
        summary = response.json()
        assert summary["known_count"] == 1
        assert summary["unknown_count"] == 1
        assert summary["open_gaps_count"] == 1
        assert summary["open_questions_count"] == 1
        assert summary["total_fields"] == 2
    finally:
        teardown_db()


def test_case_b_explicit_negative_not_unknown():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-b", name="Test Person B")
        caregiver = Caregiver(
            id="caregiver-b",
            name="Test Caregiver B",
            email="testb@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add(Evidence(
            id="evidence-person-b-falls",
            person_id=person.id,
            type="caregiver_report",
            source_text="Caregiver reported no falls in the past six months.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        ))
        db.commit()
        db.close()

        client = authorized_client("person-b")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-b",
                "subject_type": "fall_history",
                "subject_id": "fall-1",
                "field": "recent_falls",
                "epistemic_state": "known",
                "known_value": "No falls in the past six months",
                "gap_reason": "absent",
                "evidence_ids": ["evidence-person-b-falls"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "known"
        assert data["known_value"] == "No falls in the past six months"
    finally:
        teardown_db()


def test_case_f_missing_outcome():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-f", name="Test Person F")
        caregiver = Caregiver(
            id="caregiver-f",
            name="Test Caregiver F",
            email="testf@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-f")
        
        response = client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-f",
                "subject_type": "referral",
                "subject_id": "referral-1",
                "field": "outcome",
                "epistemic_state": "unknown",
                "gap_reason": "expected_but_unavailable",
                "description": "Cardiology referral made six months ago, no information about whether appointment occurred",
                "priority": "high",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "unknown"
        assert data["gap_reason"] == "expected_but_unavailable"
    finally:
        teardown_db()


def test_case_g_missing_date_with_partial_temporal():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-g", name="Test Person G")
        caregiver = Caregiver(
            id="caregiver-g",
            name="Test Caregiver G",
            email="testg@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-g")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-g",
                "subject_type": "diagnosis",
                "subject_id": "dx-1",
                "field": "onset_date",
                "epistemic_state": "partially_known",
                "gap_reason": "missing_context",
                "known_value": "Diagnosed before June 2024",
                "temporal_precision": "before_June_2024",
                "context": [{"note": "Exact onset date unknown, but predates June 2024"}],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "partially_known"
        assert data["temporal_precision"] == "before_June_2024"
    finally:
        teardown_db()


def test_case_h_missing_context():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-h", name="Test Person H")
        caregiver = Caregiver(
            id="caregiver-h",
            name="Test Caregiver H",
            email="testh@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-h")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-h",
                "subject_type": "observation",
                "subject_id": "obs-1",
                "field": "blood_pressure",
                "epistemic_state": "partially_known",
                "gap_reason": "missing_context",
                "known_value": "150/95",
                "context": [
                    {"missing": "date"},
                    {"missing": "patient_context"},
                    {"missing": "measurement_circumstances"},
                    {"missing": "source"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "partially_known"
        assert data["known_value"] == "150/95"
        assert len(data["context"]) == 4
    finally:
        teardown_db()


def test_case_i_incomplete_record():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-i", name="Test Person I")
        caregiver = Caregiver(
            id="caregiver-i",
            name="Test Caregiver I",
            email="testi@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-i")
        
        response = client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-i",
                "subject_type": "record",
                "subject_id": "hospitalization-1",
                "field": "completeness",
                "epistemic_state": "unknown",
                "gap_reason": "insufficient_evidence",
                "description": "Only discharge summary received; cannot conclude this represents complete care history",
                "priority": "high",
                "context": [
                    {"known": "discharge_summary"},
                    {"missing": "full_medical_record"},
                    {"missing": "prior_care_history"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "unknown"
        assert data["gap_reason"] == "insufficient_evidence"
    finally:
        teardown_db()


def test_uncertainty_api_enforces_session_scope_and_ignores_body_identity():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person_a = Person(id="scope-person-a", name="Scoped Person A")
        caregiver_a = Caregiver(id="scope-caregiver-a", name="Caregiver A", email=None, relationship="Family", person_id=person_a.id)
        person_b = Person(id="scope-person-b", name="Scoped Person B")
        caregiver_b = Caregiver(id="scope-caregiver-b", name="Caregiver B", email=None, relationship="Family", person_id=person_b.id)
        db.add_all([person_a, caregiver_a, person_b, caregiver_b])
        db.commit()
        db.close()

        client = authorized_client("scope-person-a")
        denied = client.get("/api/v1/uncertainty/gaps/scope-person-b")
        assert denied.status_code == 403

        created = client.post("/api/v1/uncertainty/gaps", json={
            "person_id": "scope-person-b",
            "subject_type": "medication",
            "subject_id": "med-scope",
            "field": "dose",
            "epistemic_state": "unknown",
            "gap_reason": "not_documented",
            "description": "Dose has not been documented.",
        })
        assert created.status_code == 200
        assert created.json()["person_id"] == "scope-person-a"
        assert client.get("/api/v1/uncertainty/gaps/scope-person-b").status_code == 403
    finally:
        teardown_db()


def test_uncertainty_api_rejects_missing_bearer_session():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        response = client.get("/api/v1/uncertainty/gaps/person-not-authorized")
        assert response.status_code == 401
    finally:
        teardown_db()



def test_conflict_resolution_requires_rationale_and_preserves_reopen_history():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="resolution-person", name="Resolution Person")
        caregiver = Caregiver(
            id="resolution-caregiver",
            name="Resolution Caregiver",
            email="resolution@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="resolution-other-person", name="Other Person")
        other_caregiver = Caregiver(
            id="resolution-other-caregiver",
            name="Other Caregiver",
            email="resolution-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.add(Evidence(
            id="resolution-evidence",
            person_id=person.id,
            type="document",
            source_text="The source resolves the reported date only.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        ))
        db.commit()
        db.close()

        client = authorized_client("resolution-person")
        other_client = authorized_client("resolution-other-person")
        created = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "resolution-person",
            "subject_type": "medication",
            "subject_id": "medication-1",
            "field": "status",
            "description": "Two records disagree about current status",
            "evidence_ids": [],
        })
        assert created.status_code == 200, created.text
        conflict_id = created.json()["id"]

        denied = other_client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "resolved", "resolution_notes": "Reviewed"},
        )
        assert denied.status_code == 404

        missing_rationale = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "resolved"},
        )
        assert missing_rationale.status_code == 422

        missing_resolution_evidence = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "resolved",
                "resolution_notes": "Attempted to resolve without linking any source evidence.",
            },
        )
        assert missing_resolution_evidence.status_code == 422, missing_resolution_evidence.text

        missing_scope = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_notes": "Reviewed the source.",
                "evidence_ids": ["resolution-evidence"],
            },
        )
        assert missing_scope.status_code == 422, missing_scope.text

        partial = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_notes": "The date detail is established, but the medication status remains disputed.",
                "resolution_scope": "Resolve only the reported date; leave medication status unresolved.",
                "evidence_ids": ["resolution-evidence"],
            },
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["resolution_status"] == "partially_resolved"

        resolved = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "resolved", "resolution_notes": "Reviewed the two source records; discrepancy remains documented."},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolution_status"] == "resolved"

        reopened = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "reopened", "resolution_notes": "New conflicting evidence was added; reassessment required."},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["resolution_status"] == "reopened"

        history = client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history")
        assert history.status_code == 200, history.text
        events = history.json()
        assert [event["new_status"] for event in events] == ["partially_resolved", "resolved", "reopened"]
        assert events[0]["resolution_scope"] == "Resolve only the reported date; leave medication status unresolved."
        assert all(event["rationale"] for event in events)
        assert all(event["person_id"] == "resolution-person" for event in events)

        from app.models.uncertainty import ContradictionResolutionEvent
        audit_db = TestingSessionLocal()
        try:
            audit_event = audit_db.query(ContradictionResolutionEvent).first()
            audit_event.rationale = "Attempted audit rewrite"
            with pytest.raises(ValueError, match="append-only"):
                audit_db.commit()
            audit_db.rollback()
        finally:
            audit_db.close()

        assert other_client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history").status_code == 404
    finally:
        teardown_db()



def test_evidence_review_history_persists_attribution_and_is_person_scoped():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="review-person", name="Review Person")
        caregiver = Caregiver(
            id="review-caregiver", name="Review Caregiver",
            email="review@example.com", relationship="Family", person_id=person.id,
        )
        other_person = Person(id="review-other-person", name="Other Review Person")
        other_caregiver = Caregiver(
            id="review-other-caregiver", name="Other Review Caregiver",
            email="review-other@example.com", relationship="Family", person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.flush()
        evidence = Evidence(
            id="review-evidence-1", person_id=person.id, type="caregiver_report",
            source_text="Caregiver reported a missed appointment.", uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report", time_provenance="reported as occurring last Tuesday",
        )
        db.add(evidence)
        db.commit()
        db.close()

        client = authorized_client("review-person")
        other_client = authorized_client("review-other-person")
        payload = {
            "review_type": "reliability",
            "outcome": "unverified",
            "rationale": "The source is attributed, but no independent corroborating record is linked.",
            "policy_version": "evidence-review-v1",
            "criteria": [
                {"criterion": "source attribution", "result": "present", "rationale": "Uploader and source type are persisted."},
                {"criterion": "independent corroboration", "result": "none_linked", "rationale": "No independent source is linked."},
                {"criterion": "temporal context", "result": "partial", "rationale": "The report says last Tuesday without an absolute date."},
                {"criterion": "evidence freshness", "result": "undetermined", "rationale": "The event date is not absolute enough to assess current relevance."},
                {"criterion": "record completeness", "result": "partial", "rationale": "The report does not include a source document or follow-up outcome."},
            ],
        }
        created = client.post("/api/v1/care-continuity/evidence/review-evidence-1/reviews", json=payload)
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["evidence_id"] == "review-evidence-1"
        assert row["source_attribution"]["source_type"] == "caregiver_report"
        assert row["reviewed_by_caregiver_id"] == "review-caregiver"

        listed = client.get("/api/v1/care-continuity/evidence/review-evidence-1/reviews")
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        assert listed.json()[0]["policy_version"] == "evidence-review-v1"
        assert other_client.get("/api/v1/care-continuity/evidence/review-evidence-1/reviews").status_code == 404
        invalid = client.post(
            "/api/v1/care-continuity/evidence/review-evidence-1/reviews",
            json={**payload, "outcome": "high_confidence"},
        )
        assert invalid.status_code == 422
    finally:
        teardown_db()



def test_conflict_candidates_match_context_and_overlapping_time_without_auto_resolution():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="match-person", name="Match Person")
        caregiver = Caregiver(
            id="match-caregiver", name="Match Caregiver",
            email="match@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("match-person")
        base = {
            "person_id": "match-person",
            "subject_type": "daily_care",
            "subject_id": "sleep-observation",
            "field": "sleep_pattern",
            "description": "Care reports differ about sleep timing.",
            "context_key": "overnight-at-home",
        }
        first = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-01T00:00:00Z", "effective_to": "2026-10-03T00:00:00Z",
        })
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]
        assert first.json()["resolution_status"] == "unresolved"

        overlapping = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-02T00:00:00Z", "effective_to": "2026-10-04T00:00:00Z",
        })
        assert overlapping.status_code == 200, overlapping.text
        assert first_id in overlapping.json()["matching_candidate_ids"]
        assert overlapping.json()["resolution_status"] == "unresolved"

        different_context = client.post("/api/v1/uncertainty/contradictions", json={
            **{**base, "context_key": "overnight-at-facility"},
            "effective_from": "2026-10-02T00:00:00Z", "effective_to": "2026-10-04T00:00:00Z",
        })
        assert different_context.status_code == 200, different_context.text
        assert different_context.json()["matching_candidate_ids"] == []

        disjoint = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-05T00:00:00Z", "effective_to": "2026-10-06T00:00:00Z",
        })
        assert disjoint.status_code == 200, disjoint.text
        assert disjoint.json()["matching_candidate_ids"] == []

        invalid_interval = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-06T00:00:00Z", "effective_to": "2026-10-05T00:00:00Z",
        })
        assert invalid_interval.status_code == 422

        unaudited_resolution = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "resolution_status": "resolved", "resolution_notes": "Client attempted to create an already resolved conflict.",
        })
        assert unaudited_resolution.status_code == 422
    finally:
        teardown_db()



def test_conflict_matching_respects_context_and_explicit_time_intervals():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="conflict-scope-person", name="Conflict Scope Person")
        caregiver = Caregiver(
            id="conflict-scope-caregiver",
            name="Conflict Scope Caregiver",
            email="conflict-scope@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()
        client = authorized_client("conflict-scope-person")

        first = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "conflict-scope-person",
            "subject_type": "daily_care",
            "subject_id": "meal-1",
            "field": "amount_eaten",
            "description": "Report A",
            "context_key": "breakfast",
            "effective_from": "2026-10-01T08:00:00Z",
            "effective_to": "2026-10-01T09:00:00Z",
            "evidence_ids": [],
        })
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]

        different_context_and_disjoint_time = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "conflict-scope-person",
            "subject_type": "daily_care",
            "subject_id": "meal-1",
            "field": "amount_eaten",
            "description": "Report B",
            "context_key": "dinner",
            "effective_from": "2026-10-01T18:00:00Z",
            "effective_to": "2026-10-01T19:00:00Z",
            "evidence_ids": [],
        })
        assert different_context_and_disjoint_time.status_code == 200, different_context_and_disjoint_time.text
        assert first_id not in different_context_and_disjoint_time.json()["matching_candidate_ids"]

        same_context_overlapping_time = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "conflict-scope-person",
            "subject_type": "daily_care",
            "subject_id": "meal-1",
            "field": "amount_eaten",
            "description": "Report C",
            "context_key": "breakfast",
            "effective_from": "2026-10-01T08:30:00Z",
            "effective_to": "2026-10-01T09:30:00Z",
            "evidence_ids": [],
        })
        assert same_context_overlapping_time.status_code == 200, same_context_overlapping_time.text
        assert first_id in same_context_overlapping_time.json()["matching_candidate_ids"]
    finally:
        teardown_db()


def test_analyze_endpoint_does_not_accept_assumption_from_unverified_persisted_evidence():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-analysis", name="Analysis Test Person")
        caregiver = Caregiver(
            id="caregiver-analysis",
            name="Analysis Test Caregiver",
            email="analysis@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="evidence-analysis-unverified",
            person_id=person.id,
            type="caregiver_report",
            source_text="A care instruction was reported but not independently checked.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
            evidence_status="unverified",
        )
        db.add_all([person, caregiver, evidence])
        db.commit()
        db.close()

        client = authorized_client("person-analysis")
        payload = {
            "person_id": "person-analysis",
            "subject_type": "care_instruction",
            "subject_id": "instruction-1",
            "field": "current_instruction",
            "claim": "The current care instruction is confirmed.",
            "task": "recommendation",
            "evidence_ids": ["evidence-analysis-unverified"],
            "assumptions": [{
                "statement": "The report reflects the current instruction.",
                "status": "accepted",
                "supporting_evidence_ids": ["evidence-analysis-unverified"],
                "reviewed_by_caregiver_id": "different-caregiver",
                "resolution_rationale": "Reviewed against the supplied report.",
            }],
        }
        unauthorized = client.post("/api/v1/uncertainty/analyze", json=payload)
        assert unauthorized.status_code == 403, unauthorized.text

        payload["assumptions"][0]["reviewed_by_caregiver_id"] = "caregiver-analysis"
        response = client.post("/api/v1/uncertainty/analyze", json=payload)
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["assumptions"][0]["status"] == "unresolved"
        assert analysis["assumptions"][0]["has_verified_supporting_evidence"] is False
        assert "unresolved_assumptions" in analysis["classifications"]
        assert analysis["sufficiency"] == "insufficient_pending_resolution"
        assert analysis["conclusion_confidence"] is None
    finally:
        teardown_db()



def test_source_lineage_rejects_false_independence_and_cross_record_links():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="lineage-person", name="Lineage Person")
        other_person = Person(id="lineage-other-person", name="Other Person")
        caregiver = Caregiver(id="lineage-caregiver", name="Lineage Caregiver", email="lineage@example.com", relationship="Family", person_id=person.id)
        other_caregiver = Caregiver(id="lineage-other-caregiver", name="Other Caregiver", email="lineage-other@example.com", relationship="Family", person_id=other_person.id)
        db.add_all([person, other_person, caregiver, other_caregiver])
        db.flush()
        db.add_all([
            Evidence(id="lineage-origin", person_id=person.id, type="message", source_text="Original report", uploaded_by_caregiver_id=caregiver.id),
            Evidence(id="lineage-forward", person_id=person.id, type="message", source_text="Forwarded report", uploaded_by_caregiver_id=caregiver.id),
            Evidence(id="lineage-cross-record", person_id=other_person.id, type="message", source_text="Other record report", uploaded_by_caregiver_id=other_caregiver.id),
        ])
        person_id = person.id
        caregiver_id = caregiver.id
        db.commit()
        db.close()

        client = authorized_client(person_id)
        dependent = client.post("/api/v1/care-continuity/evidence/lineage-forward/relationships", json={
            "related_evidence_id": "lineage-origin",
            "relationship_type": "forwarded_from",
            "source_independence_status": "shared_origin",
            "origin_group_key": "message-thread-1",
            "rationale": "Forwarded from the original message.",
        })
        assert dependent.status_code == 201, dependent.text
        assert dependent.json()["source_independence_status"] == "shared_origin"
        assert dependent.json()["reviewed_by_caregiver_id"] == caregiver_id

        false_independence = client.post("/api/v1/care-continuity/evidence/lineage-forward/relationships", json={
            "related_evidence_id": "lineage-origin",
            "relationship_type": "copied_from",
            "source_independence_status": "independent",
            "rationale": "Attempt to mark a copy independent.",
        })
        assert false_independence.status_code == 422

        cross_record = client.post("/api/v1/care-continuity/evidence/lineage-forward/relationships", json={
            "related_evidence_id": "lineage-cross-record",
            "relationship_type": "related_to",
            "source_independence_status": "unknown",
            "rationale": "Should not cross care records.",
        })
        assert cross_record.status_code == 404

        listed = client.get("/api/v1/care-continuity/evidence/lineage-origin/relationships")
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        assert listed.json()[0]["source_evidence_id"] == "lineage-forward"
    finally:
        teardown_db()


def test_structured_observation_requires_exact_source_and_preserves_correction_history():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import EvidenceObservation
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="observation-person", name="Observation Person")
        caregiver = Caregiver(id="observation-caregiver", name="Observation Caregiver", email="observation@example.com", relationship="Family", person_id=person.id)
        db.add_all([person, caregiver])
        db.flush()
        db.add(Evidence(
            id="observation-evidence", person_id=person.id, type="caregiver_report",
            source_text="Caregiver reports three awakenings last night.",
            uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
        ))
        person_id = person.id
        db.commit()
        db.close()

        client = authorized_client(person_id)
        source_text = "Caregiver reports three awakenings last night."
        source_passage = "three awakenings last night"
        source_span_start = source_text.index(source_passage)
        payload = {
            "observation_kind": "caregiver_report",
            "epistemic_status": "reported",
            "subject_label": "person receiving care",
            "attribute": "sleep_awakenings",
            "value_text": "three",
            "source_passage": source_passage,
            "source_span_start": source_span_start,
            "source_span_end": source_span_start + len(source_passage),
            "extraction_method": "human_entered",
            "extraction_version": "test-v1",
        }
        invalid = client.post("/api/v1/care-continuity/evidence/observation-evidence/observations", json={
            **payload, "source_passage": "four awakenings last night",
        })
        assert invalid.status_code == 422

        created = client.post("/api/v1/care-continuity/evidence/observation-evidence/observations", json=payload)
        assert created.status_code == 201, created.text
        first = created.json()
        assert first["source_passage"] == payload["source_passage"]
        assert first["epistemic_status"] == "reported"
        assert first["source_span_end"] - first["source_span_start"] == len(payload["source_passage"])

        correction = client.post("/api/v1/care-continuity/evidence/observation-evidence/observations", json={
            **payload,
            "value_text": "two",
            "supersedes_observation_id": first["id"],
            "correction_rationale": "Caregiver corrected the count.",
        })
        assert correction.status_code == 201, correction.text
        second = correction.json()
        assert second["supersedes_observation_id"] == first["id"]
        assert second["correction_rationale"] == "Caregiver corrected the count."

        db = TestingSessionLocal()
        original = db.query(EvidenceObservation).filter(EvidenceObservation.id == first["id"]).one()
        with pytest.raises(ValueError, match="append-only"):
            original.value_text = "two"
            db.commit()
        db.rollback()
        assert db.query(EvidenceObservation).filter(EvidenceObservation.id == first["id"]).one().value_text == "three"
        db.close()
    finally:
        teardown_db()


def test_analyze_endpoint_uses_persisted_shared_origin_for_independence_count():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import EvidenceSourceRelationship

        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-lineage-analysis", name="Lineage Analysis Person")
        caregiver = Caregiver(
            id="caregiver-lineage-analysis",
            name="Lineage Analysis Caregiver",
            email="lineage-analysis@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = [
            Evidence(
                id="evidence-derived-a",
                person_id=person.id,
                type="derived_summary",
                source_text="Breakfast intake was low.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="derived_summary",
                evidence_status="verified",
            ),
            Evidence(
                id="evidence-derived-b",
                person_id=person.id,
                type="derived_summary",
                source_text="Breakfast consumption was limited.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="derived_summary",
                evidence_status="verified",
            ),
            Evidence(
                id="evidence-origin-root",
                person_id=person.id,
                type="caregiver_report",
                source_text="Original caregiver report about breakfast intake.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="caregiver_report",
                evidence_status="verified",
            ),
        ]
        db.add_all([person, caregiver, *evidence])
        db.flush()
        db.add_all([
            EvidenceSourceRelationship(
                person_id=person.id,
                source_evidence_id="evidence-derived-a",
                related_evidence_id="evidence-origin-root",
                relationship_type="derived_from",
                source_independence_status="shared_origin",
                origin_group_key="breakfast-origin-group",
                rationale="Derived summary references the original caregiver report.",
                reviewed_by_caregiver_id=caregiver.id,
            ),
            EvidenceSourceRelationship(
                person_id=person.id,
                source_evidence_id="evidence-derived-b",
                related_evidence_id="evidence-origin-root",
                relationship_type="derived_from",
                source_independence_status="shared_origin",
                origin_group_key="breakfast-origin-group",
                rationale="Second derived summary references the same original report.",
                reviewed_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client("person-lineage-analysis")
        response = client.post(
            "/api/v1/uncertainty/analyze",
            json={
                "person_id": "person-lineage-analysis",
                "subject_type": "daily_care",
                "subject_id": "breakfast",
                "field": "intake",
                "claim": "Two independent sources corroborate low breakfast intake.",
                "task": "summarize",
                "evidence_ids": ["evidence-derived-a", "evidence-derived-b"],
            },
        )
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["independent_evidence_record_count"] == 1
        assert analysis["duplicate_evidence_groups"][0]["reason"] == "shared_source_lineage"
        assert analysis["duplicate_evidence_groups"][0]["independent_corroboration"] is False
    finally:
        teardown_db()



def test_claim_dependency_graph_is_persisted_person_scoped_cycle_safe_and_append_only():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import CareAssertion, EvidenceClaimDependency
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="claim-dependency-person", name="Claim Dependency Person")
        caregiver = Caregiver(
            id="claim-dependency-caregiver", name="Claim Dependency Caregiver",
            email="claim-dependency@example.com", relationship="Family", person_id=person.id,
        )
        other_person = Person(id="claim-dependency-other", name="Other Person")
        other_caregiver = Caregiver(
            id="claim-dependency-other-caregiver", name="Other Caregiver",
            email="claim-dependency-other@example.com", relationship="Family", person_id=other_person.id,
        )
        claim_a = CareAssertion(
            id="claim-dependency-a", person_id=person.id, subject_type="sleep",
            subject_id="sleep-1", field="daytime_function", assertion_kind="inference",
            asserted_value="Daytime function may be affected", source_label="caregiver record",
            source_text="Daytime function may be affected", evidence_ids=[],
            assertion_status="unverified", created_by_caregiver_id=caregiver.id,
        )
        claim_b = CareAssertion(
            id="claim-dependency-b", person_id=person.id, subject_type="sleep",
            subject_id="sleep-1", field="night_waking", assertion_kind="report",
            asserted_value="Caregiver reported two awakenings", source_label="caregiver report",
            source_text="Caregiver reported two awakenings", evidence_ids=[],
            assertion_status="reported", created_by_caregiver_id=caregiver.id,
        )
        other_claim = CareAssertion(
            id="claim-dependency-private", person_id=other_person.id, subject_type="sleep",
            subject_id="sleep-1", field="night_waking", assertion_kind="report",
            asserted_value="Private report", source_label="other care record",
            source_text="Private report", evidence_ids=[],
            assertion_status="reported", created_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver, claim_a, claim_b, other_claim])
        db.commit()
        db.close()

        client = authorized_client("claim-dependency-person")
        created = client.post("/api/v1/care-continuity/claims/claim-dependency-a/dependencies", json={
            "depends_on_claim_id": "claim-dependency-b",
            "dependency_type": "derived_from",
            "rationale": "The interpretation depends on the reported night-waking observation.",
        })
        assert created.status_code == 201, created.text
        edge = created.json()
        assert edge["person_id"] == "claim-dependency-person"
        assert edge["created_by_caregiver_id"] == "claim-dependency-caregiver"

        outgoing = client.get("/api/v1/care-continuity/claims/claim-dependency-a/dependencies")
        incoming = client.get("/api/v1/care-continuity/claims/claim-dependency-b/dependencies")
        assert outgoing.status_code == incoming.status_code == 200
        assert any(item["id"] == edge["id"] and item["direction"] == "outgoing" for item in outgoing.json())
        assert any(item["id"] == edge["id"] and item["direction"] == "incoming" for item in incoming.json())

        duplicate = client.post("/api/v1/care-continuity/claims/claim-dependency-a/dependencies", json={
            "depends_on_claim_id": "claim-dependency-b",
            "dependency_type": "derived_from",
            "rationale": "Duplicate edge must not be silently added.",
        })
        assert duplicate.status_code == 409

        cycle = client.post("/api/v1/care-continuity/claims/claim-dependency-b/dependencies", json={
            "depends_on_claim_id": "claim-dependency-a",
            "dependency_type": "requires",
            "rationale": "This edge would close a dependency cycle.",
        })
        assert cycle.status_code == 422
        cross_person = client.post("/api/v1/care-continuity/claims/claim-dependency-a/dependencies", json={
            "depends_on_claim_id": "claim-dependency-private",
            "dependency_type": "derived_from",
            "rationale": "Cross-person claims must be rejected.",
        })
        assert cross_person.status_code == 404

        audit_db = TestingSessionLocal()
        try:
            persisted = audit_db.query(EvidenceClaimDependency).filter_by(id=edge["id"]).one()
            persisted.rationale = "attempted rewrite"
            with pytest.raises(ValueError, match="append-only"):
                audit_db.commit()
            audit_db.rollback()
        finally:
            audit_db.close()
    finally:
        teardown_db()


def test_safety_response_validation_rejects_unpersisted_evidence_ids():
    """Evidence IDs supplied to safety validation must resolve inside the bearer-scoped record."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="safety-person-1", name="Safety Person")
        caregiver = Caregiver(
            id="safety-caregiver-1",
            name="Safety Caregiver",
            email="safety-caregiver@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        response = client.post(
            "/api/v1/validate-response",
            params={
                "candidate_response": "The record confirms the medication was changed.",
                "risk_class": "informational",
            },
            json={
                "claims": ["The medication was changed."],
                "evidence_ids": ["not-a-persisted-evidence-id"],
            },
        )
        assert response.status_code == 404, response.text
        assert "not found in this care record" in response.json()["detail"]
    finally:
        teardown_db()


def test_safety_audit_history_rejects_cross_record_person_id():
    """Audit history cannot be read by swapping a person ID in the URL."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="safety-person-own", name="Own Person")
        other_person = Person(id="safety-person-other", name="Other Person")
        caregiver = Caregiver(
            id="safety-caregiver-own",
            name="Own Caregiver",
            email="safety-caregiver-own@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, other_person, caregiver])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        response = client.get("/api/v1/safety/audit/safety-person-other")
        assert response.status_code == 404
    finally:
        teardown_db()
