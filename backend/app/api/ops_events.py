import hashlib
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.ops_event import OpsEvent

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ops telemetry"])

ALLOWED_EVENT_NAMES = {
    "page_view", "signup_started", "signup_completed", "care_case_created",
    "input_submitted", "document_uploaded", "step_completed", "care_record_viewed",
    "appointment_preparation_opened", "return_visit", "error_triggered",
}
ALLOWED_METADATA_KEYS = {
    "input_type", "has_documents", "doc_type", "error_code", "endpoint", "page",
}


def _hash_identifier(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


class OpsTrackInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: Optional[str] = Field(None, max_length=256)
    event_name: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=256)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_name")
    @classmethod
    def event_name_must_be_registered(cls, value: str) -> str:
        if value not in ALLOWED_EVENT_NAMES:
            raise ValueError("event_name is not registered")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_small(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if len(value) > 20:
            raise ValueError("metadata has too many fields")
        return value


def _safe_metadata(value: Dict[str, Any]) -> Dict[str, Any]:
    """Keep operational labels only; never persist free-text errors or care content."""
    safe: Dict[str, Any] = {}
    for key, item in value.items():
        if key not in ALLOWED_METADATA_KEYS:
            continue
        if isinstance(item, bool) or item is None:
            safe[key] = item
        elif isinstance(item, (int, float)):
            safe[key] = item
        elif isinstance(item, str) and len(item) <= 160:
            safe[key] = item
    return safe


@router.post("/track", status_code=202)
def track_event(payload: OpsTrackInput, db: Session = Depends(get_db)):
    event = OpsEvent(
        user_id_hash=_hash_identifier(payload.user_id),
        event_name=payload.event_name,
        session_id_hash=_hash_identifier(payload.session_id) or "",
        metadata_json=_safe_metadata(payload.metadata),
    )
    try:
        db.add(event)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Operational telemetry persistence failed")
        raise HTTPException(status_code=503, detail="Telemetry storage is unavailable") from exc
    return {"ok": True, "accepted": True, "event_id": event.id}
