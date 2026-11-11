"""Destructive-safe PostgreSQL backup/restore verification for SolenOS.

Run only against dedicated synthetic-data databases:
  SOLENOS_BACKUP_SOURCE_DATABASE_URL=..._backup_test
  SOLENOS_BACKUP_RESTORE_DATABASE_URL=..._recovery_test
  python scripts/verify_backup_restore.py

The restore target is dropped/recreated logically by pg_restore --clean. Never point
these variables at production or a database containing real caregiver information.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _database_name(url: str) -> str:
    return str(make_url(url).database or "").lower()


def _libpq_url(url: str) -> str:
    # PostgreSQL CLI tools accept libpq URIs, not SQLAlchemy driver-qualified URLs.
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _validate_database_urls(source_url: str, restore_url: str) -> None:
    source = make_url(source_url)
    restore = make_url(restore_url)
    if source_url == restore_url or (
        source.host, source.port, source.database
    ) == (restore.host, restore.port, restore.database):
        raise ValueError("Source and restore databases must be different.")
    if source.get_backend_name() != "postgresql" or restore.get_backend_name() != "postgresql":
        raise ValueError("Both databases must use PostgreSQL.")
    if not re.search(r"(test|backup)", _database_name(source_url)):
        raise ValueError("Source database name must contain 'test' or 'backup'.")
    if not re.search(r"(test|restore|recovery)", _database_name(restore_url)):
        raise ValueError("Restore database name must contain 'test', 'restore' or 'recovery'.")
    if "prod" in _database_name(source_url) or "prod" in _database_name(restore_url):
        raise ValueError("Production-named databases are prohibited.")


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, env=env, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {Path(command[0]).name}\n"
            f"{completed.stderr[-6000:]}"
        )


def _seed_fixture(database_url: str, ids: dict[str, str], document: bytes) -> None:
    digest = hashlib.sha256(document).hexdigest()
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO persons (id, name) VALUES (:id, :name)
            """), {"id": ids["person"], "name": ids["person_name"]})
            connection.execute(text("""
                INSERT INTO caregivers (id, name, relationship, person_id, timezone)
                VALUES (:id, :name, 'family caregiver', :person_id, 'UTC')
            """), {"id": ids["caregiver"], "name": ids["caregiver_name"], "person_id": ids["person"]})
            connection.execute(text("""
                INSERT INTO persons (id, name) VALUES (:id, :name)
            """), {"id": ids["other_person"], "name": ids["other_person_name"]})
            connection.execute(text("""
                INSERT INTO caregivers (id, name, relationship, person_id, timezone)
                VALUES (:id, :name, 'family caregiver', :person_id, 'UTC')
            """), {"id": ids["other_caregiver"], "name": ids["other_caregiver_name"], "person_id": ids["other_person"]})
            connection.execute(text("""
                INSERT INTO anonymous_sessions
                    (id, person_id, caregiver_id, token_hash, expires_at)
                VALUES (:id, :person_id, :caregiver_id, :token_hash, :expires_at)
            """), {
                "id": ids["session"], "person_id": ids["person"],
                "caregiver_id": ids["caregiver"],
                "token_hash": hashlib.sha256(("synthetic-token-" + ids["session"]).encode()).hexdigest(),
                "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
            })
            connection.execute(text("""
                INSERT INTO caregiver_consents
                    (id, person_id, caregiver_id, medical_disclaimer_acknowledged,
                     privacy_model_acknowledged, multi_caregiver_acknowledged,
                     no_advertising_acknowledged, data_improvement_consent,
                     accepted_terms_version, accepted_at)
                VALUES (:id, :person_id, :caregiver_id, true, true, true, true, false,
                        '2026-10', :accepted_at)
            """), {
                "id": ids["consent"], "person_id": ids["person"],
                "caregiver_id": ids["caregiver"],
                "accepted_at": datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
            })
            connection.execute(text("""
                INSERT INTO evidence
                    (id, person_id, type, source_text, uploaded_by_caregiver_id,
                     original_author, reported_by_label, origin_channel, source_text_sha256)
                VALUES
                    (:id, :person_id, 'observation', :source_text, :caregiver_id,
                     'Synthetic test author', 'Synthetic test caregiver', 'backup_restore_test', :source_hash)
            """), {
                "id": ids["evidence"], "person_id": ids["person"],
                "source_text": "Synthetic observation: breakfast intake was reduced.",
                "caregiver_id": ids["caregiver"],
                "source_hash": hashlib.sha256(b"Synthetic observation: breakfast intake was reduced.").hexdigest(),
            })
            connection.execute(text("""
                INSERT INTO care_events
                    (id, person_id, event_type, status, occurred_at, title, description,
                     evidence_ids, created_by_caregiver_id)
                VALUES
                    (:id, :person_id, 'observation', 'recorded', :occurred_at, :title,
                     :description, CAST(:evidence_ids AS json), :caregiver_id)
            """), {
                "id": ids["event"], "person_id": ids["person"],
                "occurred_at": datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc),
                "title": "Synthetic breakfast observation",
                "description": "Reduced intake reported by caregiver.",
                "evidence_ids": json.dumps([ids["evidence"]]),
                "caregiver_id": ids["caregiver"],
            })
            connection.execute(text("""
                INSERT INTO evidence_uploads
                    (evidence_id, filename, mime_type, content, size_bytes, sha256)
                VALUES (:evidence_id, 'synthetic-care-note.txt', 'text/plain',
                        :content, :size_bytes, :sha256)
            """), {
                "evidence_id": ids["evidence"], "content": document,
                "size_bytes": len(document), "sha256": digest,
            })
            connection.execute(text("""
                INSERT INTO corrections
                    (id, person_id, target_type, target_id, original_text,
                     corrected_text, reason, caregiver_id)
                VALUES (:id, :person_id, 'care_event', :target_id, :original_text,
                        :corrected_text, 'Synthetic correction history', :caregiver_id)
            """), {
                "id": ids["correction"], "person_id": ids["person"],
                "target_id": ids["event"], "original_text": "Breakfast was untouched.",
                "corrected_text": "Breakfast intake was reduced, not absent.",
                "caregiver_id": ids["caregiver"],
            })
            connection.execute(text("""
                INSERT INTO care_state_revisions
                    (id, person_id, target_type, target_id, revision_number,
                     change_kind, before_state, after_state, evidence_ids, changed_by_caregiver_id)
                VALUES (:id, :person_id, 'care_event', :target_id, 1, 'created',
                        NULL, :after_state, CAST(:evidence_ids AS json), :caregiver_id)
            """), {
                "id": ids["revision"], "person_id": ids["person"],
                "target_id": ids["event"],
                "after_state": json.dumps({"title": "Synthetic breakfast observation", "status": "recorded"}),
                "evidence_ids": json.dumps([ids["evidence"]]),
                "caregiver_id": ids["caregiver"],
            })
    finally:
        engine.dispose()


