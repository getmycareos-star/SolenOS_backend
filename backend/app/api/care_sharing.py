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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    AuthenticatedPrincipal,
    get_current_principal,
    require_authenticated,
    require_care_context_access,
)
from app.models.care_sharing import CareShareGrant as CareShareGrantModel
from app.models.caregiver import Caregiver as CaregiverModel
from app.core.sharing_enums import (
    SHAREABLE_CATEGORIES,
    ShareCategory,
    ShareRelationship,
    ShareScope,
)
from app.services.care_sharing import CareSharingService, default_categories_for

async def authorize_care_sharing_request(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    """Require authentication and care-context access before sharing operations."""
    require_authenticated(principal)
    # Category descriptions contain no family data or grant state.
    if request.url.path.rstrip("/").endswith("/care-sharing/categories"):
        return

    payload = {}
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}

    def find_context(value):
        if isinstance(value, dict):
            if value.get("person_id"):
                return str(value["person_id"])
            for nested in value.values():
                found = find_context(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = find_context(nested)
                if found:
                    return found
        return None

    person_id = (
        request.path_params.get("person_id")
        or request.query_params.get("person_id")
        or find_context(payload)
    )
    grant_id = request.path_params.get("grant_id")
    if not person_id and grant_id:
        grant = db.query(CareShareGrantModel).filter(
            CareShareGrantModel.id == grant_id
        ).first()
        person_id = getattr(grant, "person_id", None) if grant else None

    caregiver_id = request.path_params.get("caregiver_id")
    if not person_id and caregiver_id:
        caregiver = db.query(CaregiverModel).filter(
            CaregiverModel.id == caregiver_id
        ).first()
        person_id = getattr(caregiver, "person_id", None) if caregiver else None

    if not person_id:
        raise HTTPException(
            status_code=400,
            detail="A care context is required for care-sharing access.",
        )
    require_care_context_access(principal, str(person_id), "care sharing")


router = APIRouter(
    prefix="/care-sharing",
    tags=["care-sharing"],
    dependencies=[Depends(authorize_care_sharing_request)],
)


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


def authenticated_caregiver_id(
    db: Session,
    principal: AuthenticatedPrincipal,
    person_id: str,
) -> str:
    """Resolve the acting caregiver from the authenticated account, never request data."""
    require_authenticated(principal)
    caregiver = db.query(CaregiverModel).filter(
        CaregiverModel.person_id == person_id,
        CaregiverModel.email == principal.account.email,
        CaregiverModel.is_active.is_(True),
    ).first()
    if caregiver is None:
        raise HTTPException(
            status_code=403,
            detail="The authenticated account is not an active caregiver in this care context.",
        )
    return caregiver.id


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
def grant(
    payload: GrantCreate,
    service: CareSharingService = Depends(get_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
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
            granted_by_caregiver_id=authenticated_caregiver_id(service.db, principal, payload.person_id),
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
def onboard(
    payload: OnboardRequest,
    service: CareSharingService = Depends(get_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
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
                    granted_by_caregiver_id=authenticated_caregiver_id(service.db, principal, payload.person_id),
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
def revoke(
    grant_id: str,
    payload: RevokeRequest,
    service: CareSharingService = Depends(get_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    """Withdraw a grant. The revoking identity comes from the signed-in account."""
    grant_row = service.db.query(CareShareGrantModel).filter(
        CareShareGrantModel.id == grant_id
    ).first()
    if grant_row is None:
        raise HTTPException(status_code=404, detail="no such grant")
    actor_id = authenticated_caregiver_id(service.db, principal, grant_row.person_id)
    try:
        row = service.revoke(
            grant_id,
            revoked_by_caregiver_id=actor_id,
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
def disclosure(
    caregiver_id: str,
    service: CareSharingService = Depends(get_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    """Show the signed-in caregiver their own disclosure, not another person's."""
    caregiver = service.db.query(CaregiverModel).filter(
        CaregiverModel.id == caregiver_id
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=404, detail="caregiver not found")
    if caregiver.email != principal.account.email:
        raise HTTPException(status_code=403, detail="A caregiver may view only their own disclosure")
    return service.disclosure_for(caregiver_id)


@router.post("/disclosure/{caregiver_id}/acknowledge")
def acknowledge(
    caregiver_id: str,
    person_id: str,
    service: CareSharingService = Depends(get_service),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    authenticated_caregiver_id(service.db, principal, person_id)
    caregiver = service.db.query(CaregiverModel).filter(
        CaregiverModel.id == caregiver_id,
        CaregiverModel.person_id == person_id,
        CaregiverModel.email == principal.account.email,
    ).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="A caregiver may acknowledge only their own disclosure")
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
