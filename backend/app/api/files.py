from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import os
import shutil
import uuid

from app.core.database import get_db
from app.core.config import get_settings
from app.models.document import Document as DocumentModel
from app.models.identity import Person as PersonModel
from app.models.caregiver import Caregiver as CaregiverModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.job import Job as JobModel, JobType, JobStatus
from app.schemas.document import DocumentResponse, DocumentUploadResponse
from app.services.document_processing import (
    save_uploaded_file,
    SUPPORTED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput
from app.services.learning import LearningEngine, SavePipelineEvent
from app.crud import job
from app.schemas.job import JobCreate
from app.core.cache_decorators import cached_response

router = APIRouter()

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "uploads")
@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    caregiver_id: str = Form(...),
    person_id: str = Form(...),
    timestamp: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    caregiver = db.query(CaregiverModel).filter(
        CaregiverModel.id == caregiver_id,
        CaregiverModel.person_id == person_id,
    ).first()
    if not caregiver:
        raise HTTPException(
            status_code=403,
            detail="Caregiver not authorized for this person",
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file not allowed")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // (1024*1024)}MB",
        )

    mime_type = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if not ext:
        ext = SUPPORTED_MIME_TYPES.get(mime_type, "")

    if not SUPPORTED_MIME_TYPES.get(mime_type) and ext not in [".txt", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"]:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {mime_type or ext}. Supported: txt, pdf, docx, png, jpg, jpeg, gif, bmp, tiff",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    doc = save_uploaded_file(content, filename, mime_type, person_id, UPLOAD_DIR)
    doc.uploaded_by_caregiver_id = caregiver_id
    # Set initial processing status
    doc.processing_status = "pending"
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Create a job for asynchronous processing instead of processing synchronously
    job_id = str(uuid.uuid4())
    job_create = JobCreate(
        job_type=JobType.DOCUMENT_PROCESSING.value,
        owner_id=caregiver_id,
        person_id=person_id,
        document_id=doc.id,
        idempotency_key=f"doc_upload_{doc.id}",  # Prevent duplicate uploads
    )
    job_obj = job.create(db, obj_in=job_create)
    
    # Enqueue the job with Celery
    from app.celery import process_document_job
    process_document_job.delay(str(job_obj.id))
    
    # Return immediate response indicating job was created
    return DocumentUploadResponse(
        ok=True,
        document_id=doc.id,
        message="Document uploaded successfully. Processing will occur in the background.",
        filename=filename,
        mime_type=mime_type,
        file_size_bytes=len(content),
        document_type=doc.document_type,
        processing_status="queued",  # Indicate it's queued for processing
        # Note: We're not populating the processing results yet since they'll come later
        # The frontend will need to poll for job status to get the results
    )


@cached_response(ttl=300, key_prefix="files")
@router.get("/{person_id}", response_model=List[DocumentResponse])
def list_documents(person_id: str, db: Session = Depends(get_db)):
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    docs = (
        db.query(DocumentModel)
        .filter(DocumentModel.person_id == person_id)
        .order_by(DocumentModel.uploaded_at.desc())
        .all()
    )
    return [DocumentResponse.from_orm(doc) for doc in docs]


@cached_response(ttl=3600, key_prefix="file")
@router.get("/{person_id}/documents/{document_id}", response_model=DocumentResponse)
def get_document(person_id: str, document_id: str, db: Session = Depends(get_db)):
    doc = db.query(DocumentModel).filter(
        DocumentModel.id == document_id,
        DocumentModel.person_id == person_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentResponse.from_orm(doc)


@router.get("/{person_id}/documents/{document_id}/download")
def download_document(person_id: str, document_id: str, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse
    doc = db.query(DocumentModel).filter(
        DocumentModel.id == document_id,
        DocumentModel.person_id == person_id,
    ).first()
    if not doc or not os.path.exists(doc.storage_path):
        raise HTTPException(status_code=404, detail="Document file not found")
    return FileResponse(
        path=doc.storage_path,
        media_type=doc.mime_type,
        filename=doc.original_filename,
    )


@router.post("/{person_id}/documents/{document_id}/reprocess")
def reprocess_document(
    person_id: str,
    document_id: str,
    caregiver_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    doc = db.query(DocumentModel).filter(
        DocumentModel.id == document_id,
        DocumentModel.person_id == person_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if caregiver_id:
        caregiver = db.query(CaregiverModel).filter(
            CaregiverModel.id == caregiver_id,
            CaregiverModel.person_id == person_id,
        ).first()
        if not caregiver:
            raise HTTPException(status_code=403, detail="Caregiver not authorized")

    # Create a job for reprocessing instead of processing synchronously
    job_id = str(uuid.uuid4())
    job_create = JobCreate(
        job_type=JobType.DOCUMENT_PROCESSING.value,
        owner_id=caregiver_id or doc.uploaded_by_caregiver_id,
        person_id=person_id,
        document_id=document_id,
        idempotency_key=f"doc_reprocess_{document_id}_{job_id}",  # Unique ID for this reprocess request
    )
    job_obj = job.create(db, obj_in=job_create)
    
    # Enqueue the job with Celery
    from app.celery import process_document_job
    process_document_job.delay(str(job_obj.id))
    
    return {
        "ok": True,
        "message": "Document reprocessing has been queued for background processing.",
        "job_id": str(job_obj.id),
        "document_id": document_id,
    }
