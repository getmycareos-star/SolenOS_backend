"""
Celery configuration for SolenOS asynchronous processing.
"""
from celery import Celery
from app.core.config import get_settings

# Get settings
settings = get_settings()

# Create Celery instance
celery_app = Celery(
    "solenos",
    broker=settings.redis_url if hasattr(settings, 'redis_url') else "redis://localhost:6379/0",
    backend=settings.redis_url if hasattr(settings, 'redis_url') else "redis://localhost:6379/0",
    include=[
        "app.services.document_processing",
        "app.services.situation",
        "app.services.care_summary",
        # Add other service modules as needed for asynchronous processing
    ]
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=1000,
)

# The task is defined in the Celery application module so workers can discover
# it when launched with `celery -A app.celery:celery_app worker`.
@celery_app.task(name="app.celery.process_document_job")
def process_document_job(job_id: str):
    """Process an uploaded document and persist its canonical evidence/event links.

    The HTTP request only enqueues work. This task owns extraction and the
    downstream evidence/event write, and records terminal status on the job so
    clients can poll for a verifiable result.
    """
    from datetime import datetime, timezone

    from app.core.database import SessionLocal
    from app.models.document import Document
    from app.models.job import Job, JobStatus
    from app.schemas.situation import SituationDocument, SituationInput
    from app.services.document_processing import process_and_persist_document
    from app.services.situation import process_situation_input

    with SessionLocal() as db:
        job = db.query(Job).filter(Job.id == str(job_id)).first()
        if job is None:
            return {"ok": False, "status": "not_found", "job_id": str(job_id)}
        if job.status == JobStatus.COMPLETED.value:
            return {
                "ok": True,
                "status": "completed",
                "job_id": job.id,
                "document_id": job.document_id,
                "evidence_id": job.evidence_id,
                "care_event_id": job.care_event_id,
            }

        doc = db.query(Document).filter(Document.id == job.document_id).first()
        if doc is None:
            job.status = JobStatus.FAILED.value
            job.error_code = "DOCUMENT_NOT_FOUND"
            job.error_message = "The document referenced by this job does not exist."
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {"ok": False, "status": "failed", "job_id": job.id}

        job.status = JobStatus.PROCESSING.value
        job.started_at = job.started_at or datetime.now(timezone.utc)
        job.attempt_count = (job.attempt_count or 0) + 1
        job.progress_stage = "extracting_text"
        job.progress_percentage = 10
        db.commit()

        try:
            extraction = process_and_persist_document(db, doc)
            if extraction.errors or not extraction.text.strip():
                job.status = JobStatus.FAILED.value
                job.error_code = "DOCUMENT_EXTRACTION_FAILED"
                job.error_message = "; ".join(extraction.errors) or "No text could be extracted."
                job.progress_stage = "failed"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return {"ok": False, "status": "failed", "job_id": job.id}

            job.progress_stage = "creating_canonical_evidence"
            job.progress_percentage = 70
            db.commit()

            situation = process_situation_input(
                db,
                SituationInput(
                    caregiver_id=doc.uploaded_by_caregiver_id,
                    person_id=doc.person_id,
                    documents=[
                        SituationDocument(
                            id=doc.id,
                            name=doc.original_filename,
                            extracted_text=extraction.text,
                            mime_type=doc.mime_type,
                            ocr_confidence=extraction.confidence,
                        )
                    ],
                    timestamp=(
                        doc.uploaded_at.isoformat()
                        if doc.uploaded_at is not None
                        else datetime.now(timezone.utc).isoformat()
                    ),
                ),
            )
            doc.evidence_id = situation.evidence_id
            doc.care_event_id = situation.care_event_id
            job.evidence_id = situation.evidence_id
            job.care_event_id = situation.care_event_id
            job.status = JobStatus.COMPLETED.value
            job.progress_stage = "completed"
            job.progress_percentage = 100
            job.result_ref = doc.id
            job.error_code = None
            job.error_message = None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {
                "ok": True,
                "status": "completed",
                "job_id": job.id,
                "document_id": doc.id,
                "evidence_id": doc.evidence_id,
                "care_event_id": doc.care_event_id,
            }
        except Exception as exc:
            db.rollback()
            failed_job = db.query(Job).filter(Job.id == str(job_id)).first()
            if failed_job is not None:
                failed_job.status = JobStatus.FAILED.value
                failed_job.error_code = "DOCUMENT_PROCESSING_FAILED"
                failed_job.error_message = str(exc)[:1000]
                failed_job.progress_stage = "failed"
                failed_job.completed_at = datetime.now(timezone.utc)
                db.commit()
            return {"ok": False, "status": "failed", "job_id": str(job_id)}

