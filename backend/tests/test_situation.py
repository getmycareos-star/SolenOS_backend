from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_authenticated_context(client: TestClient, suffix: str):
    email = f"situation-{suffix}@example.com"
    password = "a-long-test-password-2026"
    signup = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "display_name": "Test Caregiver"},
    )
    assert signup.status_code == 201, signup.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    context = client.post(
        "/api/v1/auth/care-contexts",
        headers=headers,
        json={"name": f"Test Care Recipient {suffix}"},
    )
    assert context.status_code == 201, context.text
    links = client.get("/api/v1/auth/my-caregiver-links", headers=headers)
    assert links.status_code == 200, links.text
    caregiver = next(item for item in links.json() if item["person_id"] == context.json()["person_id"])
    return headers, context.json()["person_id"], caregiver["caregiver_id"]


def test_post_situation_creates_evidence_and_event():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_authenticated_context(client, "basic")
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
            assert data["analysis_status"] in {"complete", "failed"}
            assert len(data["events"]) == 1
            assert data["events"][0]["title"] == "Patient seemed more confused than usual today."
            assert data["evidence_id"] is not None
            assert data["care_event_id"] is not None
    finally:
        teardown_db()



def test_quick_capture_persists_category_and_distinguishes_event_time_from_entry_time():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_authenticated_context(client, "quick-capture-time")
            event_time = "2026-10-20T08:30:00+00:00"
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": caregiver_id,
                    "person_id": person_id,
                    "raw_input": "Caregiver observed restless sleep.",
                    "capture_category": "sleep",
                    "timestamp": event_time,
                    "provenance": {"input_type": "caregiver_observation", "entry_method": "quick_entry", "captured_at": datetime.now(timezone.utc).isoformat()},
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["events"][0]["time_provenance"] == "caregiver_reported_event_time"
            assert data["events"][0]["occurred_at"].startswith("2026-10-20T08:30:00")
            with TestingSessionLocal() as db:
                from app.models.care import CareEvent
                event = db.query(CareEvent).filter_by(id=data["care_event_id"]).one()
                assert "capture_category:sleep" in event.tags
                assert event.time_provenance == "caregiver_reported_event_time"
    finally:
        teardown_db()


def test_quick_capture_without_event_time_marks_entry_time_fallback():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_authenticated_context(client, "quick-capture-default-time")
            response = client.post(
                "/api/situation",
                headers=headers,
                json={"caregiver_id": caregiver_id, "person_id": person_id, "raw_input": "Ate breakfast.", "capture_category": "meal"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["events"][0]["time_provenance"] == "entry_time_defaulted"
    finally:
        teardown_db()

def test_post_situation_with_documents():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_authenticated_context(client, "documents")
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": caregiver_id,
                    "person_id": person_id,
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


def test_post_situation_rejects_unknown_caregiver():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, _ = create_authenticated_context(client, "missing-caregiver")
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": "nonexistent-caregiver",
                    "person_id": person_id,
                    "raw_input": "Some input",
                },
            )
            assert response.status_code == 400
            assert "Caregiver not found" in response.json()["detail"]
    finally:
        teardown_db()


def test_post_situation_rejects_ungranted_person():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, _, caregiver_id = create_authenticated_context(client, "wrong-person")
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": caregiver_id,
                    "person_id": "person-not-granted-to-this-account",
                    "raw_input": "Some input",
                },
            )
            # Unknown or ungranted care-record identifiers must be rejected before any write.
            assert response.status_code in {400, 403}
            with TestingSessionLocal() as db:
                from app.models.care import CareEvent, Evidence
                assert db.query(CareEvent).filter_by(person_id="person-not-granted-to-this-account").count() == 0
                assert db.query(Evidence).filter_by(person_id="person-not-granted-to-this-account").count() == 0
    finally:
        teardown_db()


def test_post_situation_empty_input_and_no_documents():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_authenticated_context(client, "empty")
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": caregiver_id,
                    "person_id": person_id,
                    "raw_input": "   ",
                    "documents": [],
                },
            )
            assert response.status_code == 400
            assert "No valid evidence provided" in response.json()["detail"]
    finally:
        teardown_db()



def test_post_situation_returns_persisted_qwen_analysis_id(monkeypatch):
    from app.services import situation as situation_service

    setup_db()
    try:
        monkeypatch.setattr(
            situation_service,
            "analyze_saved_care_event",
            lambda db, event_id: {
                "analysis_status": "complete",
                "analysis_error": None,
                "what_changed": None,
                "attention_candidate": False,
                "follow_up_candidate": False,
                "situations": [],
                "qwen_extraction_status": "pending_review",
                "qwen_candidates": {"observations": []},
                "qwen_analysis_id": "persisted-analysis-123",
            },
        )
        with TestClient(app) as client:
            headers, person_id, caregiver_id = create_authenticated_context(client, "analysis-id")
            response = client.post(
                "/api/situation",
                headers=headers,
                json={
                    "caregiver_id": caregiver_id,
                    "person_id": person_id,
                    "raw_input": "The appointment was moved to Thursday.",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["qwen_extraction_status"] == "pending_review"
            assert data["qwen_analysis_id"] == "persisted-analysis-123"
    finally:
        teardown_db()
