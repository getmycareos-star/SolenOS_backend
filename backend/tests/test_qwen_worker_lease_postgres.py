"""PostgreSQL-only regression coverage for Qwen worker lease ownership."""
import asyncio
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
from app.models.care import Caregiver, Person
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
