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
    mime_type: Optional[str] = None
    ocr_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


CaptureCategory = Literal[
    "symptom",
    "behavior",
    "sleep",
    "meal",
    "appointment",
    "medication_observation",
    "other",
]
TimeProvenance = Literal["caregiver_reported_event_time", "entry_time_defaulted"]


class SituationInput(BaseModel):
    caregiver_id: str
    client_care_key: Optional[str] = None
    person_id: str
    raw_input: str = ""
    documents: Optional[List[SituationDocument]] = None
    provenance: Optional[SituationProvenance] = None
    timestamp: Optional[str] = None
    capture_category: Optional[CaptureCategory] = None
    # Explicit command for interaction lifecycle only; never resolves or deletes care reality.
    action: Optional[Literal["pause_active_care_situation"]] = None
    care_session_id: Optional[str] = Field(None, max_length=128)


class SituationEvent(BaseModel):
    id: str
    event_type: str
    title: str
    description: Optional[str] = None
    occurred_at: datetime
    time_provenance: Optional[TimeProvenance] = None
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
