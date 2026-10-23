import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.ops_event import OpsEvent

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def authenticated_client():
    client = TestClient(app)
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": "API route test", "caregiver_name": "API route caregiver"},
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    client.headers.update(headers)
    return client, headers


def test_api_situation_route_is_mounted_and_rejects_unauthenticated_access():
    client = TestClient(app)
    response = client.get("/api/situation")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]


def test_extract_route_requires_session_and_returns_valid_json_for_text_upload():
    client = TestClient(app)
    unauthenticated = client.post(
        "/api/extract",
        files={"file": ("care-note.txt", b"Care recipient ate breakfast.", "text/plain")},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["content-type"].startswith("application/json")

    client, headers = authenticated_client()
    response = client.post(
        "/api/extract",
        headers=headers,
        files={"file": ("care-note.txt", b"Care recipient ate breakfast.", "text/plain")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["document"]["extracted_text"] == "Care recipient ate breakfast."
    assert payload["document"]["extraction_source"] == "text"
    assert payload["document"]["ocr_confidence"] is None


def test_extract_route_reports_unsupported_image_as_json_not_false_ocr_success():
    client, headers = authenticated_client()
    response = client.post(
        "/api/extract",
        headers=headers,
        files={"file": ("scan.jpg", b"not-an-image", "image/jpeg")},
    )
    assert response.status_code == 415
    assert response.headers["content-type"].startswith("application/json")
    assert "OCR" in response.json()["detail"]


def test_track_route_persists_only_allowlisted_operational_metadata_and_hashes_ids():
    client = TestClient(app)
    response = client.post(
        "/api/track",
        json={
            "user_id": "test-user-123",
            "session_id": "test-session-456",
            "event_name": "input_submitted",
            "metadata": {
                "input_type": "text",
                "has_documents": False,
                "error_message": "This may contain sensitive care details",
                "unapproved_field": "do not store",
            },
        },
    )
    assert response.status_code == 202, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is True

    db = TestSessionLocal()
    try:
        row = db.query(OpsEvent).one()
        assert row.user_id_hash == hashlib.sha256(b"test-user-123").hexdigest()
        assert row.session_id_hash == hashlib.sha256(b"test-session-456").hexdigest()
        assert row.metadata_json == {"input_type": "text", "has_documents": False}
        assert "test-user-123" not in row.user_id_hash
        assert "error_message" not in row.metadata_json
    finally:
        db.close()


def test_track_rejects_unregistered_events_and_wrong_http_method():
    client = TestClient(app)
    response = client.post(
        "/api/track",
        json={"session_id": "session", "event_name": "unknown_event", "metadata": {}},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    wrong_method = client.get("/api/track")
    assert wrong_method.status_code == 405
    assert wrong_method.headers["content-type"].startswith("application/json")
