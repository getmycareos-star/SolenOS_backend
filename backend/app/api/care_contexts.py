from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import BaseModel

from app.core.database import get_db
from app.models.community import CommunityPost, CommunityPostStatus
from app.services.community import CommunityService

router = APIRouter(prefix="/care-contexts", tags=["care-contexts"])


class DementiaContextRequest(BaseModel):
    person_id: str
    caregiver_id: str
    context_type: str = "full"


@router.post("/dementia")
def get_dementia_context(
    request: DementiaContextRequest,
    db: Session = Depends(get_db),
):
    """Get dementia-specific care context."""
    service = CommunityService(db)
    # Leverage existing dementia-related community data
    posts = service.list_public_posts(
        care_stages=["moderate", "late"],
        limit=20,
    )
    return {
        "person_id": request.person_id,
        "caregiver_id": request.caregiver_id,
        "context": {
            "recent_posts": [
                {"id": p.id, "body": p.body, "tags": p.tags}
                for p in posts
            ],
            "stages_covered": ["moderate", "late"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
