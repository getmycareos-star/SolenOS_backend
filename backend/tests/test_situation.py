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


@pytest.mark.session_security
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


@pytest.mark.session_security
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


@pytest.mark.session_security
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


@pytest.mark.session_security
def test_lcr_orientation_is_qwen_generated_read_only_and_record_grounded(monkeypatch):
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        created, headers = create_authenticated_session(client, "LCR Orientation Person")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]

        saved = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": caregiver_id,
                "person_id": person_id,
                "raw_input": "The person slept poorly last night.",
            },
        )
        assert saved.status_code == 200, saved.text
        before_count = TestingSessionLocal().query(CareEvent).filter_by(person_id=person_id).count()

        captured = {}
        monkeypatch.setattr(main_module, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
        def fake_qwen(base, model, messages, options=None):
            captured["base"] = base
            captured["model"] = model
            captured["messages"] = messages
            return "The record notes poor sleep last night. What has changed since then?"
        monkeypatch.setattr(main_module, "_ollama_chat", fake_qwen)

        response = client.post(
            "/api/lcr/orientation",
            headers=headers,
            json={"raw_input": "Hi"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reply"] == "The record notes poor sleep last night. What has changed since then?"
        assert body["ai_generated"] is True
        assert body["care_record_unchanged"] is True
        assert body["companion_history_unchanged"] is True
        assert body["written_to_lcr"] is False
        prompt = captured["messages"][0]["content"]
        assert "slept poorly last night" in prompt
        assert "Do not use canned replies" in prompt
        after_count = TestingSessionLocal().query(CareEvent).filter_by(person_id=person_id).count()
        assert after_count == before_count

        unauthorized = client.post("/api/lcr/orientation", json={"raw_input": "Hi"})
        assert unauthorized.status_code == 401
    finally:
        teardown_db()


def test_lcr_interaction_only_classifier_includes_insults_without_response_templates():
    assert main_module._is_casual_conversation_only("Hi")
    assert main_module._is_casual_conversation_only("This thing is useless.")
    assert main_module._is_casual_conversation_only("You never get anything right")
    assert not main_module._is_casual_conversation_only("Mom fell last night")
