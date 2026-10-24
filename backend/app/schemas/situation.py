from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Any, Dict, Literal


class SituationProvenance(BaseModel):
    input_type: Optional[str] = None
    entry_method: Optional[str] = None
    captured_at: Optional[str] = None
    recognition_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    transcript_uncertain: bool = False


class SituationDocument(BaseModel):
    id: str
    name: str
    extracted_text: str
    # Existing evidence IDs must be verified against the current person/caregiver scope.
    evidence_id: Optional[str] = None
    mime_type: Optional[str] = None
    ocr_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


class SituationInput(BaseModel):
    caregiver_id: str
    client_care_key: Optional[str] = None
    person_id: str
    raw_input: str = ""
    documents: Optional[List[SituationDocument]] = None
    provenance: Optional[SituationProvenance] = None
    timestamp: Optional[str] = None
    # Explicit command for interaction lifecycle only; never resolves or deletes care reality.
    action: Optional[Literal["pause_active_care_situation"]] = None
    care_session_id: Optional[str] = Field(None, max_length=128)


class SituationEvent(BaseModel):
    id: str
    event_type: str
    title: str
    description: Optional[str] = None
    occurred_at: datetime
    evidence_ids: Optional[List[str]] = None


class InteractionPauseResponse(BaseModel):
    ok: bool
    action: Literal["pause_active_care_situation"]
    interaction_session_id: str
    paused_at: datetime
    care_reality_preserved: bool = True


class SituationResponse(BaseModel):
    ok: bool
    situation_id: str
    care_key: str
    person_id: str
    evidence_id: str
    care_event_id: str
    message: str
    events: List[SituationEvent]
    situations: Optional[List[dict[str, Any]]] = None
    attention_candidate: bool = False
    follow_up_candidate: bool = False
    what_changed: Optional[str] = None
    analysis_status: Literal["complete", "partial"] = "complete"
    analysis_warning: Optional[str] = None
    # Persisted-care projection consumed by the workspace after capture.
    care_recipient_id: Optional[str] = None
    care_recipient_display_name: Optional[str] = None
    has_context_root: bool = False
    total_events: int = 0
    care_session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    ui_situations: List[Dict[str, Any]] = Field(default_factory=list)
    active_care_situation: Optional[Dict[str, Any]] = None
    active_care_situation_turn: Optional[Dict[str, Any]] = None
    return_continuity: Optional[Dict[str, Any]] = None
    qwen_extraction_status: Literal["available", "unavailable", "not_applicable"] = "not_applicable"
    qwen_candidates: Optional[Dict[str, Any]] = None
    qwen_review_required: bool = False
    qwen_written_to_lcr: bool = False