def _assert_restored(database_url: str, ids: dict[str, str], document: bytes) -> dict[str, int]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            caregiver = connection.execute(text("""
                SELECT c.id, c.person_id, p.name
                FROM caregivers c JOIN persons p ON p.id = c.person_id
                WHERE c.id = :caregiver_id
            """), {"caregiver_id": ids["caregiver"]}).mappings().one()
            assert caregiver["person_id"] == ids["person"]
            assert caregiver["name"] == ids["person_name"]

            session = connection.execute(text("""
                SELECT person_id, caregiver_id, token_hash, revoked_at
                FROM anonymous_sessions WHERE id = :id
            """), {"id": ids["session"]}).mappings().one()
            assert session["person_id"] == ids["person"]
            assert session["caregiver_id"] == ids["caregiver"]
            assert session["token_hash"] == hashlib.sha256(("synthetic-token-" + ids["session"]).encode()).hexdigest()
            assert session["revoked_at"] is None

            consent = connection.execute(text("""
                SELECT person_id, caregiver_id, privacy_model_acknowledged,
                       medical_disclaimer_acknowledged, accepted_terms_version, accepted_at
                FROM caregiver_consents WHERE id = :id
            """), {"id": ids["consent"]}).mappings().one()
            assert consent["person_id"] == ids["person"]
            assert consent["caregiver_id"] == ids["caregiver"]
            assert consent["privacy_model_acknowledged"] is True
            assert consent["medical_disclaimer_acknowledged"] is True
            assert consent["accepted_terms_version"] == "2026-10"
            assert consent["accepted_at"] is not None

            evidence = connection.execute(text("""
                SELECT id, person_id, uploaded_by_caregiver_id, source_text,
                       source_text_sha256
                FROM evidence WHERE id = :id
            """), {"id": ids["evidence"]}).mappings().one()
            assert evidence["person_id"] == ids["person"]
            assert evidence["uploaded_by_caregiver_id"] == ids["caregiver"]
            assert evidence["source_text_sha256"] == hashlib.sha256(evidence["source_text"].encode()).hexdigest()

            event = connection.execute(text("""
                SELECT id, person_id, created_by_caregiver_id, evidence_ids
                FROM care_events WHERE id = :id
            """), {"id": ids["event"]}).mappings().one()
            assert event["person_id"] == ids["person"]
            assert event["created_by_caregiver_id"] == ids["caregiver"]
            assert ids["evidence"] in (event["evidence_ids"] or [])

            upload = connection.execute(text("""
                SELECT filename, mime_type, content, size_bytes, sha256
                FROM evidence_uploads WHERE evidence_id = :id
            """), {"id": ids["evidence"]}).mappings().one()
            assert bytes(upload["content"]) == document
            assert upload["size_bytes"] == len(document)
            assert upload["sha256"] == hashlib.sha256(document).hexdigest()

            correction = connection.execute(text("""
                SELECT person_id, target_id, original_text, corrected_text, caregiver_id
                FROM corrections WHERE id = :id
            """), {"id": ids["correction"]}).mappings().one()
            assert correction["person_id"] == ids["person"]
            assert correction["target_id"] == ids["event"]
            assert correction["caregiver_id"] == ids["caregiver"]
            assert correction["original_text"] != correction["corrected_text"]

            revision = connection.execute(text("""
                SELECT person_id, target_id, evidence_ids, changed_by_caregiver_id
                FROM care_state_revisions WHERE id = :id
            """), {"id": ids["revision"]}).mappings().one()
            assert revision["person_id"] == ids["person"]
            assert revision["target_id"] == ids["event"]
            assert ids["evidence"] in (revision["evidence_ids"] or [])
            assert revision["changed_by_caregiver_id"] == ids["caregiver"]

            # Explicit ownership-isolation assertion: the other synthetic caregiver
            # has no join path to this evidence or its care-event history.
            foreign_scope = connection.execute(text("""
                SELECT count(*) FROM evidence e
                JOIN caregivers c ON c.person_id = e.person_id
                WHERE e.id = :evidence_id AND c.id = :other_caregiver_id
            """), {
                "evidence_id": ids["evidence"],
                "other_caregiver_id": ids["other_caregiver"],
            }).scalar_one()
            assert foreign_scope == 0
            return {
                "caregivers": connection.execute(text("SELECT count(*) FROM caregivers WHERE id IN (:a, :b)"), {"a": ids["caregiver"], "b": ids["other_caregiver"]}).scalar_one(),
                "persons": connection.execute(text("SELECT count(*) FROM persons WHERE id IN (:a, :b)"), {"a": ids["person"], "b": ids["other_person"]}).scalar_one(),
                "anonymous_sessions": 1, "consent_records": 1,
                "evidence": 1, "care_events": 1, "document_blobs": 1,
                "corrections": 1, "lcr_revisions": 1,
            }
    finally:
        engine.dispose()


