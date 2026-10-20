from sqlalchemy import Column, String, DateTime, Text, Float, Integer, ForeignKey, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from enum import Enum as PyEnum


class JobStatus(PyEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(PyEnum):
    DOCUMENT_PROCESSING = "document_processing"
    SITUATION_ANALYSIS = "situation_analysis"
    CARE_SUMMARY_GENERATION = "care_summary_generation"
    EVIDENCE_PROCESSING = "evidence_processing"
    CAREGIVER_INTELLIGENCE = "caregiver_intelligence"
    # Add other job types as needed


class Job(Base):
    __tablename__ = "jobs"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Job identification
    job_type = Column(String, nullable=False, index=True)
    
    # Ownership and access control
    owner_id = Column(String, nullable=False, index=True)  # caregiver_id
    person_id = Column(String, ForeignKey("persons.id"), nullable=True, index=True)
    
    # Job state
    status = Column(String, nullable=False, default=JobStatus.QUEUED.value, index=True)
    
    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Processing details
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    
    # Input data (references to existing records)
    document_id = Column(String, ForeignKey("documents.id"), nullable=True, index=True)
    # Add other input references as needed for different job types
    
    # Output data (references to existing records)
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=True, index=True)
    care_event_id = Column(String, ForeignKey("care_events.id"), nullable=True, index=True)
    
    # Progress tracking (for multi-step jobs)
    progress_stage = Column(String, nullable=True)  # e.g., "extracting_text", "running_ocr", "analyzing_situation"
    progress_percentage = Column(Integer, nullable=True)  # 0-100
    
    # Result data
    result_ref = Column(String, nullable=True)  # Reference to result storage
    result_data = Column(JSONList, nullable=True)  # For small JSON results
    
    # Error handling
    error_message = Column(Text, nullable=True)
    error_code = Column(String, nullable=True)
    
    # Worker information for lease/claim mechanism
    worker_id = Column(String, nullable=True)  # ID of worker processing this job
    leased_until = Column(DateTime(timezone=True), nullable=True)  # When lease expires
    
    # Idempotency and deduplication
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    
    # Correlation and tracking
    correlation_id = Column(String, nullable=True, index=True)  # For tracing related jobs
    
    # Retry configuration
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    
    # Expiration and cleanup
    expires_at = Column(DateTime(timezone=True), nullable=True)  # When job should be auto-cleaned
    
    def __repr__(self):
        return f"<Job(id={self.id}, type={self.job_type}, status={self.status})>"