from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Integer, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.llm_enums import (
    LLMStatus,
    LLMTask,
    LLMReasoningMode,
    GuardVerdict,
)


class LLMCall(Base):
    """Immutable audit record for every attempt to reach the local model.

    Trust rule 4 requires corrections to be logged. A model output that turns
    out to be wrong is only recoverable if the raw prompt, the raw response, the
    model version and the guard verdict were all written down at the time. The
    resolved_ flag exists so a caregiver can mark a past model call as wrong
    without ever editing or deleting it.
    """

    __tablename__ = "llm_calls"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=True, index=True)
    task = Column(SQLEnum(LLMTask), nullable=False, index=True)
    mode = Column(SQLEnum(LLMReasoningMode), nullable=False, default=LLMReasoningMode.LLM_ASSISTED)
    model = Column(String, nullable=False)
    provider = Column(String, nullable=False, default="ollama")

    status = Column(SQLEnum(LLMStatus), nullable=False, default=LLMStatus.OK)
    guard_verdict = Column(SQLEnum(GuardVerdict), nullable=True)
    guard_reasons = Column(JSONList, nullable=True)

    prompt_text = Column(Text, nullable=True)
    raw_response = Column(Text, nullable=True)
    parsed_output = Column(JSONList, nullable=True)
    error_detail = Column(Text, nullable=True)

    input_evidence_ids = Column(JSONList, nullable=True)
    output_evidence_ids = Column(JSONList, nullable=True)
    dropped_evidence_ids = Column(JSONList, nullable=True)

    latency_ms = Column(Integer, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    attempt = Column(Integer, nullable=False, default=1)

    is_fallback = Column(Boolean, nullable=False, default=False)
    is_resolved = Column(Boolean, nullable=False, default=False)
    resolution_note = Column(Text, nullable=True)

    called_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class LLMReasoningResult(Base):
    """A guarded language-model contribution, in a form safe to persist.

    This row only exists when the trust guard accepted or qualified model output.
    Rejected output is kept in the LLMCall audit row and deliberately not turned
    into a ReasoningResult, so nothing downstream can accidentally read it.
    """

    __tablename__ = "llm_reasoning_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    task = Column(SQLEnum(LLMTask), nullable=False, index=True)
    call_id = Column(String, ForeignKey("llm_calls.id"), nullable=True, index=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True, index=True)

    summary = Column(Text, nullable=False)
    detail = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=False)
    possible_context = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)

    guard_verdict = Column(SQLEnum(GuardVerdict), nullable=False, default=GuardVerdict.ACCEPTED)
    qualifications = Column(JSONList, nullable=True)

    is_active = Column(Boolean, default=True)
    superseded_by_result_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
