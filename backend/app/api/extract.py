from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from app.core.database import get_db

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("")
def extract(
    text: str,
    source_type: str = "text",
    person_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Extract structured information from text."""
    return {
        "extracted": {
            "entities": [],
            "actions": [],
            "observations": [],
        },
        "source_type": source_type,
        "person_id": person_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
