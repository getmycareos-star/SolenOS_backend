"""
Celery worker for SolenOS asynchronous job processing.
This worker can be run independently to process queued jobs.
"""
from app.celery import celery_app
from app.core.database import SessionLocal
from app.crud import job
from app.models.job import JobStatus, JobType
from app.services.document_processing import process_and_persist_document
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput
from app.models.document import Document as DocumentModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
import logging
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def process_document_job(self, job_id: str):
    """
    Process a document processing job.
    This is the main task that handles document text extraction, OCR, etc.
    """
    db = SessionLocal()
    try:
        # Get the job
        job_obj = job.get(db, id=job_id)
        if not job_obj:
            logger.error(f"Job {job_id} not found")
            return {"status": "error", "message": "Job not found"}
        
        # Check ownership and permissions would be handled at job creation time
        # Update job status to processing
        job.update_job_status(db, job_id=job_id, status=JobStatus.PROCESSING)
        
        # Get the associated document
        document = db.query(DocumentModel).filter(DocumentModel.id == job_obj.document_id).first()
        if not document:
            logger.error(f"Document {job_obj.document_id} not found for job {job_id}")
            job.fail_job(db, job_id=job_id, error_message="Document not found", is_transient=False)
            return {"status": "error", "message": "Document not found"}
        
        # Update progress
        job.update_job_progress(db, job_id=job_id, stage="extracting_text", percentage=10)
        
        # Process the document using existing logic
        # Note: We're calling the existing synchronous function, but now it's running in the worker
        result = process_and_persist_document(db, document)
        
        # Update progress
        job.update_job_progress(db, job_id=job_id, stage="creating_evidence", percentage=50)
        
        # Create evidence record from results (similar to what happens in files.py)
        evidence = EvidenceModel(
            person_id=document.person_id,
            type="document",
            source_text=result.text,
            original_file_path=document.storage_path,
            uploaded_by_caregiver_id=document.uploaded_by_caregiver_id,
            confidence=result.confidence,
            evidence_status="processed",
            source_type="uploaded_document",
        )
        db.add(evidence)
        db.flush()
        document.evidence_id = evidence.id
        
        # Update progress
        job.update_job_progress(db, job_id=job_id, stage="processing_situation", percentage=70)
        
        # Process situation input (similar to what happens in files.py)
        situation_payload = SituationInput(
            caregiver_id=document.uploaded_by_caregiver_id,
            person_id=document.person_id,
            raw_input=result.text if result.text.strip() else f"[Document: {document.original_filename}] No extractable text",
            documents=[{
                "id": document.id,
                "name": document.original_filename,
                "extracted_text": result.text,
                "mime_type": document.mime_type,
                "ocr_confidence": result.ocr_confidence,
            }],
            provenance={
                "input_type": "ocr_text" if result.ocr_status.value != "not_required" else "document",
                "entry_method": "upload",
                "recognition_confidence": result.confidence,
                "transcript_uncertain": result.ocr_confidence is not None and result.ocr_confidence < 0.7 if result.ocr_confidence else False,
            },
        )
        
        try:
            situation_result = process_situation_input(db, situation_payload)
            document.care_event_id = situation_result.care_event_id
        except Exception as e:
            logger.warning(f"Situation processing failed for job {job_id}: {e}")
            # Don't fail the entire job for situation processing failures
            # Just continue with what we have
        
        # Update progress
        job.update_job_progress(db, job_id=job_id, stage="finalizing", percentage=90)
        
        # Mark job as completed
        job.complete_job(
            db,
            job_id=job_id,
            result_ref=evidence.id,
            result_data=[
                {
                    "evidence_id": evidence.id,
                    "care_event_id": document.care_event_id,
                    "extracted_text_length": len(result.text),
                    "confidence": result.confidence,
                }
            ]
        )
        
        logger.info(f"Document processing job {job_id} completed successfully")
        return {"status": "success", "job_id": job_id}
        
    except Exception as e:
        logger.error(f"Error processing document job {job_id}: {e}", exc_info=True)
        # Fail the job
        job.fail_job(db, job_id=job_id, error_message=str(e), is_transient=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True)
