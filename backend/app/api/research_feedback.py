from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter(prefix="/research-feedback", tags=["research"])


class ResearchFeedbackRequest(BaseModel):
    person_id: str
    question_id: str
    response: str
    confidence: Optional[float] = None


@router.post("")
def submit_research_feedback(
    request: ResearchFeedbackRequest,
    db: Session = Depends(get_db),
):
    """Submit research feedback."""
    return {
        "feedback_id": f"rf_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "status": "recorded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
