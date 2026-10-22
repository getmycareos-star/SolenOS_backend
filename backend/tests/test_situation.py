from datetime import datetime, timezone, timedelta
import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Person, Caregiver
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


def create_auth_headers(db, person_id: str, caregiver_id: str):
    token = secrets.token_urlsafe(32)
    db.add(
        AnonymousSession(
            person_id=person_id,
            caregiver_id=caregiver_id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def test_post_situation_creates_evidence_and_event():
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
        headers = create_auth_headers(db, person.id, caregiver.id)
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": "caregiver-1",
                "person_id": "person-1",
                "raw_input": "Patient seemed more confused than usual today.",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["care_key"] == "caregiver-1"
        assert data["person_id"] == "person-1"
        assert data["message"] == "Care input recorded successfully."
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
        headers = create_auth_headers(db, person.id, caregiver.id)
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": "caregiver-2",
                "person_id": "person-2",
                "raw_input": "",
                "documents": [
                    {
                        "id": "doc-1",
                        "name": "discharge_summary.pdf",
                        "extracted_text": "Patient cleared for discharge with follow-up in 1 week.",
                        "mime_type": "application/pdf",
                        "ocr_confidence": 0.95,
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
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Document observation"
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
        headers = create_auth_headers(db, person.id, caregiver.id)
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": "caregiver-4",
                "person_id": "person-4",
                "raw_input": "   ",
                "documents": [],
            },
        )
        assert response.status_code == 400
        assert "No valid evidence provided" in response.json()["detail"]
    finally:
        teardown_db()
