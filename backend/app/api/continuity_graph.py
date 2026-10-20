from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from app.core.database import get_db

router = APIRouter(prefix="/continuity-graph", tags=["continuity"])


@router.get("")
def get_continuity_graph(
    person_id: str = Query(...),
    db: Session = Depends(get_db),
):
    """Get continuity graph."""
    return {
        "person_id": person_id,
        "graph": {"nodes": [], "edges": []},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
