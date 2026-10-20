"""API endpoint for getting information-reducing actions (What Should I Do?).
Part II of SOLOS specification: Day 1 "What Should I Do?" Moments.
"""

from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.services.what_should_i_do_service import get_what_should_i_do_actions
from app.core.cache_decorators import cached_response

router = APIRouter()


@cached_response(ttl=300, key_prefix="what_should_i_do")
@router.get("/persons/{person_id}/what-should-i-do", response_model=List[dict])
def get_what_should_i_do_actions(
    person_id: str = Path(..., description="The ID of the person"),
    db: Session = Depends(get_db)
) -> List[dict]:
    """
    Get information-reducing actions based on unresolved care context.
    Implements Part II of SOLOS specification: Day 1 "What Should I Do?" Moments.
    
    Returns a list of suggested actions with metadata explaining:
    - What needs clarification
    - Who can help clarify it (if known)
    - What information is missing
    - What needs follow-up
    - What is coming up
    - What should be brought forward
    """
    try:
        actions = get_what_should_i_do_actions(db, person_id)
        return actions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving what-should-i-do actions: {str(e)}")


@cached_response(ttl=300, key_prefix="what_should_i_do_summary")
@router.get("/persons/{person_id}/what-should-i-do/summary")
def get_what_should_i_do_summary(
    person_id: str = Path(..., description="The ID of the person"),
    db: Session = Depends(get_db)
) -> dict:
    """
    Get a summary of what the caregiver should do next.
    Returns a simplified view highlighting the most important actions.
    """
    try:
        actions = get_what_should_i_do_actions(db, person_id)
        
        # Categorize actions by type
        action_counts = {}
        for action in actions:
            action_type = action.get('type', 'unknown')
            action_counts[action_type] = action_counts.get(action_type, 0) + 1
        
        # Get top 3 highest priority actions
        top_actions = sorted(actions, key=lambda x: (
            {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}.get(x.get('priority', 'low'), 1),
            x.get('due_at', '9999-12-31')
        ), reverse=True)[:3]
        
        return {
            "person_id": person_id,
            "total_actions_identified": len(actions),
            "action_breakdown": action_counts,
            "top_actions": top_actions,
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving what-should-i-do summary: {str(e)}")
