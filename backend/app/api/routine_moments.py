from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.core.security import get_current_principal, require_care_context_access, AuthenticatedPrincipal
from app.crud.routine_moment import (
    get_routine_moment,
    get_routine_moments_by_person,
    get_routine_moments_by_person_and_routine,
    create_routine_moment,
    update_routine_moment,
    delete_routine_moment,
)
from app.schemas.routine_moment import RoutineMomentCreate, RoutineMomentUpdate, RoutineMoment

router = APIRouter(prefix="/routine-moments", tags=["routine-moments"])


def get_principal_and_check_person(person_id: str, principal: AuthenticatedPrincipal = Depends(get_current_principal)):
    """Dependency that ensures the principal is authenticated and has access to the person."""
    if not principal.is_authenticated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not principal.has_access_to_person(person_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this person")
    return principal


@router.get("/", response_model=List[RoutineMoment])
def list_routine_moments(
    person_id: str,
    routine_name: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    principal: AuthenticatedPrincipal = Depends(get_principal_and_check_person),
    db: Session = Depends(get_db),
):
    """List routine moments for a person, optionally filtered by routine name."""
    if routine_name:
        moments = get_routine_moments_by_person_and_routine(
            db, person_id=person_id, routine_name=routine_name, skip=offset, limit=limit
        )
    else:
        moments = get_routine_moments_by_person(
            db, person_id=person_id, skip=offset, limit=limit
        )
    return moments


@router.post("/", response_model=RoutineMoment)
def create_routine_moment_endpoint(
    observation: RoutineMomentCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal_and_check_person),
    db: Session = Depends(get_db),
):
    """Create a new routine moment."""
    # Ensure the observation person_id matches the authorized person (should be same as in path?)
    # The observation includes person_id; we should verify it matches the authorized person.
    if not principal.has_access_to_person(observation.person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create routine moment for this person",
        )
    return create_routine_moment(db, observation)


@router.get("/{moment_id}", response_model=RoutineMoment)
def get_routine_moment_endpoint(
    moment_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Get a specific routine moment by ID."""
    moment = get_routine_moment(db, moment_id)
    if not moment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routine moment not found")
    # Authorization: check access to the person associated with the moment
    if not principal.has_access_to_person(moment.person_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this moment")
    return moment


@router.patch("/{moment_id}", response_model=RoutineMoment)
def update_routine_moment_endpoint(
    moment_id: str,
    update: RoutineMomentUpdate,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Update a routine moment."""
    moment = get_routine_moment(db, moment_id)
    if not moment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routine moment not found")
    if not principal.has_access_to_person(moment.person_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update this moment")
    updated_moment = update_routine_moment(db, moment_id, update)
    if not updated_moment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routine moment not found")
    return updated_moment


@router.delete("/{moment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routine_moment_endpoint(
    moment_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Delete a routine moment."""
    moment = get_routine_moment(db, moment_id)
    if not moment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routine moment not found")
    if not principal.has_access_to_person(moment.person_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this moment")
    success = delete_routine_moment(db, moment_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routine moment not found")
    return None