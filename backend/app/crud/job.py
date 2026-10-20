from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from typing import List, Optional
from uuid import UUID

from app.crud.base import CRUDBase
from app.models.job import Job, JobStatus, JobType
from app.schemas.job import JobCreate, JobUpdate


class CRUDJob(CRUDBase[Job, JobCreate, JobUpdate]):
    def get_by_idempotency_key(self, db: Session, *, idempotency_key: str) -> Optional[Job]:
        return db.query(Job).filter(Job.idempotency_key == idempotency_key).first()

    def get_by_owner(
        self, db: Session, *, owner_id: str, skip: int = 0, limit: int = 100
    ) -> List[Job]:
        return (
            db.query(Job)
            .filter(Job.owner_id == owner_id)
            .order_by(desc(Job.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_by_person(
        self, db: Session, *, person_id: str, skip: int = 0, limit: int = 100
    ) -> List[Job]:
        return (
            db.query(Job)
            .filter(Job.person_id == person_id)
            .order_by(desc(Job.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_by_status(
        self, db: Session, *, status: JobStatus, skip: int = 0, limit: int = 100
    ) -> List[Job]:
        return (
            db.query(Job)
            .filter(Job.status == status.value)
            .order_by(desc(Job.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_queued_jobs(self, db: Session, *, limit: int = 100) -> List[Job]:
        return (
            db.query(Job)
            .filter(Job.status == JobStatus.QUEUED.value)
            .order_by(Job.created_at.ascii)  # FIFO: oldest first
            .limit(limit)
            .all()
        )

    def get_active_jobs(self, db: Session, *, owner_id: str) -> List[Job]:
        """Get jobs that are queued, processing, or retrying for a specific owner."""
        return (
            db.query(Job)
            .filter(
                and_(
                    Job.owner_id == owner_id,
                    or_(
                        Job.status == JobStatus.QUEUED.value,
                        Job.status == JobStatus.PROCESSING.value,
                        Job.status == JobStatus.RETRYING.value,
                    ),
                )
            )
            .order_by(desc(Job.created_at))
            .all()
        )

    def claim_job(
        self, db: Session, *, job_id: str, worker_id: str, lease_duration_seconds: int = 300
    ) -> Optional[Job]:
        """
        Claim a job for processing by a worker.
        Returns the job if successfully claimed, None if already claimed or not available.
        """
        from datetime import datetime, timezone
        from sqlalchemy import and_
        
        lease_expires = datetime.now(timezone.utc).timestamp() + lease_duration_seconds
        
        job = (
            db.query(Job)
            .filter(
                and_(
                    Job.id == job_id,
                    Job.status == JobStatus.QUEUED.value,
                    or_(Job.worker_id.is_(None), Job.worker_id == ""),
                )
            )
            .with_for_update()  # Lock the row for update
            .first()
        )
        
        if job:
            job.status = JobStatus.PROCESSING.value
            job.worker_id = worker_id
            job.leased_until = lease_expires
            job.started_at = datetime.now(timezone.utc)
            job.attempt_count += 1
            db.commit()
            db.refresh(job)
            
        return job

    def release_job_lease(self, db: Session, *, job_id: str) -> Optional[Job]:
        """Release a job lease, returning it to queued status."""
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = JobStatus.QUEUED.value
            job.worker_id = None
            job.leased_until = None
            db.commit()
            db.refresh(job)
        return job

    def complete_job(
        self,
        db: Session,
        *,
        job_id: str,
        result_ref: Optional[str] = None,
        result_data: Optional[List] = None,
    ) -> Optional[Job]:
        """Mark a job as completed."""
        from datetime import datetime, timezone
        
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = JobStatus.COMPLETED.value
            job.completed_at = datetime.now(timezone.utc)
            job.worker_id = None
            job.leased_until = None
            if result_ref is not None:
                job.result_ref = result_ref
            if result_data is not None:
                job.result_data = result_data
            db.commit()
            db.refresh(job)
        return job

    def fail_job(
        self,
        db: Session,
        *,
        job_id: str,
        error_message: Optional[str] = None,
        error_code: Optional[str] = None,
        is_transient: bool = True,
    ) -> Optional[Job]:
        """Mark a job as failed, potentially retryable."""
        from datetime import datetime, timezone
        
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.error_message = error_message
            job.error_code = error_code
            
            if is_transient and job.attempt_count < job.max_attempts:
                # Schedule for retry
                job.status = JobStatus.RETRYING.value
                # Calculate next retry time with exponential backoff
                import math
                base_delay = min(2 ** (job.attempt_count - 1), 300)  # Cap at 5 minutes
                # Add jitter: +/- 25% of base delay
                jitter = base_delay * 0.25 * (2 * (hash(str(job.id)) % 1000) / 1000 - 1)
                next_retry = datetime.now(timezone.utc).timestamp() + base_delay + jitter
                job.next_retry_at = datetime.fromtimestamp(next_retry, timezone.utc)
            else:
                # Max attempts reached or non-transient failure
                job.status = JobStatus.FAILED.value
                
            job.worker_id = None
            job.leased_until = None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(job)
        return job

    def cancel_job(self, db: Session, *, job_id: str) -> Optional[Job]:
        """Cancel a job."""
        from datetime import datetime, timezone
        
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = JobStatus.CANCELLED.value
            job.worker_id = None
            job.leased_until = None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(job)
        return job


job = CRUDJob(Job)