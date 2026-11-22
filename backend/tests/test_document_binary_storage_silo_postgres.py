"""Real S3-compatible object storage + PostgreSQL recovery integration test.

Runs only in the disposable CI PostgreSQL database against the Silo object
store started by document-lifecycle-postgres.yml. No S3 client is mocked.
"""
import hashlib
import os
import uuid

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.care import Evidence
from app.models.collaboration import EvidenceUpload
from app.services import document_binary_storage as storage

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires real PostgreSQL DATABASE_URL",
)


def test_s3_object_ownership_download_checksum_and_postgres_recovery():
    storage._s3_client.cache_clear()
    s3 = storage._s3_client()
    bucket = storage._bucket_name()
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise

    suffix = uuid.uuid4().hex
    original = (
        f"Original discharge document {suffix}. The caregiver reports that "
        "breakfast was mostly uneaten and asks for a follow-up."
    ).encode("utf-8")
    digest = hashlib.sha256(original).hexdigest()
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    storage_key = None

    try:
        with TestClient(app) as client:
            owner = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": f"S3 document owner {suffix}"},
            )
            other = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": f"S3 unrelated caregiver {suffix}"},
            )
            assert owner.status_code == 201, owner.text
            assert other.status_code == 201, other.text
            owner_headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
            person_id = owner.json()["person"]["id"]
            caregiver_id = owner.json()["caregiver"]["id"]

            uploaded = client.post(
                "/api/v1/upload",
                headers=owner_headers,
                data={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "evidence_type": "document",
                },
                files={"file": (f"discharge-{suffix}.txt", original, "text/plain")},
            )
            assert uploaded.status_code == 201, uploaded.text
            receipt = uploaded.json()
            evidence_id = receipt["evidence_id"]
            assert receipt["persisted"] is True
            assert receipt["size_bytes"] == len(original)
            assert receipt["sha256"] == digest

            with Session() as db:
                upload = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
                evidence = db.query(Evidence).filter_by(id=evidence_id).one()
                assert evidence.person_id == person_id
                assert evidence.uploaded_by_caregiver_id == caregiver_id
                assert upload.storage_backend == "s3"
                storage_key = upload.storage_key
                assert storage_key == f"care-records/{person_id}/documents/{evidence_id}"
                assert bytes(upload.content) == original
                assert upload.size_bytes == len(original)
                assert upload.sha256 == digest

            head = s3.head_object(Bucket=bucket, Key=storage_key)
            assert head["ContentLength"] == len(original)
            assert head["Metadata"]["sha256"] == digest
            assert head["Metadata"]["evidence-id"] == evidence_id

            downloaded = client.get(f"/api/v1/upload/{evidence_id}", headers=owner_headers)
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content == original
            assert hashlib.sha256(downloaded.content).hexdigest() == digest

            denied = client.get(f"/api/v1/upload/{evidence_id}", headers=other_headers)
            assert denied.status_code == 404, denied.text

            # A genuinely missing S3 object may be rebuilt from the checksum-
            # verified PostgreSQL recovery copy; other S3 failures do not fall back.
            s3.delete_object(Bucket=bucket, Key=storage_key)
            restored = client.get(f"/api/v1/upload/{evidence_id}", headers=owner_headers)
            assert restored.status_code == 200, restored.text
            assert restored.content == original
            restored_head = s3.head_object(Bucket=bucket, Key=storage_key)
            assert restored_head["Metadata"]["sha256"] == digest

            # A present but corrupted object must fail closed rather than silently
            # serving the PostgreSQL recovery copy as if the object were healthy.
            tampered = b"X" * len(original)
            s3.put_object(
                Bucket=bucket,
                Key=storage_key,
                Body=tampered,
                ContentType="text/plain",
                Metadata={"sha256": digest, "evidence-id": evidence_id},
            )
            integrity_failure = client.get(f"/api/v1/upload/{evidence_id}", headers=owner_headers)
            assert integrity_failure.status_code == 500, integrity_failure.text

            s3.put_object(
                Bucket=bucket,
                Key=storage_key,
                Body=original,
                ContentType="text/plain",
                Metadata={"sha256": digest, "evidence-id": evidence_id},
            )
            verified_again = client.get(f"/api/v1/upload/{evidence_id}", headers=owner_headers)
            assert verified_again.status_code == 200, verified_again.text
            assert verified_again.content == original
    finally:
        if storage_key:
            try:
                s3.delete_object(Bucket=bucket, Key=storage_key)
            except Exception:
                pass
        engine.dispose()
