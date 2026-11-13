"""Real HTTP + PostgreSQL smoke test for anonymous care continuity.

Runs only against the disposable PostgreSQL database supplied by CI. Starts and
restarts Uvicorn itself so persistence is verified across an application restart.
"""
from __future__ import annotations

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.change_enums import SituationType, SituationState
from app.models.change import Situation as SituationModel
from app.models.capture_analysis import CaptureAnalysis
from app.models.qwen_background_job import QwenBackgroundJob
from app.models.care import CareEvent
from app.models.care_continuity import CareAssertion

BASE_URL = "http://127.0.0.1:8012"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing PostgreSQL E2E smoke: DATABASE_URL must be PostgreSQL.")


def request(method: str, path: str, *, body=None, token: str | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            decoded = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            decoded = payload.decode("utf-8", "replace")
        return exc.code, decoded


def upload_request(fields: dict[str, str], filename: str, content: bytes, token: str):
    boundary = f"solenos-e2e-{os.getpid()}-{time.time_ns()}"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode()
        + content + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        BASE_URL + "/api/v1/upload",
        data=b"".join(chunks),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            decoded = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            decoded = payload.decode("utf-8", "replace")
        return exc.code, decoded


def download_binary(evidence_id: str, token: str):
    req = urllib.request.Request(
        BASE_URL + f"/api/v1/upload/{evidence_id}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def start_server():
    log_path = Path("/tmp/solenos-postgres-e2e-uvicorn.log")
    log = log_path.open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8012"],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if process.poll() is not None:
            log.close()
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
            raise RuntimeError(f"Uvicorn exited early; see {log_path}\\n{detail}")
        try:
            status, ready = request("GET", "/ready")
        except Exception:
            time.sleep(0.5)
            continue
        if status == 200:
            require(ready.get("migration_state") == "current",
                    f"PostgreSQL readiness did not verify current Alembic head: {ready}")
            require(ready.get("migration_head"),
                    f"PostgreSQL readiness did not report its Alembic head: {ready}")
            require(ready.get("continuity_schema") == "verified",
                    f"PostgreSQL readiness did not verify migration-created continuity tables: {ready}")
            log.close()
            return process
        time.sleep(0.5)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    log.close()
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
    raise RuntimeError(f"Uvicorn did not become ready; see {log_path}\\n{detail}")


def stop_server(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.returncode not in (0, -15):
        raise RuntimeError(f"Uvicorn exited with code {process.returncode}")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_restored_backup_fixture_if_present():
    """Exercise the exact synthetic caregiver restored by verify_backup_restore.py.

    This is a no-op during the first source-database E2E run. After pg_restore,
    the fixture exists and must be readable through the real authenticated API.
    """
    with SessionLocal() as db:
        fixture = db.execute(text("""
            SELECT c.id AS caregiver_id, p.id AS person_id, p.name AS person_name,
                   s.id AS session_id, s.token_hash, e.id AS evidence_id,
                   ce.id AS event_id, u.content AS document_content, u.sha256 AS document_sha256
            FROM caregivers c
            JOIN persons p ON p.id = c.person_id
            JOIN anonymous_sessions s ON s.caregiver_id = c.id AND s.person_id = p.id
            JOIN evidence e ON e.person_id = p.id AND e.uploaded_by_caregiver_id = c.id
            JOIN care_events ce ON ce.person_id = p.id
                AND ce.evidence_ids::jsonb ? e.id
            JOIN evidence_uploads u ON u.evidence_id = e.id
            WHERE c.name LIKE 'BACKUP-TEST-CAREGIVER-%'
              AND e.origin_channel = 'backup_restore_test'
            ORDER BY c.created_at DESC
            LIMIT 1
        """)).mappings().first()
        if fixture is None:
            return False
        fixture = dict(fixture)

    token = "synthetic-token-" + fixture["session_id"]
    require(
        hashlib.sha256(token.encode()).hexdigest() == fixture["token_hash"],
        "restored synthetic session token hash does not match its expected capability",
    )
    document = bytes(fixture["document_content"])
    require(
        hashlib.sha256(document).hexdigest() == fixture["document_sha256"],
        "restored synthetic document SHA-256 does not match its binary contents",
    )

    person_status, person = request(
        "GET", f"/api/v1/persons/{fixture['person_id']}", token=token
    )
    require(
        person_status == 200 and person.get("id") == fixture["person_id"]
        and person.get("name") == fixture["person_name"],
        f"restored caregiver could not read the correct person identity: {person_status}: {person}",
    )
    evidence_status, evidence = request(
        "GET", f"/api/v1/evidence/{fixture['person_id']}", token=token
    )
    require(
        evidence_status == 200
        and any(item.get("id") == fixture["evidence_id"] for item in evidence),
        f"restored caregiver could not retrieve its original evidence: {evidence_status}: {evidence}",
    )
    events_status, events = request(
        "GET", f"/api/v1/events/{fixture['person_id']}", token=token
    )
    require(
        events_status == 200
        and any(item.get("id") == fixture["event_id"]
                and fixture["evidence_id"] in (item.get("evidence_ids") or [])
                for item in events),
        f"restored care event/evidence association is not available through the API: {events_status}: {events}",
    )
    lcr_status, lcr = request(
        "GET", "/api/v1/care-continuity/context?limit_per_source=50", token=token
    )
    lcr_events = (
        ((lcr or {}).get("sources") or {}).get("events") or {}
    ).get("items") or []
    require(
        lcr_status == 200
        and any(item.get("id") == fixture["event_id"]
                and fixture["evidence_id"] in (item.get("evidence_ids") or [])
                for item in lcr_events),
        f"restored care event/evidence association is not available in the authoritative LCR source context: {lcr_status}: {lcr}",
    )
    document_status, downloaded = download_binary(fixture["evidence_id"], token)
    require(
        document_status == 200 and downloaded == document,
        f"restored document download differs from the original bytes: {document_status}",
    )
    print(
        "PASS: exact restored synthetic caregiver identity, anonymous session, evidence, "
        "care-event/LCR associations and document bytes are readable through authenticated APIs."
    )
    return True


def main():
    server = start_server()
    try:
        status, _ = request("GET", "/health")
        require(status == 200, f"/health expected 200, got {status}")
        verify_restored_backup_fixture_if_present()

        status, first = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL E2E person A", "caregiver_name": "PostgreSQL E2E caregiver A"},
        )
        require(status == 201, f"anonymous session creation expected 201, got {status}: {first}")
        token_a = first["access_token"]
        original_session_token = token_a
        person_a = first["person"]["id"]
        caregiver_a = first["caregiver"]["id"]

        status, second = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL E2E person B", "caregiver_name": "PostgreSQL E2E caregiver B"},
        )
        require(status == 201, f"second anonymous session creation expected 201, got {status}: {second}")
        token_b = second["access_token"]

        # Seed two independent persisted threads, then resume one of them
        # concurrently. PostgreSQL row locking must append every observation
        # to the selected thread without moving evidence into its neighbour.
        mobility_thread_id = str(uuid.uuid4())
        appetite_thread_id = str(uuid.uuid4())
        thread_start = datetime.now(timezone.utc)
        with SessionLocal() as db:
            db.add_all([
                SituationModel(
                    id=mobility_thread_id,
                    person_id=person_a,
                    situation_type=SituationType.UNKNOWN,
                    state=SituationState.ACTIVE,
                    title="PostgreSQL E2E mobility thread",
                    description="Independent CI-only mobility thread.",
                    signal_ids=[],
                    signal_types=["mobility"],
                    temporal_start=thread_start,
                    temporal_end=thread_start,
                ),
                SituationModel(
                    id=appetite_thread_id,
                    person_id=person_a,
                    situation_type=SituationType.UNKNOWN,
                    state=SituationState.ACTIVE,
                    title="PostgreSQL E2E appetite thread",
                    description="Independent CI-only appetite thread.",
                    signal_ids=[],
                    signal_types=["appetite"],
                    temporal_start=thread_start,
                    temporal_end=thread_start,
                ),
            ])
            db.commit()

        relation_payloads = [
            {
                "source_type": "situation",
                "source_id": mobility_thread_id,
                "relationship_type": "related_to",
                "target_type": "situation",
                "target_id": appetite_thread_id,
                "provenance": "Caregiver explicitly linked these as related but separate concerns in this PostgreSQL concurrency test.",
            },
            {
                "source_type": "situation",
                "source_id": appetite_thread_id,
                "relationship_type": "related_to",
                "target_type": "situation",
                "target_id": mobility_thread_id,
                "provenance": "Caregiver explicitly linked these as related but separate concerns in this PostgreSQL concurrency test.",
            },
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            relationship_results = list(pool.map(
                lambda payload: request(
                    "POST", "/api/v1/care-continuity/relationships",
                    body=payload, token=token_a
                ),
                relation_payloads,
            ))
        require(
            sorted(status for status, _ in relationship_results) == [201, 409],
            f"Concurrent reverse-order related-thread links must create exactly one relationship: {relationship_results}",
        )

        appetite_capture = {
            "caregiver_id": caregiver_a,
            "person_id": person_a,
            "target_situation_id": appetite_thread_id,
            "raw_input": "Daughter: She ate less than half of dinner.",
            "capture_category": "meal",
        }
        status, appetite_result = request(
            "POST", "/api/situation", body=appetite_capture, token=token_a
        )
        require(status == 200, f"Explicit appetite thread recovery failed: {status}: {appetite_result}")
        require(
            appetite_result.get("situation_id") == appetite_thread_id,
            f"Capture did not remain in the explicitly selected appetite thread: {appetite_result}",
        )

        mobility_payloads = [
            {
                "caregiver_id": caregiver_a,
                "person_id": person_a,
                "target_situation_id": mobility_thread_id,
                "raw_input": "Daughter: She was unsteady when turning near the kitchen.",
                "capture_category": "behavior",
            },
            {
                "caregiver_id": caregiver_a,
                "person_id": person_a,
                "target_situation_id": mobility_thread_id,
                "raw_input": "Nurse: She needed support when walking to the bathroom.",
                "capture_category": "behavior",
            },
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_captures = list(pool.map(
                lambda payload: request("POST", "/api/situation", body=payload, token=token_a),
                mobility_payloads,
            ))
        require(all(status == 200 for status, _ in concurrent_captures),
                f"Concurrent selected-thread care captures failed: {concurrent_captures}")
        speaker_source = (
            "Daughter: She stumbled near the kitchen this morning.\n"
            "Nurse: She needed one-person support when standing."
        )
        speaker_capture_status, speaker_capture = request(
            "POST",
            "/api/situation",
            body={
                "caregiver_id": caregiver_a,
                "person_id": person_a,
                "target_situation_id": mobility_thread_id,
                "raw_input": speaker_source,
            },
            token=token_a,
        )
        require(
            speaker_capture_status == 200
            and speaker_capture.get("situation_id") == mobility_thread_id,
            f"Multi-speaker source capture did not persist to selected thread: {speaker_capture_status}: {speaker_capture}",
        )
        concurrent_capture_event_ids = {
            appetite_result.get("care_event_id"),
            speaker_capture.get("care_event_id"),
            *(body.get("care_event_id") for _, body in concurrent_captures),
        }
        concurrent_capture_evidence_ids = {
            appetite_result.get("evidence_id"),
            speaker_capture.get("evidence_id"),
            *(body.get("evidence_id") for _, body in concurrent_captures),
        }
        require(
            all(status == 200 and body.get("situation_id") == mobility_thread_id
                for status, body in concurrent_captures),
            f"Concurrent observations escaped their explicitly selected mobility thread: {concurrent_captures}",
        )
        require(
            len(concurrent_capture_event_ids) == 4
            and None not in concurrent_capture_event_ids
            and len(concurrent_capture_evidence_ids) == 4
            and None not in concurrent_capture_evidence_ids,
            f"Each independent capture must preserve a distinct source/event pair: events={concurrent_capture_event_ids}, evidence={concurrent_capture_evidence_ids}",
        )
        with SessionLocal() as db:
            mobility_thread = db.query(SituationModel).filter_by(id=mobility_thread_id).one()
            appetite_thread = db.query(SituationModel).filter_by(id=appetite_thread_id).one()
            mobility_evidence_ids = set(mobility_thread.signal_ids or [])
            appetite_evidence_ids = set(appetite_thread.signal_ids or [])
            require(
                mobility_evidence_ids == {
                    speaker_capture.get("evidence_id"),
                    *(body.get("evidence_id") for _, body in concurrent_captures),
                },
                f"Concurrent updates were lost or misrouted in mobility thread: {mobility_evidence_ids}",
            )
            require(
                appetite_evidence_ids == {appetite_result.get("evidence_id")},
                f"Unrelated appetite thread was merged with mobility evidence: {appetite_evidence_ids}",
            )
        status, grouped_after_capture = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"Multi-thread hydration failed: {status}: {grouped_after_capture}")
        grouped_threads = {
            item.get("id"): item for item in grouped_after_capture.get("ui_situations", [])
        }
        require(
            mobility_thread_id in grouped_threads and appetite_thread_id in grouped_threads,
            f"Hydration did not return both independent threads: {grouped_threads.keys()}",
        )
        require(
            set(grouped_threads[mobility_thread_id].get("linked_evidence_ids", []))
            == {
                speaker_capture.get("evidence_id"),
                *(body.get("evidence_id") for _, body in concurrent_captures),
            },
            "Hydrated mobility thread did not retain every concurrent and multi-speaker evidence link",
        )
        source_statements = grouped_threads[mobility_thread_id].get("sourceStatements", [])
        selected_speaker_statements = [
            statement for statement in source_statements
            if statement.get("evidence_id") == speaker_capture.get("evidence_id")
        ]
        require(
            [statement.get("speaker_label") for statement in selected_speaker_statements] == ["Daughter", "Nurse"],
            f"Hydration must preserve both explicit speakers in the selected original source order: {selected_speaker_statements}",
        )
        require(
            all(statement.get("reporter_caregiver_id") == caregiver_a for statement in source_statements),
            "Authenticated reporter must remain distinct from every source speaker across the selected thread",
        )
        require(
            all(
                speaker_source[statement["source_span_start"]:statement["source_span_end"]]
                == statement["source_passage"]
                for statement in selected_speaker_statements
            ),
            "Every selected multi-speaker statement must retain exact original source offsets",
        )
        require(
            len(source_statements) >= len(selected_speaker_statements),
            "Thread hydration must retain source statements from all linked evidence records",
        )
        require(
            grouped_threads[appetite_thread_id].get("linked_evidence_ids", [])
            == [appetite_result.get("evidence_id")],
            "Hydrated appetite thread incorrectly absorbed mobility evidence",
        )
        require(
            any(link.get("id") == appetite_thread_id
                for link in grouped_threads[mobility_thread_id].get("relatedSituations", []))
            and any(link.get("id") == mobility_thread_id
                    for link in grouped_threads[appetite_thread_id].get("relatedSituations", [])),
            "Both care threads must expose the explicit relationship while retaining separate evidence",
        )

        # Two simultaneous uploads of identical bytes must not both be
        # reported as originals. PostgreSQL advisory locking serializes the
        # per-person fingerprint lookup and insert while preserving both rows.
        duplicate_blob = b"\x89PNG\r\n\x1a\nSolenOS PostgreSQL duplicate-upload concurrency fixture"
        upload_fields = {
            "person_id": person_a,
            "caregiver_id": caregiver_a,
            "evidence_type": "care_photo",
            "extracted_text": "CI-only original binary upload fixture.",
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_uploads = list(pool.map(
                lambda _: upload_request(
                    upload_fields, "concurrent-care-photo.png", duplicate_blob, token_a
                ),
                range(2),
            ))
        require(all(status == 201 for status, _ in concurrent_uploads),
                f"Concurrent identical uploads failed: {concurrent_uploads}")
        upload_bodies = [body for _, body in concurrent_uploads]
        upload_ids = {body.get("evidence_id") for body in upload_bodies}
        require(len(upload_ids) == 2 and None not in upload_ids,
                f"Both original upload records and their provenance must be retained: {upload_bodies}")
        require(sorted(body.get("duplicate_detected") for body in upload_bodies) == [False, True],
                f"Concurrent duplicate detection must produce exactly one original and one duplicate: {upload_bodies}")
        original_upload_id = next(body["evidence_id"] for body in upload_bodies if not body["duplicate_detected"])
        duplicate_upload_body = next(body for body in upload_bodies if body["duplicate_detected"])
        require(duplicate_upload_body.get("duplicate_of_evidence_id") == original_upload_id,
                f"Duplicate must point to the single canonical original: {upload_bodies}")
        foreign_download_status, foreign_download_body = request(
            "GET", f"/api/v1/upload/{original_upload_id}", token=token_b
        )
        require(
            foreign_download_status == 403,
            f"Binary evidence download must enforce care-record isolation: {foreign_download_status}: {foreign_download_body}",
        )


        # Professional-input evidence must resolve to a real source row in the
        # same PostgreSQL-backed care record; caregiver prose alone is not proof.
        status, source_record = request(
            "POST", "/api/v1/evidence", token=token_a, body={
                "person_id": person_a,
                "uploaded_by_caregiver_id": caregiver_a,
                "type": "document",
                "evidence_kind": "documented_record",
                "epistemic_status": "unverified",
                "source_text": "Synthetic CI-only source record for professional input verification.",
                "source_type": "ci_open_loop_fixture",
                "ingestion_key": "postgres-open-loop-professional-source-001",
            },
        )
        require(status in {200, 201} and source_record.get("id"),
                f"PostgreSQL source evidence creation failed: {status}: {source_record}")
        professional_source_id = source_record["id"]
        status, professional_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "professional_input",
                "title": "CI-only source-backed professional response",
                "description": "Synthetic CI fixture requiring a verified source record.",
                "idempotency_key": "postgres-open-loop-professional-input-001",
                "evidence": [],
            },
        )
        require(status == 201, f"Professional-input loop creation failed: {status}: {professional_loop}")
        professional_loop_id = professional_loop["id"]
        status, unsupported_professional_note = request(
            "POST", f"/api/v1/open-loops/{professional_loop_id}/responses", token=token_a,
            body={"text": "Caregiver says the professional called",
                  "evidence": [{"kind": "caregiver_recorded", "text": "A professional called"}]},
        )
        require(status == 422,
                f"Caregiver prose alone must not substantiate professional input: {status}: {unsupported_professional_note}")
        status, foreign_source_loop = request(
            "POST", "/api/v1/open-loops", token=token_b, body={
                "loop_type": "professional_input",
                "title": "CI-only foreign-source isolation",
                "description": "Synthetic test: source evidence from another person must be denied.",
                "idempotency_key": "postgres-open-loop-foreign-source-001",
                "evidence": [],
            },
        )
        require(status == 201, f"Foreign-source isolation loop creation failed: {status}: {foreign_source_loop}")
        status, foreign_source_attempt = request(
            "POST", f"/api/v1/open-loops/{foreign_source_loop['id']}/responses", token=token_b,
            body={"text": "Attempt to reference another person's source",
                  "evidence": [{"kind": "care_record_source", "evidence_id": professional_source_id}]},
        )
        require(status == 404,
                f"Cross-person source evidence reference must return 404: {status}: {foreign_source_attempt}")
        status, accepted_professional_response = request(
            "POST", f"/api/v1/open-loops/{professional_loop_id}/responses", token=token_a,
            body={"text": "Source-backed professional response recorded",
                  "evidence": [{"kind": "care_record_source", "evidence_id": professional_source_id}]},
        )
        require(status == 200,
                f"Source-backed professional response failed: {status}: {accepted_professional_response}")
        status, closed_professional_loop = request(
            "POST", f"/api/v1/open-loops/{professional_loop_id}/close", token=token_a,
            body={"text": "Reviewed against source record",
                  "evidence": [{"kind": "closure_review", "text": "Synthetic CI closure review"}]},
        )
        require(status == 200 and closed_professional_loop.get("loop", {}).get("status") == "closed",
                f"Source-backed professional loop did not close correctly: {status}: {closed_professional_loop}")

        # Frontend-aligned complete workflow against the real PostgreSQL-backed API:
        # create -> response/action -> outcome -> explicit verification -> close -> history.
        workflow_source = [{"kind": "care_record_source", "evidence_id": professional_source_id}]
        status, workflow_second_source = request(
            "POST", "/api/v1/evidence", token=token_a, body={
                "person_id": person_a,
                "uploaded_by_caregiver_id": caregiver_a,
                "type": "document",
                "evidence_kind": "documented_record",
                "epistemic_status": "unverified",
                "source_text": "Second synthetic CI-only source with separately reviewed provenance.",
                "source_type": "ci_open_loop_fixture",
                "ingestion_key": "postgres-open-loop-workflow-source-002",
            },
        )
        require(status in {200, 201} and workflow_second_source.get("id"),
                f"Second PostgreSQL source evidence creation failed: {status}: {workflow_second_source}")
        workflow_second_source_id = workflow_second_source["id"]
        workflow_source_pair = [
            {"kind": "care_record_source", "evidence_id": professional_source_id},
            {"kind": "care_record_source", "evidence_id": workflow_second_source_id},
        ]
        status, workflow_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "follow_up",
                "title": "CI-only end-to-end follow-up workflow",
                "description": "Synthetic API workflow covering every persisted handoff stage.",
                "idempotency_key": "postgres-open-loop-full-workflow-001",
                "evidence": [{"kind": "ci_fixture", "text": "Synthetic workflow only."}],
                "verification_rule": "The linked synthetic source record is present and was reviewed.",
            },
        )
        require(status == 201, f"Full Open Loop workflow creation failed: {status}: {workflow_loop}")
        workflow_loop_id = workflow_loop["id"]
        status, workflow_response = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/responses", token=token_a,
            body={"text": "Synthetic response recorded", "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-response-001"},
        )
        require(status == 200, f"Full workflow response recording failed: {status}: {workflow_response}")
        status, workflow_action = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/actions", token=token_a,
            body={"text": "Synthetic follow-up action recorded", "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-action-001"},
        )
        require(status == 200, f"Full workflow action recording failed: {status}: {workflow_action}")
        status, workflow_outcome = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/outcomes", token=token_a,
            body={"text": "Synthetic outcome reported", "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-outcome-001"},
        )
        require(status == 200, f"Full workflow outcome recording failed: {status}: {workflow_outcome}")
        status, workflow_verified = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/verify-outcome", token=token_a,
            body={"text": "Synthetic outcome reviewed against the linked record",
                  "evidence": workflow_source,
                  "verification_rule": "The linked synthetic source record is present and was reviewed.",
                  "idempotency_key": "postgres-open-loop-full-verify-001"},
        )
        require(status == 200 and workflow_verified.get("outcome_status") == "outcome_reported",
                f"Full workflow caregiver attestation failed: {status}: {workflow_verified}")
        status, workflow_relationship = request(
            "POST", f"/api/v1/care-continuity/evidence/{professional_source_id}/relationships",
            token=token_a,
            body={
                "related_evidence_id": workflow_second_source_id,
                "relationship_type": "corroborates",
                "source_independence_status": "independent",
                "scope_type": "evidence_pair",
                "rationale": "CI fixture explicitly records separate source origins.",
            },
        )
        require(status == 201 and workflow_relationship.get("id"),
                f"Independent source relationship creation failed: {status}: {workflow_relationship}")
        status, workflow_corroborated = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/corroborate-outcome", token=token_a,
            body={
                "text": "Synthetic independent-source corroboration review",
                "evidence": workflow_source_pair,
                "verification_rule": "The outcome must be supported by two separately originated source records.",
                "idempotency_key": "postgres-open-loop-full-corroborate-001",
            },
        )
        require(status == 200 and workflow_corroborated.get("outcome_status") == "outcome_verified",
                f"Full workflow independent corroboration failed: {status}: {workflow_corroborated}")
        status, workflow_closed = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/close", token=token_a,
            body={"text": "Synthetic closure after recorded verification",
                  "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-close-001"},
        )
        require(status == 200 and workflow_closed.get("loop", {}).get("status") == "closed",
                f"Full workflow closure failed: {status}: {workflow_closed}")
        status, workflow_history = request(
            "GET", f"/api/v1/open-loops/{workflow_loop_id}/history?limit=100", token=token_a,
        )
        workflow_event_types = {event.get("event_type") for event in workflow_history}
        require(status == 200 and {
            "created", "response_recorded", "action_recorded", "outcome_reported",
            "outcome_attested", "outcome_corroborated", "closure_verified",
        }.issubset(workflow_event_types),
                f"Full workflow audit history is incomplete: {status}: {workflow_history}")

        # Conflicting terminal transitions must serialize: exactly one of close
        # or cancel may win, and the loser must receive a conflict rather than
        # silently replacing the first terminal decision.
        status, conflict_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "follow_up",
                "title": "CI-only competing terminal transitions",
                "description": "Synthetic race between verified closure and cancellation.",
                "idempotency_key": "postgres-open-loop-terminal-race-001",
                "evidence": [{"kind": "ci_fixture", "text": "Synthetic lifecycle race only."}],
                "verification_rule": "The linked synthetic source record is present and was reviewed.",
            },
        )
        require(status == 201, f"Terminal race loop creation failed: {status}: {conflict_loop}")
        conflict_loop_id = conflict_loop["id"]
        for path, payload, label in [
            ("outcomes", {"text": "Synthetic outcome", "evidence": workflow_source}, "outcome"),
            ("verify-outcome", {"text": "Synthetic verification", "evidence": workflow_source,
                                "verification_rule": "The linked synthetic source record is present and was reviewed."}, "verification"),
        ]:
            status, result = request(
                "POST", f"/api/v1/open-loops/{conflict_loop_id}/{path}", token=token_a,
                body={**payload, "idempotency_key": f"postgres-open-loop-terminal-race-{label}-001"},
            )
            require(status == 200, f"Terminal race preparation {label} failed: {status}: {result}")
        terminal_actions = [
            ("close", {"text": "Closure won if accepted", "evidence": workflow_source,
                       "idempotency_key": "postgres-open-loop-terminal-race-close-001"}),
            ("cancel", {"text": "Cancellation won if accepted", "evidence": workflow_source,
                        "idempotency_key": "postgres-open-loop-terminal-race-cancel-001"}),
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            terminal_results = list(pool.map(
                lambda item: request(
                    "POST", f"/api/v1/open-loops/{conflict_loop_id}/{item[0]}",
                    token=token_a, body=item[1],
                ),
                terminal_actions,
            ))
        terminal_statuses = [status for status, _ in terminal_results]
        require(sorted(terminal_statuses) == [200, 409],
                f"Conflicting close/cancel transitions must have one winner and one 409 conflict: {terminal_results}")
        status, terminal_final = request(
            "GET", f"/api/v1/open-loops/{conflict_loop_id}", token=token_a,
        )
        require(status == 200 and terminal_final.get("loop", {}).get("status") in {"closed", "cancelled"},
                f"Terminal race did not leave one durable terminal state: {status}: {terminal_final}")
        status, terminal_history = request(
            "GET", f"/api/v1/open-loops/{conflict_loop_id}/history?limit=100", token=token_a,
        )
        terminal_events = [event.get("event_type") for event in terminal_history
                           if event.get("event_type") in {"closure_verified", "cancelled"}]
        require(status == 200 and len(terminal_events) == 1,
                f"Terminal race must persist exactly one terminal audit event: {status}: {terminal_history}")

        # Contradiction reconciliation and verification are separate explicit
        # steps; closing without both must not be inferred from narrative alone.
        status, contradiction_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "contradiction",
                "title": "CI-only conflicting reports",
                "description": "Synthetic conflict requiring explicit reconciliation.",
                "idempotency_key": "postgres-open-loop-reconciliation-001",
                "evidence": [{"kind": "ci_fixture", "text": "Synthetic contradiction workflow only."}],
            },
        )
        require(status == 201, f"Contradiction loop creation failed: {status}: {contradiction_loop}")
        contradiction_id = contradiction_loop["id"]
        status, premature_close = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/close", token=token_a,
            body={"text": "Premature close attempt", "evidence": workflow_source},
        )
        require(status == 409, f"Unreconciled contradiction must not close: {status}: {premature_close}")
        status, reconciliation = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/reconcile", token=token_a,
            body={"text": "Synthetic explicit reconciliation review; disagreement is not treated as semantic proof.",
                  "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-reconciliation-event-001"},
        )
        require(status == 200, f"Contradiction reconciliation failed: {status}: {reconciliation}")
        status, contradiction_verified = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/verify-outcome", token=token_a,
            body={"text": "Reconciliation reviewed against the linked source",
                  "evidence": workflow_source,
                  "verification_rule": "The linked synthetic source record is present and was reviewed.",
                  "idempotency_key": "postgres-open-loop-reconciliation-verify-001"},
        )
        require(status == 200 and contradiction_verified.get("outcome_status") == "outcome_reported",
                f"Contradiction caregiver attestation must remain reported: {status}: {contradiction_verified}")
        # A contradiction cannot be closed by reconciliation or attestation alone.
        # Require a separately persisted pair-level independent-source relationship.
        # Reuse the same persisted pair-level relationship created and verified
        # in the preceding end-to-end workflow; relationship uniqueness is enforced.
        require(workflow_relationship.get("id"),
                "The independently assessed source pair must persist before contradiction corroboration")
        status, contradiction_corroborated = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/corroborate-outcome", token=token_a,
            body={
                "text": "Synthetic independent-source corroboration review for contradiction",
                "evidence": [
                    {"kind": "care_record_source", "evidence_id": professional_source_id},
                    {"kind": "care_record_source", "evidence_id": workflow_second_source_id},
                ],
                "verification_rule": "The contradiction outcome must have two separately originated source records.",
                "idempotency_key": "postgres-open-loop-reconciliation-corroborate-001",
            },
        )
        require(status == 200 and contradiction_corroborated.get("outcome_status") == "outcome_verified",
                f"Contradiction independent corroboration failed: {status}: {contradiction_corroborated}")
        status, contradiction_closed = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/close", token=token_a,
            body={"text": "Synthetic closure after reconciliation and verification",
                  "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-reconciliation-close-001"},
        )
        require(status == 200 and contradiction_closed.get("loop", {}).get("status") == "closed",
                f"Reconciled contradiction did not close: {status}: {contradiction_closed}")
        status, contradiction_history = request(
            "GET", f"/api/v1/open-loops/{contradiction_id}/history?limit=100", token=token_a,
        )
        contradiction_event_types = {event.get("event_type") for event in contradiction_history}
        require(status == 200 and {
            "created", "contradiction_reconciled", "outcome_attested", "outcome_corroborated", "closure_verified",
        }.issubset(contradiction_event_types),
                f"Contradiction audit history is incomplete: {status}: {contradiction_history}")

        # Real PostgreSQL observation-coverage persistence, authorization, and
        # explicit coverage assessment. This never uses the deployed Railway API.
        coverage_start = datetime.now(timezone.utc) - timedelta(days=14)
        coverage_end = datetime.now(timezone.utc) - timedelta(minutes=1)
        coverage_body = {
            "person_id": person_a,
            "subject_type": "mobility",
            "window_start": coverage_start.isoformat(),
            "window_end": coverage_end.isoformat(),
            "coverage_status": "covered",
            "source_evidence_ids": [],
            "rationale": "CI fixture explicitly declares the mobility domain observed throughout this interval.",
        }
        status, saved_coverage = request(
            "POST", "/api/v1/change/coverage", token=token_a, body=coverage_body,
        )
        require(status == 201, f"coverage creation expected 201, got {status}: {saved_coverage}")
        coverage_query = urlencode({
            "subject_type": "mobility",
            "window_start": coverage_start.isoformat(),
            "window_end": coverage_end.isoformat(),
        })
        status, assessed_coverage = request(
            "GET", f"/api/v1/change/coverage/{person_a}?{coverage_query}", token=token_a,
        )
        require(status == 200, f"coverage assessment expected 200, got {status}: {assessed_coverage}")
        require(
            assessed_coverage.get("coverage_status") == "covered"
            and assessed_coverage.get("coverage_fraction") == 1.0
            and any(row.get("id") == saved_coverage.get("id") for row in assessed_coverage.get("records", [])),
            f"coverage assessment did not return persisted complete interval: {assessed_coverage}",
        )
        status, cross_person_coverage = request(
            "GET", f"/api/v1/change/coverage/{person_a}?{coverage_query}", token=token_b,
        )
        require(
            status == 403,
            f"cross-person observation-coverage read must return 403, got {status}: {cross_person_coverage}",
        )

        # Real HTTP + PostgreSQL change/pattern reconciliation after late-arriving
        # source events. These are synthetic CI records, never production care data.
        status, pattern_session = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL pattern E2E person C", "caregiver_name": "PostgreSQL pattern E2E caregiver C"},
        )
        require(status == 201, f"pattern E2E session creation expected 201, got {status}: {pattern_session}")
        token_c = pattern_session["access_token"]
        person_c = pattern_session["person"]["id"]
        caregiver_c = pattern_session["caregiver"]["id"]
        pattern_event_ids = []
        for suffix, days_ago in (("old", 20), ("middle", 10), ("recent", 2)):
            event_time = datetime.now(timezone.utc) - timedelta(days=days_ago)
            status, event_record = request(
                "POST",
                "/api/v1/events",
                token=token_c,
                body={
                    "person_id": person_c,
                    "event_type": "fall",
                    "status": "recorded",
                    "occurred_at": event_time.isoformat(),
                    "title": f"CI-only fall event {suffix}",
                    "description": "Synthetic integration fixture; not a real care report.",
                    "created_by_caregiver_id": caregiver_c,
                    "time_provenance": "ci_fixture",
                    "tags": ["ci_fixture", "change_intelligence"],
                },
            )
            require(status == 200, f"care event create expected 200, got {status}: {event_record}")
            pattern_event_ids.append(event_record["id"])

        status, persisted_patterns = request(
            "GET", f"/api/v1/change/patterns/{person_c}?subject_type=fall", token=token_c,
        )
        require(status == 200, f"pattern list expected 200, got {status}: {persisted_patterns}")
        recurrence_patterns = [
            pattern for pattern in persisted_patterns
            if pattern.get("pattern_type") == "recurrence"
            and pattern.get("is_active") is True
            and set(pattern.get("event_ids") or []).issuperset(pattern_event_ids)
        ]
        require(
            recurrence_patterns,
            "newly recorded source events did not produce a persisted source-linked recurrence pattern",
        )
        persisted_recurrence_id = recurrence_patterns[0]["id"]
        status, cross_person_patterns = request(
            "GET", f"/api/v1/change/patterns/{person_c}?subject_type=fall", token=token_b,
        )
        require(
            status == 403,
            f"cross-person pattern access must return 403, got {status}: {cross_person_patterns}",
        )

        # Real PostgreSQL uncertainty API persistence and record-isolation checks.
        status, created_uncertainty = request("POST", "/api/v1/uncertainty/assessments", token=token_a, body={
            "person_id": person_a, "subject_type": "medication",
            "subject_id": "postgres-e2e-unknown-medication", "field": "current_status",
            "epistemic_state": "unknown", "gap_reason": "not_documented", "known_value": None,
        })
        require(status == 200, f"uncertainty create expected 200, got {status}: {created_uncertainty}")
        require(created_uncertainty.get("epistemic_state") == "unknown"
                and created_uncertainty.get("confidence") is None,
                f"unknown state/confidence contract failed: {created_uncertainty}")
        status, persisted_uncertainty = request(
            "GET", f"/api/v1/uncertainty/assessments/{person_a}?subject_type=medication&subject_id=postgres-e2e-unknown-medication",
            token=token_a,
        )
        require(status == 200 and any(
            item.get("field") == "current_status" and item.get("epistemic_state") == "unknown"
            and item.get("confidence") is None for item in persisted_uncertainty
        ), f"uncertainty assessment did not persist: {status}: {persisted_uncertainty}")
        status, uncertainty_view = request(
            "GET", f"/api/v1/uncertainty/view/{person_a}/medication/postgres-e2e-unknown-medication",
            token=token_a,
        )
        require(status == 200 and uncertainty_view.get("epistemic_state") == "unknown"
                and uncertainty_view.get("known_value") is None,
                f"unknown epistemic view contract failed: {status}: {uncertainty_view}")
        status, cross_uncertainty = request(
            "GET", f"/api/v1/uncertainty/assessments/{person_a}", token=token_b,
        )
        require(status == 403,
                f"cross-person uncertainty access must return 403, got {status}: {cross_uncertainty}")

        observation = "PostgreSQL end-to-end durable observation unique to person A"
        status, captured = request(
            "POST",
            "/api/situation",
            token=token_a,
            body={
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "raw_input": observation,
                "care_session_id": "postgres-e2e-interaction-a",
            },
        )
        require(status == 200, f"care capture expected 200, got {status}: {captured}")
        require(captured.get("person_id") == person_a, "capture response person scope mismatch")
        observation_situation_id = captured.get("situation_id")
        observation_event_id = captured.get("care_event_id")
        observation_evidence_id = captured.get("evidence_id")
        require(
            observation_situation_id and observation_event_id and observation_evidence_id,
            f"care capture did not return its persisted situation/event/evidence identifiers: {captured}",
        )

        status, hydration = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"hydration expected 200, got {status}: {hydration}")
        captured_situation = next(
            (item for item in hydration.get("ui_situations", [])
             if item.get("id") == observation_situation_id),
            None,
        )
        require(
            captured_situation is not None
            and observation_event_id in (captured_situation.get("linked_event_ids") or [])
            and observation_evidence_id in (captured_situation.get("linked_evidence_ids") or []),
            f"capture did not hydrate in its own persisted situation thread: {captured_situation}",
        )
        captured_event = next(
            (item for item in hydration.get("events", [])
             if item.get("id") == observation_event_id),
            None,
        )
        require(
            captured_event is not None
            and captured_event.get("description") == observation
            and observation_evidence_id in (captured_event.get("evidence_ids") or []),
            f"capture source event did not hydrate with its original wording/evidence link: {captured_event}",
        )

        # Authenticated UPDATE must persist in PostgreSQL and be visible on a fresh read.
        updated_name = "PostgreSQL E2E person A updated"
        status, updated = request(
            "PATCH",
            "/api/situation",
            token=token_a,
            body={
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "display_name": updated_name,
            },
        )
        require(status == 200, f"care-record update expected 200, got {status}: {updated}")
        require(
            updated.get("care_recipient_display_name") == updated_name,
            f"update response did not confirm the new display name: {updated}",
        )
        status, reread = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"read after update expected 200, got {status}: {reread}")
        require(
            reread.get("care_recipient_display_name") == updated_name,
            "authenticated update was not persisted to PostgreSQL",
        )

        # Account login on a fresh client/session must recover the same care record.
        # This models a second device without reusing browser localStorage.
        account_email = f"postgres-e2e-continuity-{uuid.uuid4().hex[:12]}@example.test"
        account_password = "CI-only-strong-passphrase-2026!"
        status, account_created = request(
            "POST",
            "/api/v1/accounts",
            token=token_a,
            body={"email": account_email, "password": account_password},
        )
        require(status == 201, f"account linking expected 201, got {status}: {account_created}")

        # Terminate the original session before the independent account login.
        status, original_revoked = request(
            "POST", "/api/v1/sessions/revoke", token=original_session_token, body={},
        )
        require(status == 200 and original_revoked.get("revoked") is True,
                f"Original session termination failed: {status}: {original_revoked}")
        status, original_session_read = request(
            "GET", f"/api/v1/events/{person_a}", token=original_session_token,
        )
        require(status == 401,
                f"Terminated original session must no longer read history: {status}: {original_session_read}")

        status, second_device = request(
            "POST",
            "/api/v1/accounts/login",
            body={"email": account_email, "password": account_password},
        )
        require(status == 200, f"second-device account login expected 200, got {status}: {second_device}")
        require(second_device.get("person_id") == person_a,
                "second-device login did not restore the same care-recipient identity")
        require(second_device.get("caregiver_id") == caregiver_a,
                "second-device login did not restore the same caregiver identity")
        account_id = second_device["account"]["id"]
        token_a = second_device["access_token"]
        status, second_device_read = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"second-device read expected 200, got {status}: {second_device_read}")
        require(second_device_read.get("care_recipient_display_name") == updated_name,
                "second-device login did not retrieve the persisted updated care record")

        # Exercise the exact structured Daily Care -> CareEvent -> Evidence ->
        # Recent History -> LCR -> persisted situation-assessment lifecycle.
        daily_capture_payload = {
            "person_id": person_a,
            "caregiver_id": caregiver_a,
            "medications": [],
            "vitals": [],
            "scales": [],
            "notes": "PostgreSQL E2E caregiver report: sleep was interrupted twice overnight.",
            "capture_idempotency_key": "postgres-daily-capture-restart-001",
        }
        status, daily_capture = request(
            "POST", "/api/v1/daily/log", token=token_a, body=daily_capture_payload,
        )
        require(status == 200 and daily_capture.get("success") is True,
                f"Structured Daily Care capture failed: {status}: {daily_capture}")
        daily_event_id = daily_capture.get("id")
        daily_evidence_ids = daily_capture.get("evidence_ids") or []
        require(daily_event_id and len(daily_evidence_ids) == 1,
                f"Daily Care capture must return one event and one source evidence ID: {daily_capture}")
        daily_evidence_id = daily_evidence_ids[0]

        status, daily_history = request(
            "GET", f"/api/v1/events/{person_a}", token=token_a,
        )
        require(status == 200, f"Recent History event read failed: {status}: {daily_history}")
        history_matches = [row for row in daily_history if row.get("id") == daily_event_id]
        require(len(history_matches) == 1
                and history_matches[0].get("evidence_ids") == [daily_evidence_id],
                f"Recent History did not return the same event/evidence IDs: {history_matches}")

        status, daily_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50", token=token_a,
        )
        require(status == 200, f"Current-care LCR read failed: {status}: {daily_lcr}")
        lcr_matches = [row for row in daily_lcr.get("items", []) if row.get("id") == daily_event_id]
        require(len(lcr_matches) == 1
                and lcr_matches[0].get("evidence_ids") == [daily_evidence_id]
                and lcr_matches[0].get("evidence_link_validation") == "same_care_record_evidence_reference_verified"
                and lcr_matches[0].get("source_label") == "Caregiver"
                and lcr_matches[0].get("source_type") == "caregiver_report",
                f"LCR did not reconcile the same authorized caregiver source: {lcr_matches}")

        # Scenario 01: four natural-language reports across one day must become
        # four durable, separately attributed sources in the same PostgreSQL LCR.
        scenario_reports = [
            "Mum slept poorly last night and seemed more unsettled this morning.",
            "Mum barely touched her lunch today. She drank some water, though.",
            "I think the doctor changed Mum's medication yesterday, but I can't remember the new dose.",
            "Mum was calmer this evening. She ate a little more at dinner and is now resting.",
        ]
        scenario_receipts = []
        for index, report in enumerate(scenario_reports, start=1):
            scenario_payload = {
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "capture_kind": "natural_language_update",
                "capture_idempotency_key": f"postgres-scenario-01-report-{index:02d}",
                "notes": report,
            }
            status, receipt = request(
                "POST", "/api/v1/daily-care/entries", token=token_a, body=scenario_payload,
            )
            require(
                status == 200 and receipt.get("success") is True
                and receipt.get("id") and len(receipt.get("evidence_ids") or []) == 1,
                f"Scenario 01 report {index} was not durably accepted: {status}: {receipt}",
            )
            scenario_receipts.append((scenario_payload, receipt))

        scenario_event_ids = [receipt["id"] for _, receipt in scenario_receipts]
        scenario_evidence_ids = [receipt["evidence_ids"][0] for _, receipt in scenario_receipts]
        require(
            len(set(scenario_event_ids)) == 4 and len(set(scenario_evidence_ids)) == 4,
            "Scenario 01 reports must remain separate source events and evidence records",
        )

        with SessionLocal() as db:
            scenario_events = [
                db.query(CareEvent).filter_by(id=event_id, person_id=person_a).one()
                for event_id in scenario_event_ids
            ]
            require(
                [event.recorded_at for event in scenario_events]
                == sorted(event.recorded_at for event in scenario_events),
                "Scenario 01 source reports were not recorded in chronological order",
            )
            for index, ((scenario_payload, _receipt), event, evidence_id) in enumerate(
                zip(scenario_receipts, scenario_events, scenario_evidence_ids), start=1,
            ):
                require(
                    json.loads(event.description).get("notes") == scenario_payload["notes"]
                    and event.created_by_caregiver_id == caregiver_a,
                    f"Scenario 01 report {index} did not preserve exact wording and caregiver attribution",
                )
                source = db.execute(
                    text("""
                        SELECT person_id, reported_by_caregiver_id, source_text
                        FROM evidence WHERE id = :evidence_id
                    """),
                    {"evidence_id": evidence_id},
                ).mappings().one()
                source_payload = json.loads(source["source_text"])
                require(
                    source["person_id"] == person_a
                    and source["reported_by_caregiver_id"] == caregiver_a
                    and source_payload["original_capture"]["notes"] == scenario_payload["notes"],
                    f"Scenario 01 report {index} did not preserve its exact scoped source evidence",
                )

            medication_assertions = db.query(CareAssertion).filter_by(
                person_id=person_a, subject_type="medication",
            ).count()
            require(
                medication_assertions == 0,
                "An uncertain medication report must not create a medication assertion",
            )

        status, scenario_history = request(
            "GET", f"/api/v1/events/{person_a}", token=token_a,
        )
        require(status == 200, f"Scenario 01 history retrieval failed: {status}: {scenario_history}")
        require(
            all(
                sum(1 for row in scenario_history if row.get("id") == event_id) == 1
                for event_id in scenario_event_ids
            ),
            "Scenario 01 source reports were not individually retrievable from care history",
        )

        status, scenario_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50", token=token_a,
        )
        require(status == 200, f"Scenario 01 LCR retrieval failed: {status}: {scenario_lcr}")
        scenario_lcr_rows = [
            row for row in scenario_lcr.get("items", [])
            if row.get("id") in set(scenario_event_ids)
        ]
        require(
            len(scenario_lcr_rows) == 4
            and all(row.get("source_type") == "caregiver_report" for row in scenario_lcr_rows)
            and all(row.get("epistemic_status") == "caregiver_reported" for row in scenario_lcr_rows),
            "Scenario 01 reports did not reconcile into the existing LCR with caregiver provenance",
        )

        # Retrying the first report must return the same source IDs, not duplicate
        # the event, evidence or extraction analysis.
        first_scenario_payload, first_scenario_receipt = scenario_receipts[0]
        status, replayed_scenario = request(
            "POST", "/api/v1/daily-care/entries", token=token_a, body=first_scenario_payload,
        )
        require(
            status == 200
            and replayed_scenario.get("id") == first_scenario_receipt["id"]
            and replayed_scenario.get("evidence_ids") == first_scenario_receipt["evidence_ids"]
            and replayed_scenario.get("idempotent_replay") is True,
            f"Scenario 01 retry did not return the original source IDs: {status}: {replayed_scenario}",
        )

        foreign_scenario_status, _ = request(
            "GET", f"/api/v1/events/{person_a}", token=token_b,
        )
        require(
            foreign_scenario_status == 403,
            f"Another caregiver session accessed Scenario 01 history: {foreign_scenario_status}",
        )
        print(
            "PASS: Daily Care Scenario 01 four natural-language reports, exact PostgreSQL source "
            "preservation, caregiver/person scoping, idempotent retry, History and LCR retrieval."
        )

        # Exercise the caregiver review lifecycle against this real PostgreSQL
        # Daily Care event. Candidate generation is seeded as a persisted extraction
        # so this test does not depend on an external Qwen model being available.
        # Stop any queued extraction for this event before seeding the fixture: the
        # background worker is allowed to replace a processing analysis, and must
        # not race the deterministic review-lifecycle assertions below.
        source_db = SessionLocal()
        try:
            pending_jobs = source_db.query(QwenBackgroundJob).filter(
                QwenBackgroundJob.person_id == person_a,
                QwenBackgroundJob.caregiver_id == caregiver_a,
                QwenBackgroundJob.workflow == "daily_capture_extraction",
                QwenBackgroundJob.source_event_ids_json == json.dumps([daily_event_id]),
                QwenBackgroundJob.status.in_(("queued", "processing")),
            ).with_for_update().all()
            for pending_job in pending_jobs:
                pending_job.status = "cancelled"
                pending_job.error_code = "ci_review_fixture_seeded"
                pending_job.locked_at = None
            source_db.commit()
        finally:
            source_db.close()

        source_db = SessionLocal()
        try:
            source_event = source_db.query(CareEvent).filter_by(id=daily_event_id, person_id=person_a).one()
            original_event_description = source_event.description
            original_event_occurred_at = source_event.occurred_at
            analysis = CaptureAnalysis(
                person_id=person_a,
                caregiver_id=caregiver_a,
                source_event_id=daily_event_id,
                source_evidence_id=daily_evidence_id,
                source_text=original_event_description or daily_capture_payload["notes"],
                extraction_status="available",
                candidates={"observations": [
                    {
                        "subject": "sleep",
                        "observation": "Generated suggestion: interrupted sleep.",
                        "source_quote": daily_capture_payload["notes"],
                        "review_status": "pending_review",
                    },
                    {
                        "subject": "mood",
                        "observation": "Generated suggestion that the caregiver did not confirm.",
                        "source_quote": daily_capture_payload["notes"],
                        "review_status": "pending_review",
                    },
                ]},
                review_history=[],
            )
            source_db.add(analysis)
            source_db.commit()
            source_db.refresh(analysis)
            daily_analysis_id = analysis.id
            daily_analysis_count_before_retry = source_db.query(CaptureAnalysis).filter_by(
                person_id=person_a, source_event_id=daily_event_id,
            ).count()
        finally:
            source_db.close()

        status, corrected_review = request(
            "POST",
            f"/api/v1/capture/analyses/{daily_analysis_id}/review",
            token=token_a,
            body={
                "candidate_index": 0,
                "decision": "correct",
                "corrected_subject": "sleep",
                "corrected_observation": "Caregiver corrected the report: sleep was interrupted twice overnight.",
            },
        )
        require(status == 200
                and corrected_review.get("written_to_lcr") is False
                and corrected_review.get("candidate", {}).get("review_status") == "corrected_pending_approval",
                f"Daily Care correction must remain pending: {status}: {corrected_review}")
        source_db = SessionLocal()
        try:
            require(source_db.query(CareAssertion).filter_by(person_id=person_a).count() == 0,
                    "Corrected-but-unapproved Daily Care candidate must not create an LCR assertion")
        finally:
            source_db.close()

        status, accepted_review = request(
            "POST",
            f"/api/v1/capture/analyses/{daily_analysis_id}/review",
            token=token_a,
            body={"candidate_index": 0, "decision": "accept"},
        )
        require(status == 200
                and accepted_review.get("written_to_lcr") is True
                and accepted_review.get("assertion_id"),
                f"Explicit Daily Care acceptance must create an LCR assertion: {status}: {accepted_review}")
        accepted_assertion_id = accepted_review["assertion_id"]

        status, repeated_acceptance = request(
            "POST",
            f"/api/v1/capture/analyses/{daily_analysis_id}/review",
            token=token_a,
            body={"candidate_index": 0, "decision": "accept"},
        )
        require(status == 409, f"Repeated acceptance must not create a duplicate assertion: {status}: {repeated_acceptance}")

        status, rejected_review = request(
            "POST",
            f"/api/v1/capture/analyses/{daily_analysis_id}/review",
            token=token_a,
            body={"candidate_index": 1, "decision": "reject"},
        )
        require(status == 200
                and rejected_review.get("written_to_lcr") is False
                and rejected_review.get("candidate", {}).get("review_status") == "rejected_by_caregiver",
                f"Daily Care rejection must remain excluded from LCR: {status}: {rejected_review}")
        status, rejected_retry = request(
            "POST",
            f"/api/v1/capture/analyses/{daily_analysis_id}/review",
            token=token_a,
            body={"candidate_index": 1, "decision": "accept"},
        )
        require(status == 409, f"Rejected candidate must remain final after retry: {status}: {rejected_retry}")

        source_db = SessionLocal()
        try:
            assertions = source_db.query(CareAssertion).filter_by(
                person_id=person_a, source_event_id=daily_event_id,
            ).all()
            require(len(assertions) == 1 and assertions[0].id == accepted_assertion_id,
                    f"Exactly one source-linked assertion must persist: {[row.id for row in assertions]}")
            assertion = assertions[0]
            require(assertion.evidence_ids == [daily_evidence_id]
                    and assertion.created_by_caregiver_id == caregiver_a
                    and assertion.assertion_status == "caregiver_reviewed"
                    and assertion.observed_at == original_event_occurred_at,
                    "Accepted assertion lost evidence, caregiver attribution, review status or event time")
            persisted_analysis = source_db.query(CaptureAnalysis).filter_by(id=daily_analysis_id).one()
            require(len(persisted_analysis.review_history) == 3,
                    "Correction, acceptance and rejection must all be preserved in review history")
            require(persisted_analysis.review_history[0]["original_candidate"]["observation"] == "Generated suggestion: interrupted sleep.",
                    "Correction history did not preserve the generated original candidate")
            require(persisted_analysis.review_history[0]["reviewed_by_caregiver_id"] == caregiver_a,
                    "Correction history lost caregiver attribution")
            persisted_candidate = persisted_analysis.candidates["observations"][0]
            require(persisted_candidate["observation"] == "Generated suggestion: interrupted sleep."
                    and persisted_candidate["corrected_observation"] == "Caregiver corrected the report: sleep was interrupted twice overnight.",
                    "Caregiver correction overwrote the original Daily Care candidate instead of preserving both values")
            persisted_event = source_db.query(CareEvent).filter_by(id=daily_event_id).one()
            require(persisted_event.description == original_event_description,
                    "Caregiver review mutated the original Daily Care observation")
        finally:
            source_db.close()

        status, reviewed_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50", token=token_a,
        )
        require(status == 200, f"Reviewed LCR read failed: {status}: {reviewed_lcr}")
        reviewed_assertions = [
            row for row in reviewed_lcr.get("items", [])
            if row.get("id") == accepted_assertion_id
        ]
        require(len(reviewed_assertions) == 1,
                f"Accepted Daily Care assertion was not returned by LCR retrieval: {reviewed_assertions}")

        status, refreshed = request("POST", "/api/v1/sessions/refresh", token=token_a)
        require(status == 200 and refreshed.get("access_token"),
                f"Daily Care session refresh failed: {status}: {refreshed}")
        token_a = refreshed["access_token"]
        status, refreshed_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50", token=token_a,
        )
        require(status == 200 and any(
            row.get("id") == accepted_assertion_id for row in refreshed_lcr.get("items", [])
        ), "Accepted Daily Care assertion did not survive session refresh and retrieval")

        status, foreign_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50", token=token_b,
        )
        require(status == 200 and not any(
            row.get("id") == accepted_assertion_id for row in foreign_lcr.get("items", [])
        ), "A different care recipient session received another person's accepted assertion")

        status, daily_evidence = request(
            "GET", f"/api/v1/evidence/{person_a}", token=token_a,
        )
        require(status == 200 and any(
            row.get("id") == daily_evidence_id
            and row.get("epistemic_status") == "caregiver_reported"
            for row in daily_evidence
        ), f"Authorized caregiver source evidence could not be retrieved: {status}: {daily_evidence}")

        status, daily_assessment = request(
            "POST", "/api/v1/care-continuity/situation-understanding/reassess", token=token_a,
        )
        require(status == 200, f"Situation assessment rebuild failed: {status}: {daily_assessment}")
        assessment_records = [
            row for row in daily_assessment.get("assessment", {}).get("current_care_reality", {}).get("records", [])
            if row.get("id") == daily_event_id
        ]
        require(len(assessment_records) == 1
                and assessment_records[0].get("evidence_ids") == [daily_evidence_id]
                and assessment_records[0].get("source_label") == "Caregiver",
                f"Situation assessment did not rebuild from the verified source: {assessment_records}")

        status, daily_replay = request(
            "POST", "/api/v1/daily/log", token=token_a, body=daily_capture_payload,
        )
        require(status == 200
                and daily_replay.get("id") == daily_event_id
                and daily_replay.get("evidence_ids") == [daily_evidence_id]
                and daily_replay.get("idempotent_replay") is True
                and daily_replay.get("qwen_analysis_id") == daily_analysis_id
                and daily_replay.get("qwen_review_required") is False
                and [item.get("review_status") for item in daily_replay.get("qwen_candidates", {}).get("observations", [])]
                    == ["accepted_by_caregiver", "rejected_by_caregiver"],
                f"Unchanged Daily Care retry must restore the persisted candidate analysis and original IDs: {status}: {daily_replay}")

        retry_db = SessionLocal()
        try:
            daily_analysis_count_after_retry = retry_db.query(CaptureAnalysis).filter_by(
                person_id=person_a, source_event_id=daily_event_id,
            ).count()
            require(daily_analysis_count_after_retry == daily_analysis_count_before_retry,
                    "Idempotent Daily Care retry created a duplicate persisted candidate analysis")
        finally:
            retry_db.close()
        changed_daily_capture = {
            **daily_capture_payload,
            "notes": "Different report must not reuse the original request key.",
        }
        status, changed_retry = request(
            "POST", "/api/v1/daily/log", token=token_a, body=changed_daily_capture,
        )
        require(status == 409,
                f"Changed content under the same request key must conflict: {status}: {changed_retry}")

        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_b)
        require(status == 403, f"cross-care-record evidence access must return 403, got {status}")
        status, _ = request("GET", f"/api/v1/evidence/{person_a}")
        require(status == 401, f"unauthenticated evidence access must return 401, got {status}")

        status, recovery_session = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL E2E recovery person", "caregiver_name": "PostgreSQL E2E recovery caregiver"},
        )
        require(status == 201, f"recovery session creation expected 201, got {status}: {recovery_session}")
        recovery_token = recovery_session["access_token"]
        status, revoked = request("POST", "/api/v1/sessions/revoke", body={}, token=recovery_token)
        require(status == 200 and revoked.get("revoked") is True,
                f"session revocation expected success, got {status}: {revoked}")
        status, _ = request("GET", f"/api/v1/evidence/{recovery_session['person']['id']}", token=recovery_token)
        require(status == 401, f"revoked token must be rejected, got {status}")
        # Revoking one capability must not break another caregiver's valid session.
        status, independent_evidence_read = request("GET", f"/api/v1/evidence/{person_a}", token=token_a)
        require(
            status == 200,
            f"valid independent session failed after revocation, got {status}: {independent_evidence_read}",
        )

        status, cross_write = request(
            "POST",
            "/api/situation",
            token=token_b,
            body={
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "raw_input": "This cross-recipient write must be rejected",
                "care_session_id": "postgres-e2e-cross-write",
            },
        )
        require(status == 403, f"cross-care-record write must return 403, got {status}: {cross_write}")

        token_before_refresh = token_a
        status, refreshed = request("POST", "/api/v1/sessions/refresh", token=token_before_refresh)
        require(status == 200, f"token refresh expected 200, got {status}: {refreshed}")
        token_a = refreshed["access_token"]
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_before_refresh)
        require(status == 401, f"rotated token should be rejected, got {status}")
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_a)
        require(status == 200, f"new token should retain record access, got {status}")

        # Open Loop Engine: real PostgreSQL persistence, idempotent retry, history,
        # cross-person isolation, and concurrent append integrity. All fixtures are synthetic.
        open_loop_payload = {
            "loop_type": "awaiting_response",
            "title": "CI-only clinic callback",
            "description": "Synthetic test: callback has not yet been confirmed.",
            "request_text": "Please confirm the appointment date.",
            "recipient_label": "CI-only clinic",
            "idempotency_key": "postgres-open-loop-create-retry-001",
            "evidence": [{"kind": "ci_fixture", "text": "Synthetic integration test only."}],
        }
        status, open_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body=open_loop_payload,
        )
        require(status == 201, f"Open Loop creation expected 201, got {status}: {open_loop}")
        open_loop_id = open_loop["id"]
        status, open_loop_retry = request(
            "POST", "/api/v1/open-loops", token=token_a, body=open_loop_payload,
        )
        require(status == 201 and open_loop_retry.get("id") == open_loop_id,
                f"Open Loop idempotent create retry created a duplicate or failed: {status}: {open_loop_retry}")
        changed_retry_payload = {**open_loop_payload, "title": "Different title with the same idempotency key"}
        status, changed_retry = request(
            "POST", "/api/v1/open-loops", token=token_a, body=changed_retry_payload,
        )
        require(status == 409,
                f"Reusing a create idempotency key with a different core payload must return 409: {status}: {changed_retry}")
        # Race two identical create requests. A unique database constraint plus
        # retry recovery must converge on one durable loop instead of returning 500.
        concurrent_create_payload = {
            "loop_type": "awaiting_response",
            "title": "CI-only concurrent idempotency callback",
            "description": "Synthetic race test for duplicate create retries.",
            "request_text": "Confirm the synthetic callback.",
            "recipient_label": "CI-only test recipient",
            "idempotency_key": "postgres-open-loop-concurrent-create-001",
            "evidence": [{"kind": "ci_fixture", "text": "Concurrent idempotency race only."}],
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_creates = list(pool.map(
                lambda _: request("POST", "/api/v1/open-loops", token=token_a, body=concurrent_create_payload),
                range(2),
            ))
        require(all(status == 201 for status, _ in concurrent_creates),
                f"Concurrent duplicate Open Loop creates must both resolve successfully: {concurrent_creates}")
        concurrent_ids = {payload.get("id") for _, payload in concurrent_creates}
        require(len(concurrent_ids) == 1 and None not in concurrent_ids,
                f"Concurrent duplicate creates did not converge to one loop: {concurrent_creates}")
        concurrent_loop_id = next(iter(concurrent_ids))
        status, open_loop_list = request("GET", "/api/v1/open-loops", token=token_a)
        require(status == 200 and sum(1 for item in open_loop_list.get("items", [])
                                      if item.get("id") == open_loop_id) == 1
                and sum(1 for item in open_loop_list.get("items", [])
                        if item.get("id") == concurrent_loop_id) == 1,
                f"Open Loop idempotency created duplicate list records: {status}: {open_loop_list}")
        status, cross_person_loop = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_b,
        )
        require(status == 404,
                f"Cross-person Open Loop read must return 404, got {status}: {cross_person_loop}")

        # Replaying the same mutation key must not duplicate an action event or evidence.
        retry_action = {
            "text": "CI-only action with stable retry key",
            "evidence": [{"kind": "ci_fixture", "text": "same request retried"}],
            "idempotency_key": "postgres-open-loop-action-retry-concurrent-002",
        }
        first_action_status, _ = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
            token=token_a, body=retry_action,
        )
        retry_action_status, _ = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
            token=token_a, body=retry_action,
        )
        require(first_action_status == 200 and retry_action_status == 200,
                f"Same-key Open Loop action retry failed: {first_action_status}, {retry_action_status}")
        status, after_action_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        retry_actions = [item for item in after_action_retry.get("loop", {}).get("action_evidence", [])
                         if item.get("text") == retry_action["text"]]
        require(status == 200 and len(retry_actions) == 1,
                f"Same-key action retry duplicated evidence: {retry_actions}")

        # Two simultaneous retries with the same mutation idempotency key
        # must converge to one event/evidence append (not one success plus a 500).
        concurrent_retry_payload = {
            "text": "CI-only concurrent retry action",
            "evidence": [{"kind": "ci_fixture", "text": "concurrent same-key retry"}],
            "idempotency_key": "postgres-open-loop-concurrent-action-retry-001",
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_action_retries = list(pool.map(
                lambda _: request(
                    "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
                    token=token_a, body=concurrent_retry_payload,
                ),
                range(2),
            ))
        require(all(status == 200 for status, _ in concurrent_action_retries),
                f"Concurrent same-key action retries must both return successfully: {concurrent_action_retries}")
        status, after_concurrent_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        same_key_actions = [
            item for item in after_concurrent_retry.get("loop", {}).get("action_evidence", [])
            if item.get("text") == concurrent_retry_payload["text"]
        ]
        require(status == 200 and len(same_key_actions) == 1,
                f"Concurrent same-key retries duplicated action evidence: {same_key_actions}")

        # Concurrent append attempts must not silently overwrite one another.
        # This is intentionally a strict invariant; if it fails, the database-backed
        # test should remain red rather than claiming concurrency safety.
        action_payloads = [
            {"text": "CI-only action A", "evidence": [{"kind": "ci_fixture", "text": "action-A"}]},
            {"text": "CI-only action B", "evidence": [{"kind": "ci_fixture", "text": "action-B"}]},
        ]
        def append_open_loop_action(payload):
            return request(
                "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
                token=token_a, body=payload,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            action_results = list(pool.map(append_open_loop_action, action_payloads))
        require(all(status == 200 for status, _ in action_results),
                f"Concurrent Open Loop action appends failed: {action_results}")
        status, open_loop_after_actions = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        require(status == 200, f"Open Loop read after concurrent actions failed: {status}: {open_loop_after_actions}")
        saved_actions = open_loop_after_actions["loop"].get("action_evidence", [])
        require(len(saved_actions) == 4 and
                {item.get("text") for item in saved_actions} >= {
                    "CI-only action with stable retry key",
                    "CI-only concurrent retry action",
                    "CI-only action A",
                    "CI-only action B",
                },
                f"Concurrent Open Loop updates lost an append or idempotent retry duplicated one: {saved_actions}")

        # Retrying a mutation with the same idempotency key must return the
        # committed result and must not duplicate evidence or audit history.
        # Reuse the exact original request and key; changing the payload while
        # reusing the key must be rejected (asserted above).
        retry_payload = dict(retry_action)
        def retry_action(_):
            return request(
                "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
                token=token_a, body=retry_payload,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            retry_results = list(pool.map(retry_action, (1, 2)))
        require(all(status == 200 for status, _ in retry_results),
                f"Concurrent idempotent action retries failed: {retry_results}")
        status, loop_after_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        require(status == 200, f"Open Loop read after idempotent retry failed: {status}: {loop_after_retry}")
        retry_evidence_count = sum(
            1 for item in loop_after_retry["loop"].get("action_evidence", [])
            if item.get("text") == retry_payload["text"]
        )
        require(retry_evidence_count == 1,
                f"Idempotent action retry duplicated evidence: {loop_after_retry['loop'].get('action_evidence')}")
        status, history_after_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        retry_event_count = sum(
            1 for event in history_after_retry
            if event.get("event_type") == "action_recorded"
            and any(item.get("text") == "same request retried" for item in event.get("evidence", []))
        )
        require(status == 200 and retry_event_count == 1,
                f"Idempotent action retry duplicated audit events: {status}: {history_after_retry}")
        status, conflicting_key_reuse = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/responses", token=token_a,
            body={"text": "Wrong event for same key", "evidence": [{"kind": "ci_fixture", "text": "wrong event"}],
                  "idempotency_key": retry_payload["idempotency_key"]},
        )
        require(status == 409,
                f"Reusing an idempotency key for a different event must return 409: {status}: {conflicting_key_reuse}")
        status, recorded_response = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/responses", token=token_a,
            body={"text": "CI-only response received", "evidence": [{"kind": "ci_fixture", "text": "response"}]},
        )
        require(status == 200, f"Open Loop response event expected 200, got {status}: {recorded_response}")
        status, open_loop_history = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        require(status == 200 and any(
            event.get("event_type") == "response_recorded" for event in open_loop_history
        ), f"Open Loop response audit event missing: {status}: {open_loop_history}")

        # Conflicting terminal transitions must serialize. Exactly one of close
        # and cancel may win; the loser must receive 409 rather than overwrite it.
        status, lifecycle_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "awaiting_response",
                "title": "CI-only terminal transition race",
                "description": "Synthetic concurrency test for close versus cancel.",
                "request_text": "Confirm the synthetic outcome before closing.",
                "recipient_label": "CI-only test recipient",
                "idempotency_key": "postgres-open-loop-terminal-race-create",
                "evidence": [{"kind": "ci_fixture", "text": "Terminal transition race fixture."}],
            },
        )
        require(status == 201, f"Terminal race loop creation failed: {status}: {lifecycle_loop}")
        lifecycle_loop_id = lifecycle_loop["id"]
        status, _ = request(
            "POST", f"/api/v1/open-loops/{lifecycle_loop_id}/responses", token=token_a,
            body={"text": "CI-only response before terminal race",
                  "evidence": [{"kind": "ci_fixture", "text": "Response recorded before competing transitions."}],
                  "idempotency_key": "postgres-open-loop-terminal-race-response"},
        )
        require(status == 200, f"Terminal race prerequisite response failed: {status}")
        close_payload = {
            "text": "CI-only close attempt",
            "evidence": [{"kind": "ci_fixture", "text": "Close attempt evidence."}],
            "idempotency_key": "postgres-open-loop-terminal-race-close",
        }
        cancel_payload = {
            "text": "CI-only cancel attempt",
            "evidence": [{"kind": "ci_fixture", "text": "Cancel attempt evidence."}],
            "idempotency_key": "postgres-open-loop-terminal-race-cancel",
        }
        def terminal_transition(item):
            endpoint, payload = item
            return request(
                "POST", f"/api/v1/open-loops/{lifecycle_loop_id}/{endpoint}",
                token=token_a, body=payload,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            terminal_results = list(pool.map(
                terminal_transition, [("close", close_payload), ("cancel", cancel_payload)],
            ))
        terminal_statuses = sorted(status for status, _ in terminal_results)
        require(terminal_statuses == [200, 409],
                f"Concurrent close/cancel must have exactly one winner and one conflict: {terminal_results}")
        status, lifecycle_after_race = request(
            "GET", f"/api/v1/open-loops/{lifecycle_loop_id}", token=token_a,
        )
        final_status = lifecycle_after_race.get("loop", {}).get("status")
        require(status == 200 and final_status in {"closed", "cancelled"},
                f"Concurrent terminal transitions left an invalid state: {status}: {lifecycle_after_race}")
        status, lifecycle_history = request(
            "GET", f"/api/v1/open-loops/{lifecycle_loop_id}/history", token=token_a,
        )
        terminal_events = [
            event.get("event_type") for event in lifecycle_history
            if event.get("event_type") in {"closure_verified", "cancelled"}
        ]
        require(status == 200 and len(terminal_events) == 1,
                f"Conflicting terminal transitions must produce exactly one terminal audit event: {status}: {lifecycle_history}")

        # Exercise the complete reminder lifecycle on PostgreSQL before a real
        # Uvicorn restart. Every record must remain scoped to its server-issued
        # caregiver identity and be recoverable through authenticated HTTP.
        reminder_due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        reminder_payload = {
            "person_id": person_a,
            "title": "PostgreSQL E2E open reminder",
            "note": "Synthetic persistence fixture only.",
            "due_at": reminder_due_at,
        }
        reminder_status, open_reminder = request(
            "POST", "/api/v1/reminders", body=reminder_payload, token=token_a,
        )
        require(reminder_status == 201 and open_reminder.get("status") == "open",
                f"PostgreSQL reminder creation failed: {reminder_status}: {open_reminder}")
        open_reminder_id = open_reminder["id"]
        require(open_reminder.get("created_by_caregiver_id") == caregiver_a,
                "Reminder creator was not derived from the authenticated caregiver")

        snooze_until = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        snooze_status, snoozed_reminder = request(
            "PATCH", f"/api/v1/reminders/{open_reminder_id}/snooze",
            body={"snooze_until": snooze_until}, token=token_a,
        )
        require(snooze_status == 200
                and snoozed_reminder.get("status") == "open"
                and snoozed_reminder.get("snooze_count") == 1
                and snoozed_reminder.get("snoozed_by_caregiver_id") == caregiver_a,
                f"PostgreSQL reminder snooze lifecycle failed: {snooze_status}: {snoozed_reminder}")

        completion_status, completed_reminder = request(
            "PATCH", f"/api/v1/reminders/{open_reminder_id}/complete",
            body={"completion_note": "Synthetic completion after snooze."}, token=token_a,
        )
        require(completion_status == 200
                and completed_reminder.get("status") == "completed"
                and completed_reminder.get("completed_by_caregiver_id") == caregiver_a
                and completed_reminder.get("completion_note") == "Synthetic completion after snooze.",
                f"PostgreSQL reminder completion failed: {completion_status}: {completed_reminder}")

        cancel_status, cancelled_reminder = request(
            "POST", "/api/v1/reminders", body={
                **reminder_payload,
                "title": "PostgreSQL E2E cancelled reminder",
                "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            }, token=token_a,
        )
        require(cancel_status == 201, f"Second reminder creation failed: {cancel_status}: {cancelled_reminder}")
        cancelled_reminder_id = cancelled_reminder["id"]
        cancel_status, cancelled_reminder = request(
            "PATCH", f"/api/v1/reminders/{cancelled_reminder_id}/cancel", token=token_a,
        )
        require(cancel_status == 200
                and cancelled_reminder.get("status") == "cancelled"
                and cancelled_reminder.get("cancelled_by_caregiver_id") == caregiver_a,
                f"PostgreSQL reminder cancellation failed: {cancel_status}: {cancelled_reminder}")

        foreign_reminder_list_status, foreign_reminder_list = request(
            "GET", f"/api/v1/reminders/{person_a}", token=token_b,
        )
        require(foreign_reminder_list_status == 403,
                f"Another care record read the reminder list: {foreign_reminder_list_status}: {foreign_reminder_list}")
        foreign_reminder_status, foreign_reminder = request(
            "PATCH", f"/api/v1/reminders/{open_reminder_id}/complete",
            body={}, token=token_b,
        )
        require(foreign_reminder_status == 404,
                f"Another care record accessed a reminder by ID: {foreign_reminder_status}: {foreign_reminder}")

        reminder_list_status, reminder_list = request(
            "GET", f"/api/v1/reminders/{person_a}", token=token_a,
        )
        require(reminder_list_status == 200
                and {item.get("id") for item in reminder_list}
                    >= {open_reminder_id, cancelled_reminder_id}
                and next(item for item in reminder_list if item.get("id") == open_reminder_id).get("status") == "completed"
                and next(item for item in reminder_list if item.get("id") == cancelled_reminder_id).get("status") == "cancelled",
                f"PostgreSQL reminder lifecycle did not persist before restart: {reminder_list_status}: {reminder_list}")

        stop_server(server)
        server = start_server()

        # After the backend restart, authenticate again with the account
        # credentials from a fresh request context. Do not reuse token_a or
        # browser/session storage from before the restart.
        status, post_restart_login = request(
            "POST",
            "/api/v1/accounts/login",
            body={"email": account_email, "password": account_password},
        )
        require(status == 200, f"Independent account login after restart failed: {status}: {post_restart_login}")
        require(post_restart_login.get("person_id") == person_a
                and post_restart_login.get("caregiver_id") == caregiver_a
                and post_restart_login.get("account", {}).get("id") == account_id
                and post_restart_login.get("session_id") != second_device.get("session_id")
                and post_restart_login.get("access_token") != second_device.get("access_token"),
                "Post-restart independent login changed the account/person/caregiver identity or reused the old session")
        token_a = post_restart_login["access_token"]
        post_restart_reminder_status, post_restart_reminders = request(
            "GET", f"/api/v1/reminders/{person_a}", token=token_a,
        )
        persisted_reminders = {
            item.get("id"): item for item in (post_restart_reminders or [])
        }
        require(
            post_restart_reminder_status == 200
            and open_reminder_id in persisted_reminders
            and cancelled_reminder_id in persisted_reminders
            and persisted_reminders[open_reminder_id].get("status") == "completed"
            and persisted_reminders[open_reminder_id].get("snooze_count") == 1
            and persisted_reminders[open_reminder_id].get("completion_note") == "Synthetic completion after snooze."
            and persisted_reminders[cancelled_reminder_id].get("status") == "cancelled",
            f"Reminder lifecycle did not survive Uvicorn restart and fresh account login: {post_restart_reminder_status}: {post_restart_reminders}",
        )
        status, post_restart_identity = request("GET", "/api/situation", token=token_a)
        require(status == 200
                and post_restart_identity.get("care_recipient_display_name") == updated_name,
                f"Independent login did not restore the original care record after restart: {status}: {post_restart_identity}")

        status, post_restart_history = request(
            "GET", f"/api/v1/events/{person_a}", token=token_a,
        )
        require(status == 200, f"Recent History read after restart failed: {status}: {post_restart_history}")
        post_restart_history_matches = [
            row for row in post_restart_history if row.get("id") == daily_event_id
        ]
        require(len(post_restart_history_matches) == 1
                and post_restart_history_matches[0].get("evidence_ids") == [daily_evidence_id],
                f"Original Recent History event/evidence IDs changed after restart/login: {post_restart_history_matches}")

        status, post_restart_evidence = request(
            "GET", f"/api/v1/evidence/{person_a}", token=token_a,
        )
        require(status == 200 and any(
            row.get("id") == daily_evidence_id
            and row.get("epistemic_status") == "caregiver_reported"
            for row in post_restart_evidence
        ), f"Original source evidence was not restored after restart/login: {status}: {post_restart_evidence}")

        status, post_restart_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50", token=token_a,
        )
        require(status == 200, f"LCR read after restart/login failed: {status}: {post_restart_lcr}")
        post_restart_lcr_matches = [
            row for row in post_restart_lcr.get("items", [])
            if row.get("id") == daily_event_id
        ]
        require(len(post_restart_lcr_matches) == 1
                and post_restart_lcr_matches[0].get("evidence_ids") == [daily_evidence_id]
                and post_restart_lcr_matches[0].get("source_label") == "Caregiver",
                f"LCR did not restore the original source record after restart/login: {post_restart_lcr_matches}")

        post_restart_accepted_assertions = [
            row for row in post_restart_lcr.get("items", [])
            if row.get("id") == accepted_assertion_id
        ]
        require(len(post_restart_accepted_assertions) == 1
                and post_restart_accepted_assertions[0].get("evidence_ids") == [daily_evidence_id],
                "Accepted Daily Care assertion did not survive PostgreSQL restart and a new authenticated account session")
        require(not any(
            row.get("description") == "Generated suggestion that the caregiver did not confirm."
            for row in post_restart_lcr.get("items", [])
        ), "Rejected Daily Care candidate appeared in LCR after restart/login")

        status, post_restart_assessment = request(
            "GET", "/api/v1/care-continuity/situation-understanding", token=token_a,
        )
        persisted_assessment = post_restart_assessment.get("assessment", {}) if status == 200 else {}
        persisted_assessment_records = [
            row for row in persisted_assessment.get("current_care_reality", {}).get("records", [])
            if row.get("id") == daily_event_id
        ]
        require(status == 200 and len(persisted_assessment_records) == 1
                and persisted_assessment_records[0].get("evidence_ids") == [daily_evidence_id],
                f"Persisted situation assessment did not restore the original source after restart/login: {status}: {post_restart_assessment}")

        status, foreign_after_restart = request(
            "GET", f"/api/v1/events/{person_a}", token=token_b,
        )
        require(status == 403,
                f"Another person's session accessed Recent History after restart; expected 403, got {status}: {foreign_after_restart}")

        status, professional_after_restart = request(
            "GET", f"/api/v1/open-loops/{professional_loop_id}", token=token_a,
        )
        persisted_professional = professional_after_restart.get("loop", {}) if status == 200 else {}
        require(status == 200 and persisted_professional.get("status") == "closed"
                and any(
                    isinstance(entry, dict) and any(
                        isinstance(source, dict) and source.get("evidence_id") == professional_source_id
                        for source in (entry.get("evidence") or [])
                    )
                    for entry in persisted_professional.get("response_evidence", [])
                ),
                f"Source-backed professional response/closure did not persist across restart: {status}: {professional_after_restart}")

        status, open_loop_after_restart = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        require(status == 200, f"Open Loop read after Uvicorn restart expected 200, got {status}: {open_loop_after_restart}")
        persisted_loop = open_loop_after_restart.get("loop", {})
        require(persisted_loop.get("id") == open_loop_id
                and persisted_loop.get("status") == "open"
                and len(persisted_loop.get("action_evidence", [])) == 4
                and {item.get("text") for item in persisted_loop.get("action_evidence", [])} >= {
                    "CI-only action with stable retry key",
                    "CI-only concurrent retry action",
                    "CI-only action A",
                    "CI-only action B",
                }
                and len(persisted_loop.get("response_evidence", [])) == 1,
                f"Open Loop lifecycle/evidence did not persist across Uvicorn restart: {persisted_loop}")
        status, open_loop_history_after_restart = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        require(status == 200 and any(
            event.get("event_type") == "response_recorded" for event in open_loop_history_after_restart
        ), f"Open Loop audit history did not persist across restart: {status}: {open_loop_history_after_restart}")
        # Race two mutually exclusive terminal transitions. Exactly one may
        # win; the second must observe terminal state and conflict, not overwrite it.
        lifecycle_payloads = [
            ("POST", f"/api/v1/open-loops/{open_loop_id}/close",
             {"text": "CI-only closure", "evidence": [{"kind": "ci_fixture", "text": "closure race"}]}),
            ("POST", f"/api/v1/open-loops/{open_loop_id}/cancel",
             {"text": "CI-only cancellation", "evidence": [{"kind": "ci_fixture", "text": "cancellation race"}]}),
        ]
        def apply_lifecycle(item):
            method, path, body = item
            return request(method, path, token=token_a, body=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            lifecycle_results = list(pool.map(apply_lifecycle, lifecycle_payloads))
        require(sorted(status for status, _ in lifecycle_results) == [200, 409],
                f"Conflicting close/cancel updates must serialize to one winner: {lifecycle_results}")
        status, terminal_loop = request("GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a)
        require(status == 200 and terminal_loop.get("loop", {}).get("status") in {"closed", "cancelled"},
                f"Open Loop did not persist a valid terminal state after lifecycle race: {status}: {terminal_loop}")
        status, reopened_loop = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/reopen", token=token_a,
            body={"text": "CI-only reopening after conflict test", "evidence": [{"kind": "ci_fixture", "text": "reopen"}]},
        )
        require(status == 200 and reopened_loop.get("status") == "open",
                f"Open Loop could not be reopened after terminal race: {status}: {reopened_loop}")
        status, lifecycle_history = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        terminal_events = [event.get("event_type") for event in lifecycle_history
                           if event.get("event_type") in {"closure_verified", "cancelled"}]
        require(status == 200 and len(terminal_events) == 1
                and any(event.get("event_type") == "reopened" for event in lifecycle_history),
                f"Conflicting lifecycle race produced invalid audit history: {status}: {lifecycle_history}")
        status, cross_person_loop_after_restart = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_b,
        )
        require(status == 404,
                f"Open Loop isolation failed after restart, got {status}: {cross_person_loop_after_restart}")

        download_status, downloaded_blob = download_binary(original_upload_id, token_a)
        require(
            download_status == 200 and downloaded_blob == duplicate_blob,
            f"Original binary evidence must remain byte-identical and accessible after application restart: status={download_status}, body={downloaded_blob!r}",
        )
        duplicate_status, downloaded_duplicate = download_binary(
            duplicate_upload_body["evidence_id"], token_a
        )
        require(
            duplicate_status == 200 and downloaded_duplicate == duplicate_blob,
            f"Duplicate binary evidence must remain separately persisted and accessible after restart: status={duplicate_status}, body={downloaded_duplicate!r}",
        )

        status, hydration_after_restart = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"hydration after application restart expected 200, got {status}: {hydration_after_restart}")
        restarted_event_ids = {
            event.get("id") for event in hydration_after_restart.get("events", [])
        }
        require(
            concurrent_capture_event_ids.issubset(restarted_event_ids),
            "Both concurrent care events must survive application restart",
        )
        restarted_evidence_ids = {
            evidence_id
            for situation in hydration_after_restart.get("ui_situations", [])
            for evidence_id in situation.get("linked_evidence_ids", [])
        }
        require(
            concurrent_capture_evidence_ids.issubset(restarted_evidence_ids),
            "Both concurrent source evidence records must survive application restart",
        )
        restarted_observation_situation = next(
            (item for item in hydration_after_restart.get("ui_situations", [])
             if item.get("id") == observation_situation_id),
            None,
        )
        require(
            restarted_observation_situation is not None
            and observation_event_id in (restarted_observation_situation.get("linked_event_ids") or [])
            and observation_evidence_id in (restarted_observation_situation.get("linked_evidence_ids") or []),
            f"original care situation/event/evidence links did not survive application restart: {restarted_observation_situation}",
        )
        restarted_observation_event = next(
            (item for item in hydration_after_restart.get("events", [])
             if item.get("id") == observation_event_id),
            None,
        )
        require(
            restarted_observation_event is not None
            and restarted_observation_event.get("description") == observation
            and observation_evidence_id in (restarted_observation_event.get("evidence_ids") or []),
            f"original caregiver wording/event evidence link did not survive application restart: {restarted_observation_event}",
        )
        status, restarted_evidence = request(
            "GET", f"/api/v1/evidence/{person_a}", token=token_a,
        )
        require(status == 200, f"evidence read after restart expected 200, got {status}: {restarted_evidence}")
        restarted_observation_evidence = next(
            (item for item in restarted_evidence if item.get("id") == observation_evidence_id),
            None,
        )
        require(
            restarted_observation_evidence is not None
            and restarted_observation_evidence.get("source_text") == observation,
            f"original source evidence did not survive application restart unchanged: {restarted_observation_evidence}",
        )
        status, coverage_after_restart = request(
            "GET", f"/api/v1/change/coverage/{person_a}?{coverage_query}", token=token_a,
        )
        require(status == 200, f"coverage read after application restart expected 200, got {status}: {coverage_after_restart}")
        status, patterns_after_restart = request(
            "GET", f"/api/v1/change/patterns/{person_c}?subject_type=fall", token=token_c,
        )
        require(status == 200, f"pattern read after application restart expected 200, got {status}: {patterns_after_restart}")
        require(
            any(
                pattern.get("id") == persisted_recurrence_id
                and pattern.get("is_active") is True
                and set(pattern.get("event_ids") or []).issuperset(pattern_event_ids)
                for pattern in patterns_after_restart
            ),
            "source-linked recurrence pattern did not persist across Uvicorn restart",
        )
        require(
            any(row.get("id") == saved_coverage.get("id") for row in coverage_after_restart.get("records", [])),
            "observation coverage did not persist across application restart",
        )
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_b)
        require(status == 403, f"record isolation failed after restart; expected 403, got {status}")

        print("PASS: PostgreSQL Daily Care capture, Evidence/LCR/history parity, original-session revocation,")
        print("      independent account login after restart, unchanged IDs, cross-record denial, and persistence.")

        # Full anonymous -> account -> revoke -> independent login continuity.
        # Every fixture below is synthetic and isolated to this disposable CI database.
        continuity_session_status, continuity_session = request(
            "POST", "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL account continuity person", "caregiver_name": "PostgreSQL account continuity caregiver"},
        )
        require(continuity_session_status == 201,
                f"continuity anonymous session creation failed: {continuity_session_status}: {continuity_session}")
        continuity_token = continuity_session["access_token"]
        continuity_person_id = continuity_session["person"]["id"]
        continuity_caregiver_id = continuity_session["caregiver"]["id"]
        continuity_profile_id = continuity_session["caregiver"]["profile_id"]

        continuity_evidence_status, continuity_evidence = request(
            "POST", "/api/v1/evidence", token=continuity_token,
            body={
                "person_id": continuity_person_id,
                "uploaded_by_caregiver_id": continuity_caregiver_id,
                "type": "note",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "unverified",
                "source_text": "CI-only continuity fixture: caregiver reports a change in evening routine.",
                "source_type": "account_continuity_e2e",
                "ingestion_key": "postgres-account-continuity-source-001",
            },
        )
        require(continuity_evidence_status in {200, 201} and continuity_evidence.get("id"),
                f"continuity source evidence creation failed: {continuity_evidence_status}: {continuity_evidence}")
        continuity_evidence_id = continuity_evidence["id"]

        continuity_upload_status, continuity_upload = upload_request(
            {
                "person_id": continuity_person_id,
                "caregiver_id": continuity_caregiver_id,
                "evidence_type": "document",
                "extracted_text": "CI-only synthetic document; no real personal data.",
            },
            "continuity-document.png",
            b"\x89PNG\r\n\x1a\nSolenOS account continuity document fixture",
            continuity_token,
        )
        require(continuity_upload_status == 201 and continuity_upload.get("evidence_id"),
                f"continuity document upload failed: {continuity_upload_status}: {continuity_upload}")
        continuity_document_id = continuity_upload["evidence_id"]

        continuity_record_status, continuity_record = request(
            "POST", "/api/v1/care-continuity/living-records", token=continuity_token,
            body={"record_status": "active", "schema_version": "lcr.v1"},
        )
        require(continuity_record_status == 201 and continuity_record.get("id"),
                f"continuity Living Care Record creation failed: {continuity_record_status}: {continuity_record}")
        continuity_record_id = continuity_record["id"]

        # Source-attributed caregiver assertion and explicit correction are
        # separate durable records. The correction must not overwrite the
        # original report or erase its reporter/evidence lineage.
        continuity_assertion_source = (
            "Caregiver's original wording: the evening routine was interrupted after dinner."
        )
        continuity_assertion_status, continuity_assertion = request(
            "POST", "/api/v1/care-continuity/assertions", token=continuity_token,
            body={
                "subject_type": "routine",
                "subject_id": "postgres-account-continuity-evening-routine",
                "field": "evening_routine",
                "assertion_kind": "current_state",
                "asserted_value": continuity_assertion_source,
                "source_label": "Caregiver report",
                "source_text": continuity_assertion_source,
                "evidence_ids": [continuity_evidence_id],
                "review_after_days": 30,
            },
        )
        require(
            continuity_assertion_status == 201
            and continuity_assertion.get("id")
            and continuity_assertion.get("assertion_status") == "reported"
            and continuity_assertion.get("created_by_caregiver_id") == continuity_caregiver_id
            and continuity_assertion.get("source_text") == continuity_assertion_source
            and continuity_evidence_id in (continuity_assertion.get("evidence_ids") or []),
            f"Source-attributed assertion did not preserve original wording, reporter, and evidence link: {continuity_assertion_status}: {continuity_assertion}",
        )
        continuity_assertion_id = continuity_assertion["id"]

        continuity_correction_status, continuity_correction = request(
            "POST", "/api/v1/care-continuity/corrections", token=continuity_token,
            body={
                "care_record_id": continuity_record_id,
                "target_type": "care_assertion",
                "target_id": continuity_assertion_id,
                "original_value": continuity_assertion_source,
                "corrected_value": "Caregiver clarification: the interruption began before dinner.",
                "correction_reason": "The caregiver clarified the timing; retain the original report and record this as a separate correction.",
            },
        )
        require(
            continuity_correction_status == 201
            and continuity_correction.get("id")
            and continuity_correction.get("requested_by_caregiver_id") == continuity_caregiver_id
            and continuity_correction.get("target_id") == continuity_assertion_id
            and continuity_correction.get("original_value") == continuity_assertion_source,
            f"Correction did not preserve target, original wording, and requesting caregiver: {continuity_correction_status}: {continuity_correction}",
        )
        continuity_correction_id = continuity_correction["id"]

        # The original assertion remains unchanged after a correction. The
        # correction is a separate append-only record, not a destructive edit.
        assertion_read_status, assertion_rows = request(
            "GET", "/api/v1/care-continuity/assertions",
            token=continuity_token,
        )
        correction_read_status, correction_rows = request(
            "GET", "/api/v1/care-continuity/corrections", token=continuity_token,
        )
        persisted_assertion = next(
            (row for row in assertion_rows if row.get("id") == continuity_assertion_id), None
        ) if isinstance(assertion_rows, list) else None
        persisted_correction = next(
            (row for row in correction_rows if row.get("id") == continuity_correction_id), None
        ) if isinstance(correction_rows, list) else None
        require(
            assertion_read_status == 200
            and persisted_assertion
            and persisted_assertion.get("asserted_value") == continuity_assertion_source
            and persisted_assertion.get("created_by_caregiver_id") == continuity_caregiver_id
            and correction_read_status == 200
            and persisted_correction
            and persisted_correction.get("requested_by_caregiver_id") == continuity_caregiver_id,
            f"Assertion/correction history did not persist independently: assertion={assertion_rows}, correction={correction_rows}",
        )

        continuity_decision_status, continuity_decision = request(
            "POST", "/api/v1/care-continuity/decisions", token=continuity_token,
            body={
                "title": "CI-only routine review decision",
                "decision_text": "Continue recording the evening routine before deciding whether a change is needed.",
                "decision_status": "reported",
                "context": "Synthetic continuity test; not a clinical recommendation.",
                "evidence_ids": [continuity_evidence_id],
            },
        )
        require(continuity_decision_status == 201 and continuity_decision.get("id"),
                f"continuity decision creation failed: {continuity_decision_status}: {continuity_decision}")
        continuity_decision_id = continuity_decision["id"]
        continuity_decision_outcome_status, continuity_decision_outcome = request(
            "POST", "/api/v1/care-continuity/decision-outcomes", token=continuity_token,
            body={
                "decision_id": continuity_decision_id,
                "outcome_status": "reported",
                "description": "CI-only outcome: the routine was recorded again for review.",
                "evidence_ids": [continuity_evidence_id],
                "is_intended_outcome": True,
            },
        )
        require(continuity_decision_outcome_status == 201 and continuity_decision_outcome.get("id"),
                f"continuity decision outcome creation failed: {continuity_decision_outcome_status}: {continuity_decision_outcome}")
        continuity_decision_outcome_id = continuity_decision_outcome["id"]

        continuity_version_status, continuity_version = request(
            "POST", "/api/v1/care-continuity/record-versions", token=continuity_token,
            body={"care_record_id": continuity_record_id, "reason": "PostgreSQL account continuity fixture"},
        )
        require(continuity_version_status == 201 and continuity_version.get("id"),
                f"continuity LCR version creation failed: {continuity_version_status}: {continuity_version}")

        continuity_question_status, continuity_question = request(
            "POST", "/api/v1/uncertainty/questions", token=continuity_token,
            body={
                "person_id": continuity_person_id,
                "question": "Does the evening routine change occur every day?",
                "subject_type": "routine",
                "subject_id": "postgres-account-continuity-routine",
                "priority": "high",
                "necessity": "necessary",
                "necessity_rationale": "The caregiver needs this answer to decide whether the reported evening routine change requires a follow-up.",
                "evidence_ids": [continuity_evidence_id],
                "context": [{
                    "source_kind": "caregiver_report",
                    "source_id": continuity_evidence_id,
                    "necessity_rationale": "The caregiver needs this answer to decide whether the reported evening routine change requires a follow-up.",
                    "necessity_assessment": {
                        "decision_dependency": "Determine whether to request a routine review from the care team.",
                        "necessity_rationale": "The caregiver needs this answer to decide whether the reported evening routine change requires a follow-up.",
                        "evidence_ids": [continuity_evidence_id],
                    },
                }],
            },
        )
        require(continuity_question_status == 200 and continuity_question.get("id"),
                f"continuity clarification creation failed: {continuity_question_status}: {continuity_question}")
        continuity_question_id = continuity_question["id"]

        answer_question_status, answer_question = request(
            "POST", "/api/v1/uncertainty/questions", token=continuity_token,
            body={
                "person_id": continuity_person_id,
                "question": "Who observed the change in the evening routine?",
                "subject_type": "routine",
                "subject_id": "postgres-account-continuity-routine",
                "priority": "medium",
            },
        )
        require(answer_question_status == 200 and answer_question.get("id"),
                f"continuity answer-history question creation failed: {answer_question_status}: {answer_question}")
        answer_question_id = answer_question["id"]
        exact_answer = "I observed it on three evenings this week."
        exact_provenance = "I was present on each of those evenings."
        answer_status, answer_body = request(
            "PATCH", f"/api/v1/uncertainty/questions/{answer_question_id}",
            token=continuity_token,
            body={
                "status": "answered",
                "answer": exact_answer,
                "answer_provenance": exact_provenance,
            },
        )
        require(answer_status == 200 and answer_body.get("status") == "answered",
                f"continuity answer was not saved: {answer_status}: {answer_body}")
        answer_history_status, answer_history = request(
            "GET", f"/api/v1/uncertainty/questions/{answer_question_id}/answer-history",
            token=continuity_token,
        )
        require(
            answer_history_status == 200
            and len(answer_history) == 1
            and answer_history[0].get("answer") == exact_answer
            and answer_history[0].get("answer_provenance") == exact_provenance
            and len(answer_history[0].get("evidence_ids") or []) == 1,
            f"continuity answer history or source evidence was not persisted: {answer_history_status}: {answer_history}",
        )
        answer_evidence_id = answer_history[0]["evidence_ids"][0]
        with SessionLocal() as db:
            from app.models.care import Evidence
            saved_answer_evidence = db.query(Evidence).filter_by(
                id=answer_evidence_id,
                person_id=continuity_person_id,
            ).one()
            require(
                saved_answer_evidence.evidence_kind == "caregiver_report"
                and saved_answer_evidence.epistemic_status == "caregiver_reported"
                and saved_answer_evidence.uploaded_by_caregiver_id == continuity_caregiver_id
                and exact_answer in saved_answer_evidence.source_text
                and exact_provenance in saved_answer_evidence.source_text,
                "answer source evidence did not preserve exact caregiver wording and attribution",
            )

        continuity_loop_status, continuity_loop = request(
            "POST", "/api/v1/open-loops", token=continuity_token,
            body={
                "loop_type": "follow_up",
                "title": "CI-only continuity decision and outcome",
                "description": "Synthetic follow-up retained across anonymous account conversion.",
                "idempotency_key": "postgres-account-continuity-loop-001",
                "evidence": [{"kind": "care_record_source", "evidence_id": continuity_evidence_id}],
                "verification_rule": "The synthetic source evidence remains linked after account conversion.",
            },
        )
        require(continuity_loop_status == 201 and continuity_loop.get("id"),
                f"continuity decision/follow-up creation failed: {continuity_loop_status}: {continuity_loop}")
        continuity_loop_id = continuity_loop["id"]
        continuity_action_status, continuity_action = request(
            "POST", f"/api/v1/open-loops/{continuity_loop_id}/actions", token=continuity_token,
            body={
                "text": "CI-only action retained through account conversion",
                "evidence": [{"kind": "care_record_source", "evidence_id": continuity_evidence_id}],
                "idempotency_key": "postgres-account-continuity-action-001",
            },
        )
        require(continuity_action_status == 200,
                f"continuity action persistence failed: {continuity_action_status}: {continuity_action}")
        continuity_outcome_status, continuity_outcome = request(
            "POST", f"/api/v1/open-loops/{continuity_loop_id}/outcomes", token=continuity_token,
            body={
                "text": "CI-only outcome reported after the synthetic action",
                "evidence": [{"kind": "care_record_source", "evidence_id": continuity_evidence_id}],
                "idempotency_key": "postgres-account-continuity-outcome-001",
            },
        )
        require(continuity_outcome_status == 200,
                f"continuity outcome persistence failed: {continuity_outcome_status}: {continuity_outcome}")

        # A rejected registration must not leave a partially linked account.
        interrupted_status, interrupted_body = request(
            "POST", "/api/v1/accounts", token=continuity_token,
            body={"email": "not-an-email", "password": "CI-only-strong-passphrase-2026!"},
        )
        require(interrupted_status == 422,
                f"invalid registration must fail validation: {interrupted_status}: {interrupted_body}")
        with SessionLocal() as db:
            from app.models.caregiver_account import CaregiverAccount
            require(
                db.query(CaregiverAccount).filter_by(person_id=continuity_person_id).count() == 0,
                "failed registration left a partial caregiver account",
            )

        continuity_email = f"postgres-continuity-{uuid.uuid4().hex[:12]}@example.test"
        continuity_password = "CI-only-strong-passphrase-2026!"
        registration_status, registration = request(
            "POST", "/api/v1/accounts", token=continuity_token,
            body={"email": continuity_email, "password": continuity_password},
        )
        require(registration_status == 201,
                f"anonymous-to-account registration failed: {registration_status}: {registration}")
        require(registration.get("caregiver_profile_id") == continuity_profile_id,
                "registration replaced the existing caregiver profile identity")

        # Duplicate email from a different anonymous identity must be rejected.
        duplicate_email_status, duplicate_email_body = request(
            "POST", "/api/v1/accounts", token=token_b,
            body={"email": continuity_email, "password": continuity_password},
        )
        require(duplicate_email_status == 409,
                f"duplicate email registration must return 409: {duplicate_email_status}: {duplicate_email_body}")

        # Wrong password must not issue a session or disclose whether a record exists.
        wrong_password_status, wrong_password_body = request(
            "POST", "/api/v1/accounts/login",
            body={"email": continuity_email, "password": "incorrect-ci-password"},
        )
        require(wrong_password_status == 401,
                f"incorrect password must return 401: {wrong_password_status}: {wrong_password_body}")

        # End the original anonymous session before testing a clean login context.
        revoke_original_status, revoke_original = request(
            "POST", "/api/v1/sessions/revoke", token=continuity_token, body={},
        )
        require(revoke_original_status == 200 and revoke_original.get("revoked") is True,
                f"original anonymous session revocation failed: {revoke_original_status}: {revoke_original}")
        old_session_status, old_session_body = request(
            "GET", f"/api/v1/evidence/{continuity_person_id}", token=continuity_token,
        )
        require(old_session_status == 401,
                f"revoked original session still accessed care data: {old_session_status}: {old_session_body}")

        # No cookie, no browser storage and no prior bearer token are carried forward.
        # This independent HTTP client context receives only the account credentials.
        independent_login_status, independent_login = request(
            "POST", "/api/v1/accounts/login",
            body={"email": continuity_email, "password": continuity_password},
        )
        require(independent_login_status == 200,
                f"independent account login failed: {independent_login_status}: {independent_login}")
        require(independent_login.get("person_id") == continuity_person_id,
                "independent login changed the person ID")
        require(independent_login.get("caregiver_id") == continuity_caregiver_id,
                "independent login changed the caregiver ID")
        require(independent_login.get("caregiver_profile_id") == continuity_profile_id,
                "independent login changed the caregiver profile ID")
        continuity_login_token = independent_login["access_token"]
        require(continuity_login_token != continuity_token,
                "independent login reused the revoked anonymous bearer token")

        restored_evidence_status, restored_evidence = request(
            "GET", f"/api/v1/evidence/{continuity_person_id}", token=continuity_login_token,
        )
        require(restored_evidence_status == 200,
                f"evidence restoration failed: {restored_evidence_status}: {restored_evidence}")
        restored_evidence_by_id = {item.get("id"): item for item in restored_evidence}
        require(continuity_evidence_id in restored_evidence_by_id,
                "original caregiver evidence ID disappeared after account conversion")
        require(
            restored_evidence_by_id[continuity_evidence_id].get("source_text")
            == continuity_evidence["source_text"],
            "original caregiver evidence wording changed after account conversion",
        )
        require(continuity_document_id in restored_evidence_by_id,
                "uploaded document evidence ID disappeared after account conversion")

        restored_document_status, restored_document_bytes = download_binary(
            continuity_document_id, continuity_login_token,
        )
        require(restored_document_status == 200
                and restored_document_bytes == b"\x89PNG\r\n\x1a\nSolenOS account continuity document fixture",
                "uploaded document bytes did not survive account conversion")

        restored_question_status, restored_questions = request(
            "GET", f"/api/v1/uncertainty/questions/{continuity_person_id}?status=open",
            token=continuity_login_token,
        )
        restored_question_ids = {
            item.get("id") for item in restored_questions
            if isinstance(item, dict)
        } if isinstance(restored_questions, list) else set()
        if restored_question_status != 200 or continuity_question_id not in restored_question_ids:
            from app.models.uncertainty import OpenQuestion
            from app.services.care_continuity import has_valid_caregiver_necessity
            with SessionLocal() as diagnostic_db:
                diagnostic_question = diagnostic_db.query(OpenQuestion).filter_by(
                    id=continuity_question_id,
                    person_id=continuity_person_id,
                ).first()
                diagnostic = None if diagnostic_question is None else {
                    "status": diagnostic_question.status,
                    "necessity": diagnostic_question.necessity,
                    "evidence_ids": list(diagnostic_question.evidence_ids or []),
                    "context": list(diagnostic_question.context or []),
                    "necessity_valid": has_valid_caregiver_necessity(
                        diagnostic_question, diagnostic_db, continuity_person_id
                    ),
                }
            raise AssertionError(
                "open clarification question did not restore after account login: "
                f"http={restored_question_status}, response={restored_questions}, "
                f"persisted_question={diagnostic}"
            )
        restored_answer_history_status, restored_answer_history = request(
            "GET", f"/api/v1/uncertainty/questions/{answer_question_id}/answer-history",
            token=continuity_login_token,
        )
        require(
            restored_answer_history_status == 200
            and len(restored_answer_history) == 1
            and restored_answer_history[0].get("answer") == exact_answer
            and restored_answer_history[0].get("answer_provenance") == exact_provenance
            and restored_answer_history[0].get("evidence_ids") == [answer_evidence_id],
            "clarification answer and source history did not restore after account login",
        )

        restored_loop_status, restored_loop = request(
            "GET", f"/api/v1/open-loops/{continuity_loop_id}", token=continuity_login_token,
        )
        require(restored_loop_status == 200
                and restored_loop.get("loop", {}).get("id") == continuity_loop_id
                and any(item.get("text") == "CI-only action retained through account conversion"
                        for item in restored_loop.get("loop", {}).get("action_evidence", []))
                and any(item.get("text") == "CI-only outcome reported after the synthetic action"
                        for item in restored_loop.get("loop", {}).get("outcome_evidence", [])),
                f"decision/action/outcome history did not restore: {restored_loop_status}: {restored_loop}")

        restored_decisions_status, restored_decisions = request(
            "GET", "/api/v1/care-continuity/decisions", token=continuity_login_token,
        )
        restored_decision_outcomes_status, restored_decision_outcomes = request(
            "GET", "/api/v1/care-continuity/decision-outcomes", token=continuity_login_token,
        )
        require(restored_decisions_status == 200 and any(
            item.get("id") == continuity_decision_id for item in restored_decisions
        ), "care decision ID did not restore after account login")
        require(restored_decision_outcomes_status == 200 and any(
            item.get("id") == continuity_decision_outcome_id
            and item.get("decision_id") == continuity_decision_id
            for item in restored_decision_outcomes
        ), "decision outcome ID/link did not restore after account login")

        restored_versions_status, restored_versions = request(
            "GET", "/api/v1/care-continuity/record-versions", token=continuity_login_token,
        )
        restored_snapshots_status, restored_snapshots = request(
            "GET", "/api/v1/care-continuity/record-snapshots", token=continuity_login_token,
        )
        require(restored_versions_status == 200 and any(
            item.get("id") == continuity_version.get("id") for item in restored_versions
        ), "LCR version history did not restore after account login")
        require(restored_snapshots_status == 200 and any(
            item.get("version_id") == continuity_version.get("id") for item in restored_snapshots
        ), "LCR snapshot history did not restore after account login")

        continuity_snapshot = next(
            (item for item in restored_snapshots
             if item.get("version_id") == continuity_version.get("id")),
            None,
        )
        continuity_snapshot_tables = (
            (continuity_snapshot or {}).get("snapshot_payload") or {}
        ).get("tables") or {}
        snapshot_assertions = continuity_snapshot_tables.get("care_assertions") or []
        snapshot_corrections = continuity_snapshot_tables.get("care_corrections") or []
        snapshot_evidence = continuity_snapshot_tables.get("evidence") or []
        snapshot_assertion = next(
            (item for item in snapshot_assertions if item.get("id") == continuity_assertion_id),
            None,
        )
        snapshot_correction = next(
            (item for item in snapshot_corrections if item.get("id") == continuity_correction_id),
            None,
        )
        snapshot_source = next(
            (item for item in snapshot_evidence if item.get("id") == continuity_evidence_id),
            None,
        )
        require(
            continuity_snapshot is not None
            and snapshot_assertion
            and snapshot_assertion.get("asserted_value") == continuity_assertion_source
            and snapshot_assertion.get("created_by_caregiver_id") == continuity_caregiver_id
            and snapshot_assertion.get("evidence_ids") == [continuity_evidence_id]
            and snapshot_correction
            and snapshot_correction.get("target_id") == continuity_assertion_id
            and snapshot_correction.get("requested_by_caregiver_id") == continuity_caregiver_id
            and snapshot_correction.get("original_value") == continuity_assertion_source
            and snapshot_source
            and snapshot_source.get("uploaded_by_caregiver_id") == continuity_caregiver_id,
            "LCR snapshot did not preserve original source evidence, reporter attribution, assertion, and separate correction",
        )

        # LCR assertion and correction endpoints must enforce the same
        # person boundary as evidence retrieval; a valid foreign session cannot
        # enumerate assertions or attach a correction to another person's claim.
        own_assertion_status, own_assertions = request(
            "GET",
            f"/api/v1/care-continuity/assertions/{continuity_person_id}",
            # Account conversion intentionally revokes the original anonymous
            # capability. Use the newly issued account session for post-conversion
            # reads; the old token is separately verified as revoked after restart.
            token=continuity_login_token,
        )
        require(
            own_assertion_status == 200
            and isinstance(own_assertions, list)
            and any(row.get("id") == continuity_assertion_id for row in own_assertions),
            f"authenticated caregiver could not retrieve their own persisted assertion: {own_assertion_status}: {own_assertions}",
        )
        foreign_assertion_status, foreign_assertions = request(
            "GET",
            f"/api/v1/care-continuity/assertions/{continuity_person_id}",
            token=token_b,
        )
        require(
            foreign_assertion_status == 403,
            f"cross-person assertion listing must be denied without disclosing record contents: {foreign_assertion_status}: {foreign_assertions}",
        )
        foreign_correction_status, foreign_correction = request(
            "POST",
            "/api/v1/care-continuity/corrections",
            token=token_b,
            body={
                "care_record_id": continuity_record_id,
                "target_type": "care_assertion",
                "target_id": continuity_assertion_id,
                "original_value": continuity_assertion_source,
                "corrected_value": "Foreign correction must never be persisted.",
                "correction_reason": "Cross-person isolation test.",
            },
        )
        require(
            foreign_correction_status == 404,
            f"cross-person correction write must return 404: {foreign_correction_status}: {foreign_correction}",
        )

        # Cross-record attempts with another person's valid session remain denied.
        cross_person_status, cross_person_body = request(
            "GET", f"/api/v1/evidence/{continuity_person_id}", token=token_b,
        )
        require(cross_person_status == 403,
                f"another person's session accessed this care record: {cross_person_status}: {cross_person_body}")
        cross_loop_status, cross_loop_body = request(
            "GET", f"/api/v1/open-loops/{continuity_loop_id}", token=token_b,
        )
        require(cross_loop_status == 404,
                f"another person's session accessed this follow-up history: {cross_loop_status}: {cross_loop_body}")

        # Expired sessions must fail closed even if the token hash still exists.
        expired_status, expired_session = request(
            "POST", "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL expired-session fixture", "caregiver_name": "CI-only caregiver"},
        )
        require(expired_status == 201, f"expired-session fixture creation failed: {expired_status}: {expired_session}")
        with SessionLocal() as db:
            from app.models.anonymous_session import AnonymousSession
            expired_row = db.query(AnonymousSession).filter_by(id=expired_session["session_id"]).one()
            expired_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        expired_read_status, expired_read_body = request(
            "GET", f"/api/v1/evidence/{expired_session['person']['id']}",
            token=expired_session["access_token"],
        )
        require(expired_read_status == 401,
                f"expired session was not rejected: {expired_read_status}: {expired_read_body}")

        # Two simultaneous account conversions for one anonymous identity must
        # converge to one account; database uniqueness is the final arbiter.
        concurrent_session_status, concurrent_session = request(
            "POST", "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL concurrent registration fixture", "caregiver_name": "CI-only caregiver"},
        )
        require(concurrent_session_status == 201,
                f"concurrent registration fixture failed: {concurrent_session_status}: {concurrent_session}")
        concurrent_email = f"postgres-concurrent-{uuid.uuid4().hex[:12]}@example.test"
        concurrent_payload = {"email": concurrent_email, "password": continuity_password}
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_registrations = list(pool.map(
                lambda _: request("POST", "/api/v1/accounts",
                                  token=concurrent_session["access_token"], body=concurrent_payload),
                range(2),
            ))
        require(sorted(status for status, _ in concurrent_registrations) == [201, 409],
                f"concurrent account conversion must have exactly one winner and one conflict: {concurrent_registrations}")
        with SessionLocal() as db:
            require(
                db.query(CaregiverAccount).filter_by(person_id=concurrent_session["person"]["id"]).count() == 1,
                "concurrent account conversion created duplicate accounts for one care record",
            )

        # A transport failure must not be mistaken for a successful registration.
        # This probe deliberately targets a closed local port and cannot touch the database.
        import urllib.error
        failed_network_request = urllib.request.Request(
            "http://127.0.0.1:1/api/v1/accounts",
            data=json.dumps({"email": f"offline-{uuid.uuid4().hex[:8]}@example.test",
                             "password": continuity_password}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(failed_network_request, timeout=1)
            raise AssertionError("closed-port network failure probe unexpectedly connected")
        except urllib.error.URLError:
            pass

        # Verify the exact Daily Care capture, source evidence, and LCR projection
        # survive the same real Uvicorn restart using the original anonymous session.
        stop_server(server)
        server = start_server()
        restarted_daily_history_status, restarted_daily_history = request(
            "GET", f"/api/v1/events/{person_a}", token=token_a,
        )
        restarted_daily_matches = [
            row for row in (restarted_daily_history or [])
            if row.get("id") == daily_event_id
        ]
        require(
            restarted_daily_history_status == 200
            and len(restarted_daily_matches) == 1
            and restarted_daily_matches[0].get("evidence_ids") == [daily_evidence_id],
            f"Daily Care event/evidence did not survive Uvicorn restart: {restarted_daily_history_status}: {restarted_daily_matches}",
        )
        restarted_daily_lcr_status, restarted_daily_lcr = request(
            "GET", "/api/v1/care-continuity/current-care?limit_per_source=50",
            token=token_a,
        )
        restarted_daily_lcr_matches = [
            row for row in (restarted_daily_lcr or {}).get("items", [])
            if row.get("id") == daily_event_id
        ]
        require(
            restarted_daily_lcr_status == 200
            and len(restarted_daily_lcr_matches) == 1
            and restarted_daily_lcr_matches[0].get("evidence_ids") == [daily_evidence_id],
            f"Daily Care source was not retrievable from LCR after Uvicorn restart: {restarted_daily_lcr_status}: {restarted_daily_lcr_matches}",
        )
        replay_status, replayed_daily_capture = request(
            "POST", "/api/v1/daily/log", token=token_a, body=daily_capture_payload,
        )
        require(
            replay_status == 200
            and replayed_daily_capture.get("id") == daily_event_id
            and replayed_daily_capture.get("evidence_ids") == [daily_evidence_id]
            and replayed_daily_capture.get("idempotent_replay") is True,
            f"Daily Care retry after restart created or returned a different record: {replay_status}: {replayed_daily_capture}",
        )
        foreign_daily_history_status, foreign_daily_history = request(
            "GET", f"/api/v1/events/{person_a}", token=token_b,
        )
        require(
            foreign_daily_history_status == 403,
            f"Foreign caregiver could read Daily Care history after restart: {foreign_daily_history_status}: {foreign_daily_history}",
        )

        # Verify the same restored account and all its data after an actual server restart.
        restarted_login_status, restarted_login = request(
            "POST", "/api/v1/accounts/login",
            body={"email": continuity_email, "password": continuity_password},
        )
        require(restarted_login_status == 200
                and restarted_login.get("person_id") == continuity_person_id
                and restarted_login.get("caregiver_id") == continuity_caregiver_id,
                f"account identity did not survive PostgreSQL-backed server restart: {restarted_login_status}: {restarted_login}")
        restarted_token = restarted_login["access_token"]
        restarted_evidence_status, restarted_evidence = request(
            "GET", f"/api/v1/evidence/{continuity_person_id}", token=restarted_token,
        )
        require(restarted_evidence_status == 200
                and {continuity_evidence_id, continuity_document_id}.issubset(
                    {item.get("id") for item in restarted_evidence}
                ), "original evidence/document IDs did not survive server restart")
        restarted_document_status, restarted_document_bytes = download_binary(
            continuity_document_id, restarted_token,
        )
        require(
            restarted_document_status == 200
            and restarted_document_bytes == b"\x89PNG\r\n\x1a\nSolenOS account continuity document fixture",
            "uploaded document bytes did not survive PostgreSQL-backed application restart",
        )
        revoked_original_after_restart, revoked_original_body = request(
            "GET", f"/api/v1/evidence/{continuity_person_id}", token=continuity_token,
        )
        require(
            revoked_original_after_restart == 401,
            f"original revoked anonymous capability became valid after restart: {revoked_original_after_restart}: {revoked_original_body}",
        )

        restarted_question_status, restarted_questions = request(
            "GET", f"/api/v1/uncertainty/questions/{continuity_person_id}?status=open",
            token=restarted_token,
        )
        require(restarted_question_status == 200 and any(
            item.get("id") == continuity_question_id for item in restarted_questions
        ), "clarification history did not survive server restart")
        restarted_loop_status, restarted_loop = request(
            "GET", f"/api/v1/open-loops/{continuity_loop_id}", token=restarted_token,
        )
        require(
            restarted_loop_status == 200
            and restarted_loop.get("loop", {}).get("id") == continuity_loop_id
            and any(item.get("text") == "CI-only action retained through account conversion"
                    for item in restarted_loop.get("loop", {}).get("action_evidence", []))
            and any(item.get("text") == "CI-only outcome reported after the synthetic action"
                    for item in restarted_loop.get("loop", {}).get("outcome_evidence", [])),
            "decision/action/outcome history did not survive server restart",
        )
        restarted_decisions_status, restarted_decisions = request(
            "GET", "/api/v1/care-continuity/decisions", token=restarted_token,
        )
        restarted_decision_outcomes_status, restarted_decision_outcomes = request(
            "GET", "/api/v1/care-continuity/decision-outcomes", token=restarted_token,
        )
        require(restarted_decisions_status == 200 and any(
            item.get("id") == continuity_decision_id for item in restarted_decisions
        ), "care decision did not survive PostgreSQL server restart")
        require(restarted_decision_outcomes_status == 200 and any(
            item.get("id") == continuity_decision_outcome_id
            for item in restarted_decision_outcomes
        ), "decision outcome did not survive PostgreSQL server restart")

        restarted_versions_status, restarted_versions = request(
            "GET", "/api/v1/care-continuity/record-versions", token=restarted_token,
        )
        require(restarted_versions_status == 200 and any(
            item.get("id") == continuity_version.get("id") for item in restarted_versions
        ), "LCR version history did not survive server restart")

        restarted_snapshots_status, restarted_snapshots = request(
            "GET", "/api/v1/care-continuity/record-snapshots", token=restarted_token,
        )
        restarted_snapshot = next(
            (item for item in restarted_snapshots
             if item.get("version_id") == continuity_version.get("id")),
            None,
        ) if restarted_snapshots_status == 200 else None
        restarted_tables = (
            (restarted_snapshot or {}).get("snapshot_payload") or {}
        ).get("tables") or {}
        require(
            restarted_snapshot is not None
            and any(
                item.get("id") == continuity_assertion_id
                and item.get("asserted_value") == continuity_assertion_source
                and item.get("created_by_caregiver_id") == continuity_caregiver_id
                for item in (restarted_tables.get("care_assertions") or [])
            )
            and any(
                item.get("id") == continuity_correction_id
                and item.get("target_id") == continuity_assertion_id
                and item.get("requested_by_caregiver_id") == continuity_caregiver_id
                for item in (restarted_tables.get("care_corrections") or [])
            )
            and any(
                item.get("id") == continuity_evidence_id
                and item.get("uploaded_by_caregiver_id") == continuity_caregiver_id
                for item in (restarted_tables.get("evidence") or [])
            ),
            "LCR snapshot lost original evidence, assertion attribution, or correction lineage after server restart",
        )

        print("PASS: PostgreSQL readiness, anonymous session creation, durable capture/hydration,")
        print("      cross-record denial, token rotation, and persistence after Uvicorn restart.")
    finally:
        if server.poll() is None:
            stop_server(server)


if __name__ == "__main__":
    main()
