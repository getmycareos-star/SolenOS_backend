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
        assert data["events"][0]["time_provenance"] == "entry_time_defaulted"
        assert data["evidence_id"] is not None
        assert data["care_event_id"] is not None
    finally:
        teardown_db()



@pytest.mark.session_security
def test_post_situation_preserves_caregiver_reported_event_time_provenance():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        client = TestClient(app)
        created, headers = create_authenticated_session(client, "Reported Event Time Person")
        event_time = "2026-10-20T08:30:00+00:00"
        response = client.post(
            "/api/situation",
            headers=headers,
            json={
                "caregiver_id": created["caregiver"]["id"],
                "person_id": created["person"]["id"],
                "raw_input": "Caregiver reports restless sleep.",
                "timestamp": event_time,
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["events"][0]["occurred_at"].startswith("2026-10-20T08:30:00")
        assert data["events"][0]["time_provenance"] == "caregiver_reported_event_time"

        db = TestingSessionLocal()
        try:
            saved = db.query(CareEvent).filter_by(id=data["care_event_id"]).one()
            assert saved.time_provenance == "caregiver_reported_event_time"
            assert saved.occurred_at.isoformat().startswith("2026-10-20T08:30:00")
        finally:
            db.close()
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
    assert main_module._is_casual_conversation_only("You are dumb")
    assert not main_module._is_casual_conversation_only("Mom fell last night")


def test_quick_capture_persists_category_and_caregiver_reported_event_time():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        with TestClient(app) as client:
            created, headers = create_authenticated_session(client, "Quick Capture Reported Time")
            event_time = "2026-10-20T08:30:00+00:00"
            original_report = "Caregiver observed restless sleep."
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": created["caregiver"]["id"],
                    "person_id": created["person"]["id"],
                    "raw_input": original_report,
                    "capture_category": "sleep",
                    "timestamp": event_time,
                    "provenance": {
                        "input_type": "caregiver_observation",
                        "entry_method": "quick_entry",
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["events"][0]["time_provenance"] == "caregiver_reported_event_time"
            assert data["events"][0]["occurred_at"].startswith("2026-10-20T08:30:00")
            with TestingSessionLocal() as db:
                event = db.query(CareEvent).filter_by(id=data["care_event_id"]).one()
                evidence = db.query(Evidence).filter_by(id=data["evidence_id"]).one()
                assert "capture_category:sleep" in event.tags
                assert event.time_provenance == "caregiver_reported_event_time"
                assert evidence.source_text == original_report
    finally:
        teardown_db()


def test_quick_capture_without_event_time_marks_entry_time_fallback():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="default-time-person", name="Default Time Person")
        caregiver = Caregiver(
            id="default-time-caregiver",
            name="Default Time Caregiver",
            email="default-time@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                raw_input="Ate breakfast.",
                capture_category="meal",
            ),
        )
        assert response.events[0].time_provenance == "entry_time_defaulted"
        event = db.query(CareEvent).filter_by(id=response.care_event_id).one()
        assert "capture_category:meal" in event.tags
    finally:
        db.close()
        teardown_db()


def test_quick_capture_rejects_invalid_event_timestamp_without_saving():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="bad-time-person", name="Bad Time Person")
        caregiver = Caregiver(
            id="bad-time-caregiver",
            name="Bad Time Caregiver",
            email="bad-time@example.com",
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
                    capture_category="symptom",
                    timestamp="yesterday morning",
                ),
            )
        assert db.query(Evidence).filter_by(person_id=person.id).count() == 0
        assert db.query(CareEvent).filter_by(person_id=person.id).count() == 0
    finally:
        db.close()
        teardown_db()


def test_quick_capture_rejects_unsupported_category():
    with pytest.raises(Exception):
        SituationInput(
            caregiver_id="caregiver",
            person_id="person",
            raw_input="Observed a change.",
            capture_category="unsupported-category",
        )


def test_document_evidence_preserves_extraction_provenance_without_inventing_confidence():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="document-provenance-person", name="Document Provenance Person")
        caregiver = Caregiver(
            id="document-provenance-caregiver",
            name="Document Provenance Caregiver",
            email="document-provenance@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                documents=[
                    {
                        "id": "scan-1",
                        "name": "discharge-scan.png",
                        "extracted_text": "Follow up with cardiology in two weeks.",
                        "mime_type": "image/png",
                        "extraction_source": "ocr",
                        "ocr_confidence": 0.87,
                    }
                ],
                provenance={
                    "input_type": "document",
                    "entry_method": "upload",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                },
            ),
        )

        evidence = db.query(Evidence).filter_by(id=response.evidence_id).one()
        metadata = __import__("json").loads(evidence.extra_metadata)
        assert evidence.source_type == "document_ocr"
        assert evidence.confidence == 0.87
        assert metadata["extraction_source"] == "ocr"
        assert metadata["mime_type"] == "image/png"
        assert metadata["document_time_status"] == "not_extracted"
        assert metadata["document_id"] == "scan-1"
    finally:
        db.close()
        teardown_db()


def test_document_without_ocr_score_keeps_confidence_unknown():
    setup_db()
    db = TestingSessionLocal()
    try:
        person = Person(id="document-unknown-confidence-person", name="Document Unknown Confidence")
        caregiver = Caregiver(
            id="document-unknown-confidence-caregiver",
            name="Document Unknown Confidence Caregiver",
            email="document-unknown-confidence@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()

        response = process_situation_input(
            db,
            SituationInput(
                caregiver_id=caregiver.id,
                person_id=person.id,
                documents=[
                    {
                        "id": "pdf-1",
                        "name": "discharge.pdf",
                        "extracted_text": "Follow up in two weeks.",
                        "mime_type": "application/pdf",
                        "extraction_source": "tika",
                    }
                ],
            ),
        )
        evidence = db.query(Evidence).filter_by(id=response.evidence_id).one()
        metadata = __import__("json").loads(evidence.extra_metadata)
        assert evidence.source_type == "document_text_extraction"
        assert evidence.confidence is None
        assert metadata["ocr_confidence"] is None
        assert metadata["document_time_status"] == "not_extracted"
    finally:
        db.close()
        teardown_db()
