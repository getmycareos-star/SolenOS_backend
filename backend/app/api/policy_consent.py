from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter(prefix="/policy/consent", tags=["policy"])


class ConsentRequest(BaseModel):
    user_id: str
    consent_type: str
    granted: bool
    scope: Optional[List[str]] = None


@router.post("")
def manage_consent(
    request: ConsentRequest,
    db: Session = Depends(get_db),
):
    """Record consent decision."""
    return {
        "consent_id": f"cnst_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "user_id": request.user_id,
        "consent_type": request.consent_type,
        "granted": request.granted,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("")
def get_consent(
    user_id: str = Query(...),
    consent_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get consent status."""
    return {
        "user_id": user_id,
        "consents": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
