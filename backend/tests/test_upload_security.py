import hashlib

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
        first = test_client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "First care recipient", "caregiver_name": "First caregiver"},
        )
        second = test_client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Second care recipient", "caregiver_name": "Second caregiver"},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        yield test_client, first.json(), second.json()
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def test_original_upload_is_persisted_with_digest_and_downloaded_byte_for_byte(client):
    test_client, first, _ = client
    token = first["access_token"]
    original = b"Caregiver note: breakfast was mostly uneaten.\n"
    response = test_client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "evidence_type": "document",
            "extracted_text": "Caregiver-reported source text",
        },
        files={"file": ("care-note.txt", original, "text/plain")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["persisted"] is True
    assert body["sha256"] == hashlib.sha256(original).hexdigest()

    downloaded = test_client.get(
        f"/api/v1/upload/{body['evidence_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == original
    assert downloaded.headers["content-disposition"].startswith("attachment;")


def test_original_upload_cannot_be_downloaded_from_another_care_record(client):
    test_client, first, second = client
    original = b"Private care evidence"
    uploaded = test_client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        data={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "evidence_type": "document",
            "extracted_text": "Private caregiver-reported note",
        },
        files={"file": ("private.txt", original, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    denied = test_client.get(
        f"/api/v1/upload/{uploaded.json()['evidence_id']}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert denied.status_code == 404, denied.text


def test_upload_rejects_spoofed_image_signature(client):
    test_client, first, _ = client
    response = test_client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        data={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "evidence_type": "care_photo",
        },
        files={"file": ("scan.png", b"not a PNG file", "image/png")},
    )
    assert response.status_code == 422, response.text
    assert "contents do not match" in response.json()["detail"]
