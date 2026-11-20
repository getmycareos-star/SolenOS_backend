from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest

from app.services import document_binary_storage as storage


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.put_calls = []
        self.delete_calls = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = bytes(kwargs["Body"])

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(self.objects[(kwargs["Bucket"], kwargs["Key"])])}

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)


def test_postgres_storage_remains_a_durable_compatible_fallback(monkeypatch):
    monkeypatch.setenv("DOCUMENT_STORAGE_BACKEND", "postgres")
    payload = b"original discharge document"
    stored = storage.store_document_binary(
        person_id="person-1",
        evidence_id="evidence-1",
        data=payload,
        mime_type="text/plain",
    )
    assert stored["storage_backend"] == "postgres"
    assert stored["storage_key"] is None
    assert stored["content"] == payload
    row = SimpleNamespace(
        storage_backend="postgres",
        storage_key=None,
        content=stored["content"],
        size_bytes=stored["size_bytes"],
        sha256=stored["sha256"],
    )
    assert storage.read_document_binary(row) == payload
    storage.delete_document_binary(row)


def test_s3_storage_writes_reads_verifies_and_deletes_original_binary(monkeypatch):
    monkeypatch.setenv("DOCUMENT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("DOCUMENT_S3_BUCKET", "solenos-care-documents")
    client = FakeS3()
    monkeypatch.setattr(storage, "_s3_client", lambda: client)
    payload = b"original object storage bytes"

    stored = storage.store_document_binary(
        person_id="person-1",
        evidence_id="evidence-2",
        data=payload,
        mime_type="application/pdf",
    )
    assert stored["storage_backend"] == "s3"
    assert stored["content"] == payload
    assert stored["storage_key"] == "care-records/person-1/documents/evidence-2"
    assert client.put_calls[0]["Metadata"]["sha256"] == hashlib.sha256(payload).hexdigest()

    row = SimpleNamespace(
        storage_backend=stored["storage_backend"],
        storage_key=stored["storage_key"],
        content=None,
        size_bytes=stored["size_bytes"],
        sha256=stored["sha256"],
    )
    assert storage.read_document_binary(row) == payload
    storage.delete_document_binary(row)
    assert client.delete_calls == [{
        "Bucket": "solenos-care-documents",
        "Key": stored["storage_key"],
    }]


def test_s3_object_can_be_restored_from_the_postgresql_recovery_copy(monkeypatch):
    monkeypatch.setenv("DOCUMENT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("DOCUMENT_S3_BUCKET", "solenos-care-documents")
    client = FakeS3()
    monkeypatch.setattr(storage, "_s3_client", lambda: client)
    payload = b"backup recovery document"
    stored = storage.store_document_binary(
        person_id="person-2",
        evidence_id="evidence-recovery",
        data=payload,
        mime_type="application/pdf",
    )
    row = SimpleNamespace(
        evidence_id="evidence-recovery",
        storage_backend="s3",
        storage_key=stored["storage_key"],
        content=stored["content"],
        mime_type="application/pdf",
        size_bytes=stored["size_bytes"],
        sha256=stored["sha256"],
    )
    storage.delete_document_binary(row)
    assert ("solenos-care-documents", stored["storage_key"]) not in client.objects
    restored = storage.restore_document_binary_from_database(row)
    assert restored == payload
    assert storage.read_document_binary(row) == payload


def test_s3_storage_fails_closed_when_bucket_credentials_are_missing(monkeypatch):
    monkeypatch.setenv("DOCUMENT_STORAGE_BACKEND", "s3")
    for name in (
        "DOCUMENT_S3_ENDPOINT",
        "DOCUMENT_S3_REGION",
        "DOCUMENT_S3_ACCESS_KEY_ID",
        "DOCUMENT_S3_SECRET_ACCESS_KEY",
        "DOCUMENT_S3_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)
    storage._s3_client.cache_clear()
    with pytest.raises(RuntimeError, match="configuration is incomplete"):
        storage.store_document_binary(
            person_id="person-1",
            evidence_id="evidence-3",
            data=b"must not be silently stored in the wrong backend",
            mime_type="application/pdf",
        )


def test_s3_storage_rejects_corrupt_original_bytes(monkeypatch):
    monkeypatch.setenv("DOCUMENT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("DOCUMENT_S3_BUCKET", "solenos-care-documents")
    client = FakeS3()
    monkeypatch.setattr(storage, "_s3_client", lambda: client)
    client.objects[("solenos-care-documents", "corrupt/key")] = b"corrupt"
    row = SimpleNamespace(
        storage_backend="s3",
        storage_key="corrupt/key",
        size_bytes=7,
        sha256=hashlib.sha256(b"expected").hexdigest(),
        content=None,
    )
    with pytest.raises(RuntimeError, match="checksum"):
        storage.read_document_binary(row)
