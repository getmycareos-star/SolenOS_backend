from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver

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

        client = TestClient(app)
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

        client = TestClient(app)
        
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
        db.commit()
        db.close()

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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
        db.commit()
        db.close()

        client = TestClient(app)
        
        client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "name",
                "epistemic_state": "known",
                "known_value": "Metformin",
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
        db.commit()
        db.close()

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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

        client = TestClient(app)
        
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
