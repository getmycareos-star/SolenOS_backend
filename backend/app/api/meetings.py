from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter(prefix="/meetings", tags=["meetings"])


class MeetingCreate(BaseModel):
    person_id: str
    title: str
    scheduled_at: datetime
    attendees: List[str] = []
    agenda: List[str] = []


@router.get("")
def list_meetings(
    person_id: str = Query(...),
    trigger: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """List meetings for a person."""
    return {
        "person_id": person_id,
        "meetings": [],
        "trigger": trigger,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("")
def create_meeting(
    meeting: MeetingCreate,
    db: Session = Depends(get_db),
):
    """Create a meeting."""
    return {
        "meeting_id": f"mtg_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "person_id": meeting.person_id,
        "title": meeting.title,
        "scheduled_at": meeting.scheduled_at.isoformat(),
        "status": "scheduled",
    }


@router.post("/prepare")
def prepare_meeting(
    person_id: str = Body(...),
    meeting_type: str = Body(...),
    scheduled_at: Optional[datetime] = Body(None),
    db: Session = Depends(get_db),
):
    """Prepare meeting materials."""
    return {
        "person_id": person_id,
        "meeting_type": meeting_type,
        "agenda": ["Review medications", "Discuss symptoms", "Update care plan"],
        "questions": ["Any medication changes?", "New symptoms?"],
        "documents": ["Medication list", "Recent labs", "Care summary"],
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
