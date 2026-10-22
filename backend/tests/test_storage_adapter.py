from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.storage as storage
import app.api.v1 as api_v1
from app.core.database import Base, get_db
from app.api.authentication import current_account
from app.main import app
from app.models.auth_identity import AuthAccount, AccountPersonAccess, CaregiverAccountLink
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
        storage.upload_private_object("opaque-photo.jpg", "image/jpeg", b"image")

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


def test_signed_url_includes_supabase_storage_api_prefix(monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://project.supabase.co",
            supabase_service_role_key="service-role-test-secret",
            supabase_storage_bucket="care-evidence",
        ),
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"signedURL":"/object/sign/care-evidence/opaque-photo.jpg?token=short-lived"}'

    def fake_urlopen(request, timeout):
        assert request.full_url == (
            "https://project.supabase.co/storage/v1/object/sign/"
            "care-evidence/opaque-photo.jpg"
        )
        assert request.get_method() == "POST"
        assert timeout == 15
        return FakeResponse()

    monkeypatch.setattr(storage, "urlopen", fake_urlopen)
    url = storage.create_signed_object_url("opaque-photo.jpg")

    assert url == (
        "https://project.supabase.co/storage/v1/object/sign/"
        "care-evidence/opaque-photo.jpg?token=short-lived"
    )



def test_photo_upload_requires_access_and_persists_evidence(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    uploaded = {}

    def fake_upload(object_path, content_type, data):
        uploaded.update(path=object_path, content_type=content_type, data=data)

    monkeypatch.setattr(api_v1, "upload_private_object", fake_upload)
    monkeypatch.setattr(api_v1, "delete_private_object", lambda _path: None)

    db = testing_session()
    try:
        person = Person(id="storage-route-person", name="Storage Route Person")
        caregiver = Caregiver(
            id="storage-route-caregiver",
            name="Storage Route Caregiver",
            email="storage-route@example.invalid",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        account = AuthAccount(
            id="storage-route-account",
            email="storage-route@example.invalid",
            password_hash="test-only",
            is_active=True,
        )
        db.add_all([person, caregiver, account])
        db.flush()
        db.add(
            AccountPersonAccess(
                account_id=account.id,
                person_id=person.id,
                role="owner",
                permissions=["read", "write", "admin"],
                is_active=True,
                granted_by_account_id=account.id,
            )
        )
        db.add(
            CaregiverAccountLink(
                account_id=account.id,
                caregiver_id=caregiver.id,
                person_id=person.id,
                linked_by_account_id=account.id,
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    def override_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_account] = lambda: account
    client = TestClient(app)
    try:
        response = client.post(
            "/api/v1/upload",
            data={
                "person_id": "storage-route-person",
                "caregiver_id": "storage-route-caregiver",
            },
            files={"file": ("medication.jpg", b"\\xff\\xd8\\xffimage-bytes", "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        evidence_id = response.json()["evidence_id"]
        assert uploaded["content_type"] == "image/jpeg"
        assert uploaded["data"] == b"\\xff\\xd8\\xffimage-bytes"
        assert "/" not in uploaded["path"]

        db = testing_session()
        try:
            evidence = db.query(Evidence).filter_by(id=evidence_id).one()
            assert evidence.person_id == "storage-route-person"
            assert evidence.uploaded_by_caregiver_id == "storage-route-caregiver"
            assert evidence.original_file_path == uploaded["path"]
            assert evidence.source_type == "caregiver_uploaded_image"
            assert evidence.evidence_status == "reported"
            assert evidence.confidence is None
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
