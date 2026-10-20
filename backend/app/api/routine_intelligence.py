from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.core.security import get_current_principal, require_care_context_access, AuthenticatedPrincipal
from app.crud.routine_moment import get_routine_moments_by_person
from app.schemas.routine_moment import RoutineMoment

router = APIRouter(prefix="/routines", tags=["routines"])


def get_principal_and_check_person(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal)):
    if not principal.is_authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not principal.has_access_to_person(person_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this person")
    return principal


@router.get("/{person_id}/moments", response_model=List[RoutineMoment])
def get_routine_moments_for_person(
    person_id: str,
    principal: AuthenticatedPrincipal = Depends(get_principal_and_check_person),
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
):
    """Get routine moments for a person (canonical endpoint per capability matrix)."""
    moments = get_routine_moments_by_person(db, person_id=person_id, skip=offset, limit=limit)
    return moments