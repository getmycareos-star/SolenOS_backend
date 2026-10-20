from fastapi import APIRouter, Depends, HTTPException, Query, Path, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.api import deps
from app.core.config import get_settings
from app.crud import job
from app.models.job import JobStatus, JobType
from app.schemas.job import JobResponse, JobListResponse, JobCreate, JobUpdate
from app.core.cache_decorators import cached_response

router = APIRouter()


@router.post("/", response_model=JobResponse)
def create_job(
    *,
    db: Session = Depends(deps.get_db),
    job_in: JobCreate,
    current_user_id: str = Depends(deps.get_current_user_id),
):
    """
    Create a new job.
    """
    # Verify ownership - user can only create jobs for themselves
    if job_in.owner_id != current_user_id:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to create job for this user",
        )
    
    # Check for idempotency
    if job_in.idempotency_key:
        existing_job = job.get_by_idempotency_key(db, idempotency_key=job_in.idempotency_key)
        if existing_job:
            return existing_job
    
    job_obj = job.create(db, obj_in=job_in)
    return job_obj


@cached_response(ttl=60, key_prefix="jobs_list")
@router.get("/", response_model=JobListResponse)
def read_jobs(
    db: Session = Depends(deps.get_db),
    current_user_id: str = Depends(deps.get_current_user_id),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[JobStatus] = Query(None),
    job_type: Optional[JobType] = Query(None),
):
    """
    Retrieve jobs for the current user with optional filtering.
    """
    query = db.query(job.model).filter(job.model.owner_id == current_user_id)
    
    if status:
        query = query.filter(job.model.status == status.value)
    
    if job_type:
        query = query.filter(job.model.job_type == job_type.value)
    
    total = query.count()
    jobs = query.order_by(job.model.created_at.desc()).offset(skip).limit(limit).all()
    
    return JobListResponse(
        jobs=jobs,
        total=total,
        page=skip // limit + 1,
        size=len(jobs),
        pages=(total + limit - 1) // limit,
    )


@cached_response(ttl=300, key_prefix="job")
@router.get("/{job_id}", response_model=JobResponse)
def read_job(
    *,
    db: Session = Depends(deps.get_db),
    job_id: str = Path(..., title="The ID of the job to get"),
    current_user_id: str = Depends(deps.get_current_user_id),
):
    """
    Get a specific job by ID.
    """
    job_obj = job.get(db, id=job_id)
    if not job_obj:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership
    if job_obj.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this job")
    
    return job_obj


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    *,
    db: Session = Depends(deps.get_db),
    job_id: str = Path(..., title="The ID of the job to cancel"),
    current_user_id: str = Depends(deps.get_current_user_id),
):
    """
    Cancel a job.
    """
    job_obj = job.get(db, id=job_id)
    if not job_obj:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership
    if job_obj.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized to cancel this job")
    
    # Only allow cancelling queued or processing jobs
    if job_obj.status not in [JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.RETRYING]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job with status {job_obj.status}",
        )
    
    updated_job = job.cancel_job(db, job_id=job_id)
    return updated_job


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(
    *,
    db: Session = Depends(deps.get_db),
    job_id: str = Path(..., title="The ID of the job to retry"),
    current_user_id: str = Depends(deps.get_current_user_id),
):
    """
    Retry a failed job.
    """
    job_obj = job.get(db, id=job_id)
    if not job_obj:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership
    if job_obj.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized to retry this job")
    
    # Only allow retrying failed jobs
    if job_obj.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry failed jobs, current status: {job_obj.status}",
        )
    
    # Reset job to queued state for retry
    updated_job = job.update(
        db,
        db_obj=job_obj,
        obj_in=JobUpdate(
            status=JobStatus.QUEUED,
            attempt_count=0,  # Reset attempt count for retry
            error_message=None,
            error_code=None,
            worker_id=None,
            leased_until=None,
            next_retry_at=None,
        ),
    )
    return updated_job


@cached_response(ttl=30, key_prefix="job_status")
@router.get("/{job_id}/status", response_model=JobResponse)
def get_job_status(
    *,
    db: Session = Depends(deps.get_db),
    job_id: str = Path(..., title="The ID of the job to get status for"),
    current_user_id: str = Depends(deps.get_current_user_id),
):
    """
    Get the current status of a job (lightweight endpoint for polling).
    """
    job_obj = job.get(db, id=job_id)
    if not job_obj:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Check ownership
    if job_obj.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this job")
    
    return job_obj