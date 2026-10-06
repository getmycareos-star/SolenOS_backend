"""Care sharing endpoints.

The grant routes are family-facing: someone with access to the record decides
what another caregiver may see. The disclosure route is caregiver-facing: it
answers "what can I see" and nothing more.

Note what is absent. There is no route that lists who else is on the circle,
because knowing who else can see your mother's record is itself information about
the family. There is no route that deletes a grant, only one that revokes it,
because a revoked grant that has been erased cannot answer the question a family
will ask in six months.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.sharing_enums import (
    SHAREABLE_CATEGORIES,
    ShareCategory,
    ShareRelationship,
    ShareScope,
)
from app.services.care_sharing import CareSharingService, default_categories_for

router = APIRouter(prefix="/care-sharing", tags=["care-sharing"])


class GrantCreate(BaseModel):
    person_id: str
    caregiver_id: str
    category: str
    granted_by_caregiver_id: Optional[str] = None
    relationship: str = ShareRelationship.OTHER.value
    scope: str = ShareScope.NAMED.value
    reason: Optional[str] = None
    expires_in_days: Optional[int] = Field(None, gt=0)


class RevokeRequest(BaseModel):
    revoked_by_caregiver_id: Optional[str] = None
    reason: Optional[str] = None


class GrantResponse(BaseModel):
    id: str
    person_id: str
    caregiver_id: str
    category: str
    scope: str
    status: str
    relationship: str
    reason: Optional[str] = None
    granted_at: str
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None


class OnboardRequest(BaseModel):
    person_id: str
    caregiver_id: str
    relationship: str
    granted_by_caregiver_id: Optional[str] = None


def get_service(db: Session = Depends(get_db)) -> CareSharingService:
    return CareSharingService(db)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _to_response(row) -> GrantResponse:
    return GrantResponse(
        id=row.id,
        person_id=row.person_id,
        caregiver_id=row.caregiver_id,
        category=row.category.value,
        scope=row.scope.value,
        status=row.status.value,
        relationship=row.relationship.value,
        reason=row.reason,
        granted_at=_iso(row.granted_at),
        expires_at=_iso(row.expires_at),
        revoked_at=_iso(row.revoked_at),
    )


@router.get("/categories")
def categories():
    """What can be shared, and what each one actually means.

    Returned with descriptions rather than bare enum values so a caller building
    a consent screen shows a caregiver what they are agreeing to.
    """
    from app.services.care_sharing import _CATEGORY_PHRASES

    return {
        "categories": [
            {
                "value": c.value,
                "means": _CATEGORY_PHRASES.get(c, ""),
                "family_only": c in {
                    ShareCategory.CLINICAL_DOCUMENTS,
                    ShareCategory.MEANING_AND_WHAT_MATTERS,
                },
            }
            for c in ShareCategory
            if c in SHAREABLE_CATEGORIES
        ],
        "scopes": [
            {
                "value": ShareScope.NAMED.value,
                "means": "This one named caregiver, decided one at a time.",
            },
            {
                "value": ShareScope.CIRCLE.value,
                "means": "Everyone with access to this person's care, including anyone added later.",
            },
        ],
    }


@router.post("/grant", response_model=GrantResponse)
def grant(payload: GrantCreate, service: CareSharingService = Depends(get_service)):
    """Share one category with one caregiver.

    Returns 400 when the request itself does not make sense, such as granting a
    neighbour the GP letters. Failing loudly here is the point: the alternative
    is accepting the string and quietly widening access.
    """
    try:
        row = service.grant(
            person_id=payload.person_id,
            caregiver_id=payload.caregiver_id,
            category=payload.category,
            granted_by_caregiver_id=payload.granted_by_caregiver_id,
            relationship=payload.relationship,
            scope=payload.scope,
            reason=payload.reason,
            expires_in_days=payload.expires_in_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    service.db.commit()
    return _to_response(row)


@router.post("/onboard", response_model=List[GrantResponse])
def onboard(payload: OnboardRequest, service: CareSharingService = Depends(get_service)):
    """Set up a caregiver's starting access.

    A starting point rather than a decision: family get what they need to
    coordinate care, everyone else gets the day-to-day view. Anything beyond this
    is a separate, deliberate grant.
    """
    try:
        rows = []
        for category in sorted(
            default_categories_for(payload.relationship), key=lambda c: c.value
        ):
            rows.append(
                service.grant(
                    person_id=payload.person_id,
                    caregiver_id=payload.caregiver_id,
                    category=category.value,
                    granted_by_caregiver_id=payload.granted_by_caregiver_id,
                    relationship=payload.relationship,
                    scope=ShareScope.NAMED.value,
                    reason="starting access when added to the care circle",
                )
            )
        service.db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [_to_response(r) for r in rows]


@router.post("/revoke/{grant_id}", response_model=GrantResponse)
def revoke(grant_id: str, payload: RevokeRequest, service: CareSharingService = Depends(get_service)):
    """Withdraw a grant. Immediate, total, and recorded rather than deleted."""
    try:
        row = service.revoke(
            grant_id,
            revoked_by_caregiver_id=payload.revoked_by_caregiver_id,
            reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    service.db.commit()
    return _to_response(row)


@router.get("/grants/{person_id}", response_model=List[GrantResponse])
def list_grants(
    person_id: str,
    caregiver_id: Optional[str] = None,
    service: CareSharingService = Depends(get_service),
):
    """Every grant on this person, revoked ones included.

    Included deliberately: "who could see this, and when did that stop" cannot
    be answered from active grants alone.
    """
    return [
        _to_response(r) for r in service.grants_for(person_id, caregiver_id=caregiver_id)
    ]


@router.get("/access/{person_id}")
def can_access(
    person_id: str,
    caregiver_id: str,
    category: str,
    surface: str = "manual_check",
    service: CareSharingService = Depends(get_service),
):
    """Ask the boundary directly. Useful for a UI deciding whether to show something."""
    allowed = service.can_see(person_id, caregiver_id, category, surface=surface)
    service.db.commit()
    return {"person_id": person_id, "caregiver_id": caregiver_id, "category": category, "allowed": allowed}


@router.get("/disclosure/{caregiver_id}")
def disclosure(caregiver_id: str, service: CareSharingService = Depends(get_service)):
    """What this caregiver can see, in words rather than enum values."""
    return service.disclosure_for(caregiver_id)


@router.post("/disclosure/{caregiver_id}/acknowledge")
def acknowledge(caregiver_id: str, person_id: str, service: CareSharingService = Depends(get_service)):
    row = service.acknowledge_disclosure(caregiver_id, person_id)
    service.db.commit()
    return {"id": row.id, "acknowledged_at": _iso(row.acknowledged_at)}


@router.get("/access-log/{person_id}")
def access_log(
    person_id: str,
    limit: int = 100,
    only_denied: bool = False,
    service: CareSharingService = Depends(get_service),
):
    """Who asked for what, and what the boundary said.

    Denials are included by default because they are the interesting half: a
    grant nobody exercised is not evidence that the boundary is right.
    """
    return {
        "person_id": person_id,
        "entries": service.access_log(person_id, limit=limit, only_denied=only_denied),
    }
