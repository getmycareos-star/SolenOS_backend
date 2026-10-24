from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.auth as auth
import app.api.v1 as api_v1
import app.core.database as database
import app.main as main
from app.core.database import Base
from app.models.auth import AuthAccount
from app.models.care import Caregiver, Evidence, Person


def test_authenticated_upload_persists_original_metadata_and_denies_cross_recipient_access(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    secret = "test-only-upload-isolation-secret-at-least-32-bytes"
    monkeypatch.setattr(main.settings, "secret_key", secret)
    monkeypatch.setattr(auth.settings, "secret_key", secret)
    monkeypatch.setattr(database, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(main, "SessionLocal", TestingSessionLocal)
    stored_objects = {}

    def store_object(path, content_type, data):
        stored_objects[path] = {"content_type": content_type, "bytes": data}

    monkeypatch.setattr(api_v1, "upload_private_object", store_object)
    monkeypatch.setattr(api_v1, "delete_private_object", lambda path: stored_objects.pop(path, None))
    monkeypatch.setattr(api_v1, "create_signed_object_url", lambda path: f"https://signed.invalid/{path}")
    main.app.dependency_overrides.clear()

    db = TestingSessionLocal()
    try:
        person_a = Person(id="upload-person-a", name="Upload Test Person A")
        person_b = Person(id="upload-person-b", name="Upload Test Person B")
        caregiver_a = Caregiver(
            id="upload-caregiver-a",
            name="Upload Test Caregiver A",
            email="upload-caregiver-a@example.invalid",
            relationship="Family",
            person_id=person_a.id,
        )
        caregiver_b = Caregiver(
            id="upload-caregiver-b",
            name="Upload Test Caregiver B",
            email="upload-caregiver-b@example.invalid",
            relationship="Family",
            person_id=person_b.id,
        )
        db.add_all([person_a, person_b, caregiver_a, caregiver_b])
        db.flush()
        account = AuthAccount(
            id="upload-account-a",
            caregiver_id=caregiver_a.id,
            email=caregiver_a.email,
            password_hash="test-only",
            role="caregiver",
            is_active=True,
        )
        db.add(account)
        db.commit()
        token = auth.issue_token(account)
    finally:
        db.close()

    client = TestClient(main.app)
    headers = {"Authorization": f"Bearer {token}"}
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"test-only-png-payload"
    try:
        denied = client.post(
            "/api/v1/upload",
            headers=headers,
            data={"person_id": "upload-person-b", "caregiver_id": "upload-caregiver-a"},
            files={"file": ("evidence.png", image_bytes, "image/png")},
        )
        assert denied.status_code == 403, denied.text
        assert stored_objects == {}

        uploaded = client.post(
            "/api/v1/upload",
            headers=headers,
            data={"person_id": "upload-person-a", "caregiver_id": "upload-caregiver-a"},
            files={"file": ("evidence.png", image_bytes, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]

        db = TestingSessionLocal()
        try:
            evidence = db.query(Evidence).filter_by(id=evidence_id).one()
            assert evidence.person_id == "upload-person-a"
            assert evidence.uploaded_by_caregiver_id == "upload-caregiver-a"
            assert evidence.source_type == "caregiver_uploaded_image"
            assert evidence.confidence is None
            object_path = evidence.original_file_path
            assert stored_objects[object_path]["bytes"] == image_bytes
            assert stored_objects[object_path]["content_type"] == "image/png"
        finally:
            db.close()

        signed_url = client.get(
            f"/api/v1/evidence/upload-person-a/{evidence_id}/signed-url",
            headers=headers,
        )
        assert signed_url.status_code == 200, signed_url.text
        assert signed_url.json() == {
            "url": f"https://signed.invalid/{object_path}",
            "expires_in": 300,
        }

        cross_recipient_url = client.get(
            f"/api/v1/evidence/upload-person-b/{evidence_id}/signed-url",
            headers=headers,
        )
        assert cross_recipient_url.status_code == 404, cross_recipient_url.text
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
