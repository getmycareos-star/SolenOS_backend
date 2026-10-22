from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.storage as storage


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