def process_situation_job(self, job_id: str):
    """
    Process a situation analysis job.
    """
    db = SessionLocal()
    try:
        # Get the job
        job_obj = job.get(db, id=job_id)
        if not job_obj:
            logger.error(f"Job {job_id} not found")
            return {"status": "error", "message": "Job not found"}
        
        # Update job status to processing
        job.update_job_status(db, job_id=job_id, status=JobStatus.PROCESSING)
        
        # TODO: Implement situation analysis job processing
        # This would depend on what specific situation analysis needs to be done
        
        # For now, just mark as completed
        job.complete_job(db, job_id=job_id)
        
        logger.info(f"Situation analysis job {job_id} completed")
        return {"status": "success", "job_id": job_id}
        
    except Exception as e:
        logger.error(f"Error processing situation job {job_id}: {e}", exc_info=True)
        job.fail_job(db, job_id=job_id, error_message=str(e), is_transient=True)
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


# Helper functions for job operations (these would ideally be in the CRUD layer)
def _get_job_crud():
    from app.crud.job import job
    return job


# Monkey-patch helper methods onto the job CRUD object for easy access
job_crud = _get_job_crud()

def update_job_status(db: Session, *, job_id: str, status: JobStatus):
    return job_crud.update(db, db_obj=job_crud.get(db, id=job_id), obj_in={"status": status.value})

def update_job_progress(db: Session, *, job_id: str, stage: str, percentage: int):
    updates = {"progress_stage": stage}
    if percentage is not None:
        updates["progress_percentage"] = percentage
    return job_crud.update(db, db_obj=job_crud.get(db, id=job_id), obj_in=updates)

# Attach helper methods to the job crud object for use in tasks
job_crud.update_job_status = lambda self, db, *, job_id, status: update_job_status(db, job_id=job_id, status=status)
job_crud.update_job_progress = lambda self, db, *, job_id, stage, percentage=None: update_job_progress(db, job_id=job_id, stage=stage, percentage=percentage)
job_crud.complete_job = lambda self, db, *, job_id, result_ref=None, result_data=None: complete_job(db, job_id=job_id, result_ref=result_ref, result_data=result_data)
job_crud.fail_job = lambda self, db, *, job_id, error_message=None, error_code=None, is_transient=True: fail_job(db, job_id=job_id, error_message=error_message, error_code=error_code, is_transient=is_transient)

def complete_job(db: Session, *, job_id: str, result_ref: Optional[str] = None, result_data: Optional[List] = None):
    from datetime import datetime, timezone
    job_obj = job.get(db, id=job_id)
    if job_obj:
        job_obj.status = JobStatus.COMPLETED.value
        job_obj.completed_at = datetime.now(timezone.utc)
        job_obj.worker_id = None
        job_obj.leased_until = None
        if result_ref is not None:
            job_obj.result_ref = result_ref
        if result_data is not None:
            job_obj.result_data = result_data
        db.commit()
        db.refresh(job_obj)
    return job_obj

def fail_job(db: Session, *, job_id: str, error_message: Optional[str] = None, error_code: Optional[str] = None, is_transient: bool = True):
    from datetime import datetime, timezone
    job_obj = job.get(db, id=job_id)
    if job_obj:
        job_obj.error_message = error_message
        job_obj.error_code = error_code
        
        if is_transient and job_obj.attempt_count < job_obj.max_attempts:
            job_obj.status = JobStatus.RETRYING.value
            # Calculate next retry time with exponential backoff
            import math
            base_delay = min(2 ** (job_obj.attempt_count - 1), 300)  # Cap at 5 minutes
            # Add jitter: +/- 25% of base delay
            import random
            jitter = base_delay * 0.25 * (2 * random.random() - 1)
            next_retry = datetime.now(timezone.utc).timestamp() + base_delay + jitter
            job_obj.next_retry_at = datetime.fromtimestamp(next_retry, timezone.utc)
        else:
            job_obj.status = JobStatus.FAILED.value
            
        job_obj.worker_id = None
        job_obj.leased_until = None
        job_obj.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(job_obj)
    return job_obj