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
from app.models.anonymous_session import AnonymousSession

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-5")
        
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
        
        response = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "closed",
                "answer": "Yes, patient attended and follow-up was discussed",
                "answer_provenance": "Note from cardiology visit",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["answer"] == "Yes, patient attended and follow-up was discussed"
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
        assert [event["new_status"] for event in events] == ["resolved", "reopened"]
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
