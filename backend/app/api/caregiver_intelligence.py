"""API routes for caregiver coordination and provenance-aware family context."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import AuthenticatedPrincipal, get_current_active_user
from app.schemas.caregiver_intelligence import (
    CaregiverProfileCreate,
    CaregiverProfile as CaregiverProfileSchema,
)
from app.services.caregiver_intelligence import CaregiverIntelligenceService

router = APIRouter(tags=["caregiver-intelligence"])


def _require_person_access(person_id: str, current_user: AuthenticatedPrincipal) -> None:
    """Enforce the person boundary before reading or mutating caregiver data."""
    if not current_user.has_access_to_person(person_id) and not current_user.is_operations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person's caregiver information",
        )


@router.post(
    "/caregiver-profiles",
    response_model=CaregiverProfileSchema,
    status_code=status.HTTP_200_OK,
)
def create_caregiver_profile(
    profile: CaregiverProfileCreate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user),
):
    _require_person_access(profile.person_id, current_user)
    service = CaregiverIntelligenceService(db)
    return service.create_caregiver_profile(profile)


@router.get(
    "/caregiver-profiles/{person_id}",
    response_model=list[CaregiverProfileSchema],
)
def list_caregiver_profiles(
    person_id: str,
    db: Session = Depends(get_db),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user),
):
    _require_person_access(person_id, current_user)
    return CaregiverIntelligenceService(db).list_caregiver_profiles(person_id)
