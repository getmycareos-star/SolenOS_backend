import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app

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


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Care recipient", "caregiver_name": "Family caregiver"},
        )
        assert created.status_code == 201, created.text
        yield test_client, created.json()["access_token"]
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def test_document_extraction_requires_a_valid_session(client):
    test_client, _ = client
    response = test_client.post(
        "/api/extract",
        files={"file": ("care-note.txt", b"Care note", "text/plain")},
    )
    assert response.status_code == 401, response.text


def test_document_extraction_reads_utf8_text_with_session(client):
    test_client, token = client
    original_text = "Caregiver observation: breakfast was mostly uneaten."
    response = test_client.post(
        "/api/extract",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("care-note.txt", original_text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["document"]["extracted_text"] == original_text
    assert body["document"]["extraction_source"] == "text"
    assert body["document"]["ocr_confidence"] is None


def test_document_extraction_rejects_scanned_image_without_ocr(client):
    test_client, token = client
    response = test_client.post(
        "/api/extract",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("scan.png", b"not a real png", "image/png")},
    )
    assert response.status_code == 415, response.text
    assert "OCR" in response.json()["detail"]
