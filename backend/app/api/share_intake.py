from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter(prefix="/share-intake", tags=["share"])


class ShareIntakeRequest(BaseModel):
    person_id: str
    shared_by_caregiver_id: str
    shared_with: List[str]
    permissions: List[str] = ["read"]


@router.post("")
def create_share_intake(
    request: ShareIntakeRequest,
    db: Session = Depends(get_db),
):
    """Create a share intake link."""
    return {
        "share_id": f"shr_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "token": f"share_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "person_id": request.person_id,
        "expires_at": None,
        "status": "active",
    }


@router.get("/{token}")
def get_share_intake(
    token: str,
    db: Session = Depends(get_db),
):
    """Get share intake details."""
    return {
        "token": token,
        "person_id": "unknown",
        "status": "active",
    }
