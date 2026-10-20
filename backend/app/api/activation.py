from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db
from app.core.cache_decorators import cached_response

router = APIRouter(prefix="/activation", tags=["activation"])


class ActivationEventRequest(BaseModel):
    user_id: str
    event_type: str
    session_id: Optional[str] = None
    properties: Dict[str, Any] = {}


@router.post("/events")
def create_activation_event(
    request: ActivationEventRequest,
    db: Session = Depends(get_db),
):
    """Record an activation event."""
    return {
        "event_id": f"act_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "user_id": request.user_id,
        "event_type": request.event_type,
        "status": "recorded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@cached_response(ttl=60, key_prefix="activation_metrics")
@router.get("/metrics")
def get_activation_metrics(
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Get activation metrics."""
    return {
        "user_id": user_id,
        "metrics": {
            "total_sessions": 0,
            "active_days": 0,
            "completion_rate": 0.0,
        },
    }


@cached_response(ttl=60, key_prefix="activation_session")
@router.get("/session")
def get_activation_session(
    session_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Get activation session details."""
    return {
        "session_id": session_id,
        "status": "active",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
