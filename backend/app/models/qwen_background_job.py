import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Index, Boolean, JSON
from sqlalchemy.sql import func

from app.core.database import Base


class QwenBackgroundJob(Base):
    __tablename__ = "qwen_background_jobs"
    __table_args__ = (
        Index("ix_qwen_background_jobs_claim", "status", "available_at", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="queued", server_default="queued", index=True)
    task_text = Column(Text, nullable=False)
    context_text = Column(Text, nullable=False, default="", server_default="")
    evidence_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    appointment_id = Column(String(128), nullable=True)
    workflow = Column(String(48), nullable=True)
    result_text = Column(Text, nullable=True)
    source_event_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    source_evidence_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    error_code = Column(String(48), nullable=True)
    available_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)



class QwenModelOutput(Base):
    """Generated model content, stored separately from job lifecycle and care facts."""
    __tablename__ = "qwen_model_outputs"
    __table_args__ = (
        Index("ix_qwen_model_outputs_person_created", "person_id", "created_at"),
        Index("ix_qwen_model_outputs_review", "review_status", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow = Column(String(64), nullable=False)
    model_name = Column(String(120), nullable=True)
    model_version = Column(String(120), nullable=True)
    output_text = Column(Text, nullable=False)
    output_metadata = Column(JSON, nullable=False, default=dict)
    source_event_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    source_evidence_ids_json = Column(Text, nullable=False, default="[]", server_default="[]")
    review_status = Column(String(24), nullable=False, default="pending_review", server_default="pending_review")
    review_required = Column(Boolean, nullable=False, default=True, server_default="true")
    written_to_lcr = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QwenJobEvent(Base):
    """Append-only lifecycle trace; contains no generated care facts."""
    __tablename__ = "qwen_job_events"
    __table_args__ = (
        Index("ix_qwen_job_events_job_created", "job_id", "created_at"),
        Index("ix_qwen_job_events_person_created", "person_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String(40), nullable=False)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    error_code = Column(String(48), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QwenSafetyEvaluation(Base):
    """Safety decision for a model output; never an authoritative care assertion."""
    __tablename__ = "qwen_safety_evaluations"
    __table_args__ = (
        Index("ix_qwen_safety_evaluations_output", "output_id", "created_at"),
        Index("ix_qwen_safety_evaluations_person", "person_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    risk_class = Column(String(40), nullable=False, default="unassessed", server_default="unassessed")
    decision = Column(String(40), nullable=False, default="review_required", server_default="review_required")
    human_review_required = Column(Boolean, nullable=False, default=True, server_default="true")
    violations_json = Column(JSON, nullable=False, default=list)
    policy_references_json = Column(JSON, nullable=False, default=list)
    reviewer_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
