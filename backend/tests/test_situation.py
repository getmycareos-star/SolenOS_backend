import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.schemas.situation import SituationInput
from app.services.situation import process_situation_input

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


app.dependency_overrides = {}


def setup_db():
    Base.metadata.create_all(bind=engine)
    main_module.SessionLocal = TestingSessionLocal


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal


def create_authenticated_session(client, person_name: str):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": person_name, "caregiver_name": "Test family caregiver"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


def test_post_situation_creates_evidence_and_event():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Test Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "Patient seemed more confused than usual today.",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["care_key"] == caregiver_id
        assert data["person_id"] == person_id
        assert data["message"] == "Care input recorded successfully."
        assert data["analysis_status"] == "complete"
        assert data["analysis_warning"] is None
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Patient seemed more confused than usual today."
        assert data["evidence_id"] is not None
        assert data["care_event_id"] is not None
    finally:
        teardown_db()


def test_post_situation_with_documents():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Test Person 2")
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": created["caregiver"]["id"],
                "person_id": created["person"]["id"],
                "raw_input": "",
                "documents": [
                    {
                        "id": "doc-1",
                        "name": "discharge_summary.pdf",
                        "extracted_text": "Patient cleared for discharge with follow-up in 1 week.",
                        "mime_type": "application/pdf",
                        "ocr_confidence": 0.95,
                        "extraction_source": "pdf_ocr",
                        "extraction_warnings": ["Low OCR confidence; verify against original."],
                        "sha256": "a" * 64,
                        "extracted_char_count": 54,
                    }
                ],
                "provenance": {
                    "input_type": "document",
                    "entry_method": "upload",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "recognition_confidence": 0.95,
                    "transcript_uncertain": False,
                },
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Document observation"
        evidence_db = TestingSessionLocal()
        stored = evidence_db.query(Evidence).filter_by(id=data["evidence_id"]).first()
        metadata = json.loads(stored.extra_metadata)
        assert metadata["extraction_source"] == "pdf_ocr"
        assert metadata["sha256"] == "a" * 64
        assert metadata["ocr_confidence"] == 0.95
        assert metadata["extraction_warnings"] == ["Low OCR confidence; verify against original."]
        assert metadata["extracted_char_count"] == 54
        assert stored.source_type == "document"
        assert stored.confidence == 0.95
        evidence_db.close()

        hydrated = client.get(
            f"/api/v1/evidence/{created['person']['id']}",
            headers=headers,
        )
        assert hydrated.status_code == 200, hydrated.text
        saved_document = hydrated.json()[0]
        assert saved_document["extra_metadata"]["extraction_source"] == "pdf_ocr"
        assert saved_document["extra_metadata"]["sha256"] == "a" * 64
        assert saved_document["source_type"] == "document"
    finally:
        teardown_db()


def test_situation_service_rejects_missing_caregiver():
    setup_db()
    try:
        db = TestingSessionLocal()
        db.add(Person(id="person-1", name="Test Person"))
        db.commit()
        with pytest.raises(ValueError, match="Caregiver not found"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id="nonexistent",
                    person_id="person-1",
                    raw_input="Some input",
                ),
            )
        db.close()
    finally:
        teardown_db()


def test_situation_service_rejects_missing_person():
    setup_db()
    try:
        db = TestingSessionLocal()
        caregiver = Caregiver(
            id="caregiver-3",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id="person-3",
        )
        db.add(caregiver)
        db.commit()
        with pytest.raises(ValueError, match="Person not found"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id="caregiver-3",
                    person_id="nonexistent",
                    raw_input="Some input",
                ),
            )
        db.close()
    finally:
        teardown_db()


def test_post_situation_empty_input_and_no_documents():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Test Person 4")
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": created["caregiver"]["id"],
                "person_id": created["person"]["id"],
                "raw_input": "   ",
                "documents": [],
            },
        )
        assert response.status_code == 400, response.text
        assert "No valid evidence provided" in response.json()["detail"]
    finally:
        teardown_db()



def test_analysis_failure_is_reported_as_partial_after_evidence_is_saved(monkeypatch):
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="partial-person", name="Partial Person")
        caregiver = Caregiver(
            id="partial-caregiver",
            name="Partial Caregiver",
            email="partial@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        def fail_analysis(*args, **kwargs):
            raise RuntimeError("internal analysis failure")

        monkeypatch.setattr("app.services.situation.detect_event_changes", fail_analysis)
        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                raw_input="Breakfast was mostly uneaten.",
            ),
        )

        assert response.ok is True
        assert response.analysis_status == "partial"
        assert response.message == "Care input recorded; analysis is incomplete."
        assert response.analysis_warning
        assert "internal analysis failure" not in response.analysis_warning
        assert db.query(Evidence).filter_by(person_id=person.id).count() == 1
        assert db.query(CareEvent).filter_by(person_id=person.id).count() == 1
        assert response.situations is None
        db.close()
    finally:
        teardown_db()


def test_invalid_observation_timestamp_is_rejected_not_replaced_with_now():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="invalid-time-person", name="Invalid Time Person")
        caregiver = Caregiver(
            id="invalid-time-caregiver",
            name="Invalid Time Caregiver",
            email="invalid-time@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        with pytest.raises(ValueError, match="Invalid timestamp"):
            process_situation_input(
                db,
                SituationInput(
                    caregiver_id=caregiver.id,
                    person_id=person.id,
                    raw_input="A reported care observation.",
                    timestamp="not-a-timestamp",
                ),
            )

        assert db.query(Evidence).filter_by(person_id=person.id).count() == 0
        assert db.query(CareEvent).filter_by(person_id=person.id).count() == 0
        db.close()
    finally:
        teardown_db()
