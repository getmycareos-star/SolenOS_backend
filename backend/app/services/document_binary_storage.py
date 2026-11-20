"""Durable storage for original document binaries.

PostgreSQL remains the metadata and care-record authority. New binaries use the
configured S3-compatible bucket when enabled; existing PostgreSQL-backed rows
remain readable for backwards compatibility and local development.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Any


def _backend() -> str:
    value = os.getenv("DOCUMENT_STORAGE_BACKEND", "postgres").strip().lower()
    if value not in {"postgres", "s3"}:
        raise RuntimeError(f"Unsupported DOCUMENT_STORAGE_BACKEND: {value}")
    return value


@lru_cache(maxsize=1)
def _s3_client():
    required = {
        "DOCUMENT_S3_ENDPOINT": os.getenv("DOCUMENT_S3_ENDPOINT"),
        "DOCUMENT_S3_REGION": os.getenv("DOCUMENT_S3_REGION"),
        "DOCUMENT_S3_ACCESS_KEY_ID": os.getenv("DOCUMENT_S3_ACCESS_KEY_ID"),
        "DOCUMENT_S3_SECRET_ACCESS_KEY": os.getenv("DOCUMENT_S3_SECRET_ACCESS_KEY"),
        "DOCUMENT_S3_BUCKET": os.getenv("DOCUMENT_S3_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "S3 document storage is enabled but configuration is incomplete: "
            + ", ".join(missing)
        )

    import boto3
    from botocore.config import Config

    addressing_style = os.getenv("DOCUMENT_S3_ADDRESSING_STYLE", "path").strip().lower()
    if addressing_style not in {"path", "virtual", "auto"}:
        raise RuntimeError("DOCUMENT_S3_ADDRESSING_STYLE must be path, virtual, or auto")
    return boto3.client(
        "s3",
        endpoint_url=required["DOCUMENT_S3_ENDPOINT"],
        region_name=required["DOCUMENT_S3_REGION"],
        aws_access_key_id=required["DOCUMENT_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=required["DOCUMENT_S3_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing_style},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _bucket_name() -> str:
    value = os.getenv("DOCUMENT_S3_BUCKET", "").strip()
    if not value:
        raise RuntimeError("DOCUMENT_S3_BUCKET is not configured")
    return value


def store_document_binary(
    *,
    person_id: str,
    evidence_id: str,
    data: bytes,
    mime_type: str,
) -> dict[str, Any]:
    """Store bytes and return the fields required by EvidenceUpload."""
    payload = bytes(data)
    digest = hashlib.sha256(payload).hexdigest()
    if _backend() == "postgres":
        return {
            "storage_backend": "postgres",
            "storage_key": None,
            "content": payload,
            "size_bytes": len(payload),
            "sha256": digest,
        }

    # The evidence UUID is generated and flushed before this call. A failed
    # object write aborts the database transaction; it is never reported saved.
    key = f"care-records/{person_id}/documents/{evidence_id}"
    _s3_client().put_object(
        Bucket=_bucket_name(),
        Key=key,
        Body=payload,
        ContentType=mime_type,
        Metadata={"sha256": digest, "evidence-id": evidence_id},
    )
    return {
        "storage_backend": "s3",
        "storage_key": key,
        "content": payload,
        "size_bytes": len(payload),
        "sha256": digest,
    }


def read_document_binary(upload: Any) -> bytes:
    """Read and integrity-check an original binary from its recorded storage."""
    backend = (getattr(upload, "storage_backend", None) or "postgres").strip().lower()
    if backend == "s3":
        key = getattr(upload, "storage_key", None)
        if not key:
            raise RuntimeError("S3-backed document is missing its storage key")
        response = _s3_client().get_object(Bucket=_bucket_name(), Key=key)
        body = response["Body"]
        try:
            payload = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
    elif backend == "postgres":
        payload = bytes(getattr(upload, "content", None) or b"")
    else:
        raise RuntimeError(f"Unsupported document storage backend: {backend}")

    expected_size = int(getattr(upload, "size_bytes", -1))
    expected_hash = str(getattr(upload, "sha256", "") or "")
    if len(payload) != expected_size:
        raise RuntimeError("Original document size does not match its persisted metadata")
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise RuntimeError("Original document checksum does not match its persisted metadata")
    return payload


def restore_document_binary_from_database(upload: Any) -> bytes:
    """Rebuild a missing S3 object from the PostgreSQL recovery copy."""
    backend = (getattr(upload, "storage_backend", None) or "postgres").strip().lower()
    if backend == "postgres":
        return read_document_binary(upload)
    if backend != "s3":
        raise RuntimeError(f"Unsupported document storage backend: {backend}")

    payload = bytes(getattr(upload, "content", None) or b"")
    expected_size = int(getattr(upload, "size_bytes", -1))
    expected_hash = str(getattr(upload, "sha256", "") or "")
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_hash:
        raise RuntimeError("PostgreSQL document recovery copy failed its integrity check")

    key = getattr(upload, "storage_key", None)
    if not key:
        raise RuntimeError("S3-backed document is missing its storage key")
    _s3_client().put_object(
        Bucket=_bucket_name(),
        Key=key,
        Body=payload,
        ContentType=getattr(upload, "mime_type", "application/octet-stream"),
        Metadata={"sha256": expected_hash, "evidence-id": str(getattr(upload, "evidence_id", ""))},
    )
    return read_document_binary(upload)


def delete_document_binary(upload: Any) -> None:
    """Delete an external object; PostgreSQL-backed binaries cascade with metadata."""
    backend = (getattr(upload, "storage_backend", None) or "postgres").strip().lower()
    if backend == "postgres":
        return
    if backend != "s3":
        raise RuntimeError(f"Unsupported document storage backend: {backend}")
    key = getattr(upload, "storage_key", None)
    if not key:
        raise RuntimeError("S3-backed document is missing its storage key")
    _s3_client().delete_object(Bucket=_bucket_name(), Key=key)


def delete_document_object(storage_backend: str, storage_key: str | None) -> None:
    """Delete by storage coordinates when the ORM row is already being removed."""
    if storage_backend == "postgres":
        return
    if storage_backend != "s3" or not storage_key:
        raise RuntimeError("Cannot delete document object without valid storage coordinates")
    _s3_client().delete_object(Bucket=_bucket_name(), Key=storage_key)
