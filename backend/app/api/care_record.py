from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.security import (
    get_principal_from_request,
    require_care_context_access,
    INPUT_LIMITS,
)

router = APIRouter(prefix="/care-record", tags=["care-record"])


@router.get("/timeline")
def get_care_record_timeline(
    request: Request,
    person_id: str = Query(...),
    limit: int = Query(50),
    db: Session = Depends(get_db),
):
    """Get care record timeline."""
    principal = get_principal_from_request(request)
    require_care_context_access(principal, person_id, "care_record", request)
    limit = min(limit, INPUT_LIMITS["pagination_max"])
    return {
        "person_id": person_id,
        "records": [],
        "limit": limit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

