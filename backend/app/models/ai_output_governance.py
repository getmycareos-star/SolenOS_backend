"""Durable AI output governance records, separate from authoritative care facts."""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, Float
from sqlalchemy.sql import func

from app.core.database import Base


def _id():
    return str(uuid.uuid4())


class AIOutputRejection(Base):
    __tablename__ = "ai_output_rejections"
    __table_args__ = (Index("ix_ai_output_rejections_output_created", "output_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    reason_code = Column(String(80), nullable=False)
    reason_text = Column(Text, nullable=False)
    rejected_by = Column(String(40), nullable=False, default="safety_system", server_default="safety_system")
    policy_version = Column(String(120), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AISafetyCheck(Base):
    __tablename__ = "ai_safety_checks"
    __table_args__ = (Index("ix_ai_safety_checks_output_created", "output_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    check_name = Column(String(120), nullable=False)
    check_version = Column(String(120), nullable=True)
    result = Column(String(32), nullable=False, default="not_run", server_default="not_run")
    risk_level = Column(String(40), nullable=False, default="unassessed", server_default="unassessed")
    details_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AISafetyViolation(Base):
    __tablename__ = "ai_safety_violations"
    __table_args__ = (Index("ix_ai_safety_violations_output_created", "output_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    safety_check_id = Column(String, ForeignKey("ai_safety_checks.id", ondelete="SET NULL"), nullable=True, index=True)
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    violation_code = Column(String(100), nullable=False)
    severity = Column(String(32), nullable=False, default="unclassified", server_default="unclassified")
    description = Column(Text, nullable=False)
    evidence_json = Column(JSON, nullable=False, default=dict)
    resolved = Column(Boolean, nullable=False, default=False, server_default="false")
    resolved_by = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AIModelVersion(Base):
    __tablename__ = "ai_model_versions"
    __table_args__ = (Index("ix_ai_model_versions_provider_name", "provider", "model_name"),)

    id = Column(String, primary_key=True, default=_id)
    provider = Column(String(80), nullable=False)
    model_name = Column(String(160), nullable=False)
    model_version = Column(String(160), nullable=False)
    artifact_digest = Column(String(256), nullable=True)
    deployment_environment = Column(String(80), nullable=True)
    is_active = Column(Boolean, nullable=False, default=False, server_default="false")
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    retired_at = Column(DateTime(timezone=True), nullable=True)


class AIPromptVersion(Base):
    __tablename__ = "ai_prompt_versions"
    __table_args__ = (Index("ix_ai_prompt_versions_key_version", "prompt_key", "version"),)

    id = Column(String, primary_key=True, default=_id)
    prompt_key = Column(String(160), nullable=False, index=True)
    version = Column(String(80), nullable=False)
    template_text = Column(Text, nullable=False)
    content_digest = Column(String(256), nullable=False)
    workflow = Column(String(120), nullable=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft")
    created_by = Column(String, nullable=True)
    change_summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    activated_at = Column(DateTime(timezone=True), nullable=True)


class AIInferenceMetric(Base):
    __tablename__ = "ai_inference_metrics"
    __table_args__ = (Index("ix_ai_inference_metrics_job_created", "job_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="SET NULL"), nullable=True, index=True)
    model_version_id = Column(String, ForeignKey("ai_model_versions.id", ondelete="SET NULL"), nullable=True)
    prompt_version_id = Column(String, ForeignKey("ai_prompt_versions.id", ondelete="SET NULL"), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    estimated_cost = Column(Float, nullable=True)
    outcome = Column(String(40), nullable=False, default="unknown", server_default="unknown")
    metrics_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AIJobStatusEvent(Base):
    __tablename__ = "ai_job_status_events"
    __table_args__ = (Index("ix_ai_job_status_events_job_created", "job_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    event_type = Column(String(80), nullable=False)
    error_code = Column(String(100), nullable=True)
    details_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AIHumanFeedback(Base):
    __tablename__ = "ai_human_feedback"
    __table_args__ = (Index("ix_ai_human_feedback_output_created", "output_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    rating = Column(String(40), nullable=True)
    feedback_type = Column(String(80), nullable=False)
    feedback_text = Column(Text, nullable=True)
    corrected_output = Column(Text, nullable=True)
    disposition = Column(String(40), nullable=False, default="pending_review", server_default="pending_review")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AIOutputLCRProposal(Base):
    __tablename__ = "ai_output_lcr_proposals"
    __table_args__ = (Index("ix_ai_output_lcr_proposals_status_created", "status", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    output_id = Column(String, ForeignKey("qwen_model_outputs.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id = Column(String, ForeignKey("qwen_background_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    proposal_type = Column(String(80), nullable=False)
    proposed_change_json = Column(JSON, nullable=False)
    source_event_ids_json = Column(JSON, nullable=False, default=list)
    source_evidence_ids_json = Column(JSON, nullable=False, default=list)
    rationale = Column(Text, nullable=True)
    uncertainty_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="pending_authorization", server_default="pending_authorization")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)


class AIOutputLCRDecision(Base):
    __tablename__ = "ai_output_lcr_decisions"
    __table_args__ = (Index("ix_ai_output_lcr_decisions_proposal_created", "proposal_id", "created_at"),)

    id = Column(String, primary_key=True, default=_id)
    proposal_id = Column(String, ForeignKey("ai_output_lcr_proposals.id", ondelete="CASCADE"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True)
    decision = Column(String(32), nullable=False)
    rationale = Column(Text, nullable=True)
    clarification_request = Column(Text, nullable=True)
    resulting_lcr_revision_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
