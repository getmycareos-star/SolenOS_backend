"""PostgreSQL-only regression coverage for Qwen worker lease ownership."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires a migrated PostgreSQL database",
)

from app.core.database import SessionLocal
from app.main import app
from app.models.care import CaptureAnalysis, Caregiver, Person
from app.models.qwen_background_job import QwenBackgroundJob, QwenJobEvent, QwenModelOutput
from app.services import qwen_background_worker as worker


def test_reclaimed_worker_cannot_overwrite_classified_workflow_on_postgresql(monkeypatch):
    """A stale classifier cannot overwrite the workflow selected by a newer lease owner."""
    monkeypatch.setenv("SOLENOS_QWEN_WORKER_ENABLED", "false")
    job_id = str(uuid.uuid4())
    newer_lock = datetime.now(timezone.utc) + timedelta(minutes=1)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={
                "name": "PostgreSQL Qwen lease regression",
                "caregiver_name": "PostgreSQL Qwen lease regression",
            },
        )
        assert created.status_code == 201, created.text
        person_id = created.json()["person"]["id"]
        caregiver_id = created.json()["caregiver"]["id"]

        with SessionLocal() as db:
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Please summarize the supplied appointment note.",
                context_text="Appointment date: not supplied.",
                evidence_ids_json="[]",
                source_event_ids_json="[]",
                source_evidence_ids_json="[]",
                status="queued",
                attempt_count=0,
                available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))
            db.commit()

        async def reclaim_during_classification(*args, **kwargs):
            with SessionLocal() as other_worker_db:
                current = other_worker_db.query(QwenBackgroundJob).filter_by(id=job_id).one()
                current.status = "processing"
                current.locked_at = newer_lock
                current.workflow = "companion"
                other_worker_db.commit()
            return "appointment"

        async def unexpected_assist(*args, **kwargs):
            raise AssertionError("a reclaimed worker must stop before assistant inference")

        monkeypatch.setattr(worker, "_classify_task", reclaim_during_classification)
        from app.api import collaboration
        monkeypatch.setattr(collaboration, "qwen_support_assist", unexpected_assist)

        assert asyncio.run(worker.process_one_qwen_job(job_id)) is True

        with SessionLocal() as db:
            job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            assert job.status == "processing"
            assert job.workflow == "companion"
            assert job.locked_at == newer_lock
            assert db.query(QwenModelOutput).filter_by(job_id=job_id).count() == 0
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id,
                event_type="job_completed",
            ).count() == 0


def test_postgres_lease_compare_and_swap_rejects_a_reclaimed_job(monkeypatch):
    """Renewal must match the exact persisted lock, not only job identity/status."""
    suffix = uuid.uuid4().hex
    person_id = f"lease-cas-person-{suffix}"
    caregiver_id = f"lease-cas-caregiver-{suffix}"
    job_id = str(uuid.uuid4())
    claimed_at = datetime.now(timezone.utc)
    monkeypatch.setattr(worker, "SessionLocal", SessionLocal)
    try:
        with SessionLocal() as db:
            db.add(Person(id=person_id, name="Lease CAS test recipient"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Lease CAS test caregiver",
                relationship="Family",
                person_id=person_id,
            ))
            db.flush()
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Review a saved care note.",
                context_text="",
                workflow="appointment",
                status="processing",
                attempt_count=1,
                locked_at=claimed_at,
                available_at=claimed_at,
            ))
            db.commit()

        lease_state = {"locked_at": claimed_at, "lost": False, "_lock": threading.Lock()}
        refreshed_at = claimed_at + timedelta(seconds=30)
        assert worker._refresh_qwen_job_lease_once(
            job_id,
            person_id,
            caregiver_id,
            expected_locked_at=claimed_at,
            lease_state=lease_state,
            now=refreshed_at,
        ) is True
        assert lease_state["locked_at"] == refreshed_at

        newer_lock = refreshed_at + timedelta(minutes=1)
        with SessionLocal() as db:
            job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            job.status = "processing"
            job.locked_at = newer_lock
            db.commit()

        assert worker._refresh_qwen_job_lease_once(
            job_id,
            person_id,
            caregiver_id,
            expected_locked_at=refreshed_at,
            lease_state=lease_state,
        ) is False
        assert lease_state["lost"] is True
        with SessionLocal() as db:
            job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            assert job.status == "processing"
            assert job.locked_at == newer_lock
    finally:
        with SessionLocal() as db:
            db.query(QwenJobEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
            db.query(QwenBackgroundJob).filter_by(id=job_id).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()


def test_postgres_document_worker_does_not_block_cancellation_during_ocr(monkeypatch):
    """The queue row remains updateable while document extraction is running."""
    suffix = uuid.uuid4().hex
    person_id = f"document-cancel-person-{suffix}"
    caregiver_id = f"document-cancel-caregiver-{suffix}"
    job_id = str(uuid.uuid4())
    monkeypatch.setattr(worker, "SessionLocal", SessionLocal)
    try:
        with SessionLocal() as db:
            db.add(Person(id=person_id, name="Document cancellation recipient"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Document cancellation caregiver",
                relationship="Family",
                person_id=person_id,
            ))
            db.flush()
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Process a saved document for caregiver review.",
                context_text="",
                workflow="document_intake",
                evidence_ids_json='["document-cancel-evidence"]',
                status="queued",
                attempt_count=0,
                available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))
            db.commit()

        def cancel_while_ocr_runs(evidence_id, scoped_person_id, scoped_caregiver_id):
            assert evidence_id == "document-cancel-evidence"
            assert scoped_person_id == person_id
            assert scoped_caregiver_id == caregiver_id
            with SessionLocal() as db:
                current = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
                current.status = "cancelled"
                current.locked_at = None
                current.error_code = "document_deleted_during_processing"
                db.commit()
            return (
                {"analysis_id": "analysis-not-used", "retryable_page_numbers": [], "retry_exhausted_page_numbers": []},
                {"intelligence_status": "complete", "observations": [], "processed_page_count": 1, "total_page_count": 1, "unprocessed_page_numbers": []},
            )

        monkeypatch.setattr(worker, "_process_document_evidence", cancel_while_ocr_runs)
        assert asyncio.run(worker.process_one_qwen_job(job_id)) is True
        with SessionLocal() as db:
            current = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            assert current.status == "cancelled"
            assert current.error_code == "document_deleted_during_processing"
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id, event_type="document_intake_completed"
            ).count() == 0
    finally:
        with SessionLocal() as db:
            db.query(QwenJobEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
            db.query(QwenBackgroundJob).filter_by(id=job_id).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()


def test_postgres_worker_discards_assistant_result_after_lease_reclaim(monkeypatch):
    """An old worker cannot save a Companion answer after another worker claims the job."""
    suffix = uuid.uuid4().hex
    person_id = f"qwen-stale-output-person-{suffix}"
    caregiver_id = f"qwen-stale-output-caregiver-{suffix}"
    job_id = str(uuid.uuid4())
    monkeypatch.setattr(worker, "SessionLocal", SessionLocal)
    try:
        with SessionLocal() as db:
            db.add(Person(id=person_id, name="Stale output recipient"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Stale output caregiver",
                relationship="Family",
                person_id=person_id,
            ))
            db.flush()
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Summarize the saved appointment note.",
                context_text="",
                evidence_ids_json="[]",
                status="queued",
                attempt_count=0,
                available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))
            db.commit()

        async def classify(*args, **kwargs):
            return "appointment"

        async def reclaim_during_inference(payload, request, inference_db):
            newer_lock = datetime.now(timezone.utc) + timedelta(minutes=1)
            with SessionLocal() as db:
                current = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
                current.status = "processing"
                current.locked_at = newer_lock
                db.commit()
            return {
                "workflow": "appointment",
                "reply": "This answer belongs to the stale worker.",
                "ai_generated": True,
                "provider_status": "available",
                "model": "qwen3.5:4b",
            }

        monkeypatch.setattr(worker, "_classify_task", classify)
        from app.api import collaboration
        monkeypatch.setattr(collaboration, "qwen_support_assist", reclaim_during_inference)
        assert asyncio.run(worker.process_one_qwen_job(job_id)) is True

        with SessionLocal() as db:
            current = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            assert current.status == "processing"
            assert current.locked_at is not None
            assert db.query(QwenModelOutput).filter_by(job_id=job_id).count() == 0
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id, event_type="job_completed"
            ).count() == 0
    finally:
        with SessionLocal() as db:
            db.query(QwenJobEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
            db.query(QwenModelOutput).filter_by(job_id=job_id).delete(synchronize_session=False)
            db.query(QwenBackgroundJob).filter_by(id=job_id).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()


def test_postgres_worker_does_not_requeue_after_lease_reclaim_and_inference_error(monkeypatch):
    """The exception path must not overwrite the status owned by a newer worker."""
    suffix = uuid.uuid4().hex
    person_id = f"qwen-stale-error-person-{suffix}"
    caregiver_id = f"qwen-stale-error-caregiver-{suffix}"
    job_id = str(uuid.uuid4())
    monkeypatch.setattr(worker, "SessionLocal", SessionLocal)
    try:
        with SessionLocal() as db:
            db.add(Person(id=person_id, name="Stale error recipient"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Stale error caregiver",
                relationship="Family",
                person_id=person_id,
            ))
            db.flush()
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Summarize the saved appointment note.",
                context_text="",
                evidence_ids_json="[]",
                status="queued",
                attempt_count=0,
                available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ))
            db.commit()

        async def classify(*args, **kwargs):
            return "appointment"

        async def reclaim_then_fail(payload, request, inference_db):
            newer_lock = datetime.now(timezone.utc) + timedelta(minutes=1)
            with SessionLocal() as db:
                current = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
                current.status = "processing"
                current.locked_at = newer_lock
                db.commit()
            raise RuntimeError("stale worker inference failed")

        monkeypatch.setattr(worker, "_classify_task", classify)
        from app.api import collaboration
        monkeypatch.setattr(collaboration, "qwen_support_assist", reclaim_then_fail)
        assert asyncio.run(worker.process_one_qwen_job(job_id)) is True

        with SessionLocal() as db:
            current = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
            assert current.status == "processing"
            assert current.locked_at is not None
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id,
                event_type="job_completed",
            ).count() == 0
            assert db.query(QwenJobEvent).filter_by(
                job_id=job_id,
                event_type="job_failed_or_requeued",
            ).count() == 0
            assert db.query(QwenModelOutput).filter_by(job_id=job_id).count() == 0
    finally:
        with SessionLocal() as db:
            db.query(QwenJobEvent).filter_by(job_id=job_id).delete(synchronize_session=False)
            db.query(QwenModelOutput).filter_by(job_id=job_id).delete(synchronize_session=False)
            db.query(QwenBackgroundJob).filter_by(id=job_id).delete(synchronize_session=False)
            db.query(Caregiver).filter_by(id=caregiver_id).delete(synchronize_session=False)
            db.query(Person).filter_by(id=person_id).delete(synchronize_session=False)
            db.commit()


def test_postgres_document_deletion_completes_while_extraction_is_running(monkeypatch):
    """Deletion must not wait for OCR; stale extraction must not recreate deleted content."""
    monkeypatch.setenv("SOLENOS_QWEN_WORKER_ENABLED", "false")
    entered_ocr = threading.Event()
    release_ocr = threading.Event()

    from app.api import document_lifecycle

    def blocked_extract(*args, **kwargs):
        entered_ocr.set()
        if not release_ocr.wait(timeout=15):
            raise TimeoutError("test did not release the OCR barrier")
        return "[Page 1]\\nCare note: lunch was not eaten.", "server_utf8_text"

    monkeypatch.setattr(document_lifecycle, "_extract", blocked_extract)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Document deletion race test"},
        )
        assert created.status_code == 201, created.text
        headers = {"Authorization": "Bearer " + created.json()["access_token"]}
        person_id = created.json()["person"]["id"]
        caregiver_id = created.json()["caregiver"]["id"]
        uploaded = client.post(
            "/api/v1/upload",
            headers=headers,
            data={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "evidence_type": "document",
            },
            files={"file": ("deletion-race.txt", b"Original care note.", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            extraction_future = pool.submit(
                client.post,
                f"/api/v1/documents/{evidence_id}/extract",
                headers=headers,
                json={},
            )
            assert entered_ocr.wait(timeout=10), "extraction never reached OCR"
            deletion_future = pool.submit(
                client.delete,
                f"/api/v1/documents/{evidence_id}",
                headers=headers,
            )
            deletion_finished_before_ocr_release = False
            try:
                deletion_response = deletion_future.result(timeout=5)
                deletion_finished_before_ocr_release = True
            except TimeoutError:
                release_ocr.set()
                deletion_response = deletion_future.result(timeout=15)
            finally:
                release_ocr.set()
            extraction_response = extraction_future.result(timeout=15)

        assert deletion_finished_before_ocr_release, "document deletion was blocked by the OCR transaction"
        assert deletion_response.status_code == 200, deletion_response.text
        assert extraction_response.status_code == 404, extraction_response.text
        with SessionLocal() as db:
            assert db.query(CaptureAnalysis).filter_by(
                person_id=person_id,
                source_evidence_id=evidence_id,
            ).count() == 0


def test_postgres_delete_is_not_blocked_by_document_ocr(monkeypatch):
    monkeypatch.setenv("SOLENOS_QWEN_WORKER_ENABLED", "false")
    entered, release = threading.Event(), threading.Event()
    from app.api import document_lifecycle

    def slow_extract(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return "[Page 1]\\nCare note.", "server_utf8_text"

    monkeypatch.setattr(document_lifecycle, "_extract", slow_extract)
    with TestClient(app) as client:
        created = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "OCR delete test"})
        assert created.status_code == 201, created.text
        headers = {"Authorization": "Bearer " + created.json()["access_token"]}
        person_id, caregiver_id = created.json()["person"]["id"], created.json()["caregiver"]["id"]
        uploaded = client.post(
            "/api/v1/upload", headers=headers,
            data={"person_id": person_id, "caregiver_id": caregiver_id, "evidence_type": "document"},
            files={"file": ("ocr-race.txt", b"Original care note.", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            extraction = pool.submit(client.post, f"/api/v1/documents/{evidence_id}/extract", headers=headers, json={})
            assert entered.wait(10)
            deletion = pool.submit(client.delete, f"/api/v1/documents/{evidence_id}", headers=headers)
            completed_before_release = False
            try:
                deleted = deletion.result(timeout=5)
                completed_before_release = True
            except TimeoutError:
                release.set()
                deleted = deletion.result(timeout=15)
            finally:
                release.set()
            extracted = extraction.result(timeout=15)
        assert completed_before_release
        assert deleted.status_code == 200, deleted.text
        assert extracted.status_code == 404, extracted.text
        with SessionLocal() as db:
            assert db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).count() == 0


def test_postgres_delete_is_not_blocked_by_document_interpretation(monkeypatch):
    monkeypatch.setenv("SOLENOS_QWEN_WORKER_ENABLED", "false")
    entered, release = threading.Event(), threading.Event()
    from app.api import document_intelligence

    def slow_interpretation(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return {"observations": [], "open_questions": []}

    monkeypatch.setattr(document_intelligence, "extract_review_candidates", slow_interpretation)
    with TestClient(app) as client:
        created = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Interpretation delete test"})
        assert created.status_code == 201, created.text
        headers = {"Authorization": "Bearer " + created.json()["access_token"]}
        person_id, caregiver_id = created.json()["person"]["id"], created.json()["caregiver"]["id"]
        uploaded = client.post(
            "/api/v1/upload", headers=headers,
            data={"person_id": person_id, "caregiver_id": caregiver_id, "evidence_type": "document"},
            files={"file": ("interpretation-race.txt", b"Hospital note: follow-up review recommended.", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=headers, json={})
        assert extracted.status_code == 201, extracted.text
        with ThreadPoolExecutor(max_workers=2) as pool:
            interpretation = pool.submit(
                client.post, f"/api/v1/documents/{evidence_id}/intelligence", headers=headers, json={}
            )
            assert entered.wait(10)
            deletion = pool.submit(client.delete, f"/api/v1/documents/{evidence_id}", headers=headers)
            completed_before_release = False
            try:
                deleted = deletion.result(timeout=5)
                completed_before_release = True
            except TimeoutError:
                release.set()
                deleted = deletion.result(timeout=15)
            finally:
                release.set()
            interpreted = interpretation.result(timeout=15)
        assert completed_before_release
        assert deleted.status_code == 200, deleted.text
        assert interpreted.status_code == 404, interpreted.text
        with SessionLocal() as db:
            assert db.query(CaptureAnalysis).filter_by(
                person_id=person_id, source_evidence_id=evidence_id
            ).count() == 0