def main() -> int:
    source_url = os.environ.get("SOLENOS_BACKUP_SOURCE_DATABASE_URL", "")
    restore_url = os.environ.get("SOLENOS_BACKUP_RESTORE_DATABASE_URL", "")
    if not source_url or not restore_url:
        print("Set SOLENOS_BACKUP_SOURCE_DATABASE_URL and SOLENOS_BACKUP_RESTORE_DATABASE_URL.", file=sys.stderr)
        return 2
    try:
        _validate_database_urls(source_url, restore_url)
    except Exception as exc:
        print(f"SAFETY CHECK FAILED: {exc}", file=sys.stderr)
        return 2
    for binary in ("pg_dump", "pg_restore"):
        if not shutil.which(binary):
            print(f"Required PostgreSQL client not found: {binary}", file=sys.stderr)
            return 2

    run_id = uuid.uuid4().hex
    ids = {
        "person": str(uuid.uuid4()), "caregiver": str(uuid.uuid4()),
        "other_person": str(uuid.uuid4()), "other_caregiver": str(uuid.uuid4()),
        "evidence": str(uuid.uuid4()), "event": str(uuid.uuid4()),
        "correction": str(uuid.uuid4()), "revision": str(uuid.uuid4()),
        "session": str(uuid.uuid4()), "consent": str(uuid.uuid4()),
        "person_name": f"BACKUP-TEST-PERSON-{run_id}",
        "caregiver_name": f"BACKUP-TEST-CAREGIVER-{run_id}",
        "other_person_name": f"BACKUP-TEST-OTHER-PERSON-{run_id}",
        "other_caregiver_name": f"BACKUP-TEST-OTHER-CAREGIVER-{run_id}",
    }
    document = f"SolenOS synthetic recovery fixture {run_id}\nNo real caregiver data.\n".encode()
    started = time.monotonic()
    try:
        _seed_fixture(source_url, ids, document)
        seeded_at = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="solenos-backup-restore-") as directory:
            dump_path = str(Path(directory) / "solenos-recovery.dump")
            _run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", dump_path, _libpq_url(source_url)])
            backup_seconds = time.monotonic() - seeded_at
            backup_bytes = Path(dump_path).stat().st_size
            _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl", "--exit-on-error", "--dbname", _libpq_url(restore_url), dump_path])
            restore_seconds = time.monotonic() - seeded_at - backup_seconds
        counts = _assert_restored(restore_url, ids, document)
        report = {
            "status": "PASS",
            "run_id": run_id,
            "source_database": _database_name(source_url),
            "restore_database": _database_name(restore_url),
            "backup_seconds": round(backup_seconds, 3),
            "backup_bytes": backup_bytes,
            "restore_seconds": round(restore_seconds, 3),
            "end_to_end_seconds": round(time.monotonic() - started, 3),
            "fixture_record_counts": counts,
            "verified": [
                "caregiver and person identity association",
                "anonymous session identity and token-hash persistence",
                "caregiver consent and accepted-terms persistence",
                "evidence ownership and source fingerprint",
                "care event to evidence association",
                "uploaded document bytes, size and SHA-256",
                "correction history",
                "LCR care-state revision and evidence association",
                "cross-caregiver ownership isolation",
            ],
            "scope": "synthetic PostgreSQL backup/restore; no production data",
        }
        rendered = json.dumps(report, indent=2)
        print(rendered)
        report_path = os.environ.get("SOLENOS_BACKUP_RESTORE_REPORT_PATH")
        if report_path:
            Path(report_path).write_text(rendered + "\\n", encoding="utf-8")
        return 0
    except Exception as exc:
        failure_report = {
            "status": "FAIL", "run_id": run_id, "error": str(exc),
            "note": "Do not mark backup recovery verified. Inspect the isolated databases and logs.",
        }
        rendered = json.dumps(failure_report, indent=2)
        print(rendered, file=sys.stderr)
        report_path = os.environ.get("SOLENOS_BACKUP_RESTORE_REPORT_PATH")
        if report_path:
            Path(report_path).write_text(rendered + "\\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
