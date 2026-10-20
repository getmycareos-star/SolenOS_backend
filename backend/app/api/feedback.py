from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    person_id: str
    caregiver_id: Optional[str] = None
    feedback_type: str
    content: str
    rating: Optional[int] = None


@router.post("")
def submit_feedback(
    request: FeedbackRequest,
    db: Session = Depends(get_db),
):
    """Submit feedback."""
    return {
        "feedback_id": f"fb_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "status": "received",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

