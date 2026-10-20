from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter(prefix="/track", tags=["track"])


class TrackEvent(BaseModel):
    event_type: str
    person_id: str
    caregiver_id: Optional[str] = None
    properties: Dict[str, Any] = {}


@router.post("")
def track_event(
    event: TrackEvent,
    db: Session = Depends(get_db),
):
    """Track an analytics event."""
    return {
        "event_id": f"evt_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "event_type": event.event_type,
        "status": "tracked",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
