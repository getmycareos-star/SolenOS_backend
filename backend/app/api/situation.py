from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    AuthenticatedPrincipal,
    get_current_principal,
    require_authenticated,
    require_care_context_access,
)
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput, SituationResponse


async def authorize_situation_context(
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> None:
    """Authenticate capture and bind both person and caregiver to the caller.

    IDs in a JSON body are references, not authorization. The person must be
    in the principal's care context, and the caregiver attribution must match
    an active caregiver profile linked by the authenticated account email.
    """
    require_authenticated(principal)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}
    person_id = payload.get("person_id")
    caregiver_id = payload.get("caregiver_id")
    if not person_id or not caregiver_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Person and caregiver context are required",
        )

    person = db.query(Person).filter(Person.id == str(person_id)).first()
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")

    require_care_context_access(principal, str(person_id), "situation capture")

    caller_email = str(principal.account.email or "").strip().lower()
    caregiver = db.query(Caregiver).filter(
        Caregiver.id == str(caregiver_id),
        Caregiver.person_id == str(person_id),
        func.lower(Caregiver.email) == caller_email,
        Caregiver.is_active.is_(True),
    ).first()
    if caregiver is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caregiver profile is not linked to the authenticated account and person",
        )


router = APIRouter(
    prefix="/api",
    tags=["situation"],
    dependencies=[Depends(authorize_situation_context)],
)


@router.post("/situation", response_model=SituationResponse)
def create_situation(
    situation_input: SituationInput,
    db: Session = Depends(get_db),
):
    try:
        result = process_situation_input(db, situation_input)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        # Keep implementation details out of client responses. The server logs
        # the traceback for diagnosis; callers receive a stable error contract.
        import logging

        logging.getLogger(__name__).exception("Situation processing failed")
        raise HTTPException(
            status_code=500,
            detail="Situation processing failed",
        ) from e
