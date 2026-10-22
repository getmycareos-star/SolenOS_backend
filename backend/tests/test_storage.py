from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.database as database
import app.main as main
import app.api.auth as auth
import app.api.v1 as api_v1
import app.services.storage as storage
from app.core.database import Base
from app.models.auth import AuthAccount
from app.models.care import Caregiver, Evidence, Person


def test_storage_fails_closed_when_private_bucket_is_not_configured(monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="",
            supabase_service_role_key="",
            supabase_storage_bucket="",
        ),
    )

    with pytest.raises(HTTPException) as error:
        storage.upload_private_object("photo.jpg", "image/jpeg", b"image")

    assert error.value.status_code == 503
    assert "not configured" in error.value.detail


def test_storage_upload_uses_private_bucket_and_service_key(monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://project.supabase.co/",
            supabase_service_role_key="service-role-test-secret",
            supabase_storage_bucket="care-evidence",
        ),
    )
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["api_key"] = request.get_header("Apikey")
        captured["content_type"] = request.get_header("Content-type")
        captured["method"] = request.get_method()
        captured["timeout"] = timeout
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr(storage, "urlopen", fake_urlopen)
    storage.upload_private_object("opaque-photo-id.jpg", "image/jpeg", b"image-bytes")

    assert captured["url"] == "https://project.supabase.co/storage/v1/object/care-evidence/opaque-photo-id.jpg"
    assert captured["authorization"] == "Bearer service-role-test-secret"
    assert captured["api_key"] == "service-role-test-secret"
    assert captured["content_type"] == "image/jpeg"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 20
    assert captured["data"] == b"image-bytes"


def test_authenticated_photo_upload_persists_evidence_and_actor(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    secret = "test-only-private-photo-upload-secret-at-least-32-bytes"
    monkeypatch.setattr(main.settings, "secret_key", secret)
    monkeypatch.setattr(auth.settings, "secret_key", secret)
    monkeypatch.setattr(database, "SessionLocal", testing_session)
    monkeypatch.setattr(main, "SessionLocal", testing_session)
    main.app.dependency_overrides.clear()
    uploaded = {}

    def fake_upload(object_path, content_type, data):
        uploaded.update(path=object_path, content_type=content_type, data=data)

    monkeypatch.setattr(api_v1, "upload_private_object", fake_upload)

    db = testing_session()
    try:
        person = Person(id="photo-person", name="Photo Person")
        caregiver = Caregiver(
            id="photo-caregiver",
            name="Photo Caregiver",
            email="photo-caregiver@example.invalid",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        account = AuthAccount(
            id="photo-account",
            caregiver_id=caregiver.id,
            email=caregiver.email,
            password_hash="test-only",
            role="caregiver",
            is_active=True,
        )
        db.add_all([person, caregiver, account])
        db.commit()
        token = auth.issue_token(account)
    finally:
        db.close()

    client = TestClient(main.app)
    try:
        response = client.post(
            "/api/v1/upload?person_id=photo-person&caregiver_id=photo-caregiver",
            headers={"Authorization": f"Bearer {token}"},
            data={"person_id": "photo-person", "caregiver_id": "photo-caregiver"},
            files={"file": ("medication.jpg", b"\\xff\\xd8\\xffimage-bytes", "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        evidence_id = response.json()["evidence_id"]
        assert uploaded["content_type"] == "image/jpeg"
        assert uploaded["data"] == b"\\xff\\xd8\\xffimage-bytes"
        assert "/" not in uploaded["path"]
        assert "photo-person" not in uploaded["path"]

        db = testing_session()
        try:
            evidence = db.query(Evidence).filter_by(id=evidence_id).one()
            assert evidence.person_id == "photo-person"
            assert evidence.uploaded_by_caregiver_id == "photo-caregiver"
            assert evidence.original_file_path == uploaded["path"]
            assert evidence.source_type == "caregiver_uploaded_image"
            assert evidence.evidence_status == "reported"
            assert evidence.confidence is None
        finally:
            db.close()
    finally:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
