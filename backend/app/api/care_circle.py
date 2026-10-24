from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.care import Caregiver as CaregiverModel, Person as PersonModel
from app.models.care_circle import CareCircle, CareCircleMembership
from app.schemas.care_circle import (
    CareCircleCreate,
    CareCircleRead,
    CareCircleMemberCreate,
    CareCircleMembershipRead,
)

router = APIRouter()


ACCESS_PERMISSIONS = {
    "viewer": {
        "can_view_information": True,
        "can_contribute_information": False,
        "can_receive_notifications": True,
        "can_participate_in_decisions": False,
    },
    "contributor": {
        "can_view_information": True,
        "can_contribute_information": True,
        "can_receive_notifications": True,
        "can_participate_in_decisions": False,
    },
    "coordinator": {
        "can_view_information": True,
        "can_contribute_information": True,
        "can_receive_notifications": True,
        "can_participate_in_decisions": True,
    },
}

ALLOWED_ROLES = {
    "family_member",
    "primary_caregiver",
    "appointment_coordinator",
    "medication_manager",
    "overnight_caregiver",
    "emergency_contact",
    "other",
}


def _session_caregiver_id(request: Request) -> str:
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not caregiver_id:
        raise HTTPException(status_code=401, detail="A scoped caregiver session is required")
    return str(caregiver_id)


def _get_circle_or_404(db: Session, circle_id: str) -> CareCircle:
    circle = db.query(CareCircle).filter(CareCircle.id == circle_id).first()
    if not circle:
        raise HTTPException(status_code=404, detail="Care circle not found")
    return circle


def _require_circle_manager(db: Session, circle: CareCircle, caregiver_id: str) -> None:
    membership = (
        db.query(CareCircleMembership)
        .filter(
            CareCircleMembership.care_circle_id == circle.id,
            CareCircleMembership.person_id == circle.person_id,
            CareCircleMembership.member_caregiver_id == caregiver_id,
            CareCircleMembership.is_active.is_(True),
        )
        .first()
    )
    if not membership or membership.access_level not in {"owner", "coordinator"}:
        raise HTTPException(status_code=403, detail="Only a care-circle owner or coordinator can manage membership")


@router.get("/person/{person_id}", response_model=list[CareCircleRead])
def list_person_circles(person_id: str, db: Session = Depends(get_db)):
    if not db.query(PersonModel.id).filter(PersonModel.id == person_id).first():
        raise HTTPException(status_code=404, detail="Care record not found")
    return (
        db.query(CareCircle)
        .filter(CareCircle.person_id == person_id, CareCircle.is_active.is_(True))
        .order_by(CareCircle.created_at.desc())
        .all()
    )


@router.post("/", response_model=CareCircleRead, status_code=201)
def create_care_circle(
    payload: CareCircleCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    caregiver_id = _session_caregiver_id(request)
    person = db.query(PersonModel).filter(PersonModel.id == payload.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Care record not found")
    creator = (
        db.query(CaregiverModel)
        .filter(CaregiverModel.id == caregiver_id, CaregiverModel.person_id == payload.person_id)
        .first()
    )
    if not creator:
        raise HTTPException(status_code=403, detail="Your caregiver session is not linked to this care record")

    circle = CareCircle(
        person_id=payload.person_id,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        created_by_caregiver_id=caregiver_id,
    )
    db.add(circle)
    db.flush()
    db.add(CareCircleMembership(
        care_circle_id=circle.id,
        person_id=payload.person_id,
        member_caregiver_id=caregiver_id,
        role="primary_caregiver",
        role_description="Care-circle owner",
        access_level="owner",
        can_view_information=True,
        can_contribute_information=True,
        can_receive_notifications=True,
        can_participate_in_decisions=True,
        relationship_to_person=creator.relationship,
        invitation_status="active",
        is_active=True,
    ))
    db.commit()
    db.refresh(circle)
    return circle


@router.get("/{circle_id}/members", response_model=list[CareCircleMembershipRead])
def list_circle_members(circle_id: str, request: Request, db: Session = Depends(get_db)):
    circle = _get_circle_or_404(db, circle_id)
    caregiver_id = _session_caregiver_id(request)
    # Membership lists are private to active members of the same circle.
    membership = (
        db.query(CareCircleMembership.id)
        .filter(
            CareCircleMembership.care_circle_id == circle.id,
            CareCircleMembership.member_caregiver_id == caregiver_id,
            CareCircleMembership.is_active.is_(True),
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=403, detail="You are not an active member of this care circle")
    return (
        db.query(CareCircleMembership)
        .filter(CareCircleMembership.care_circle_id == circle.id, CareCircleMembership.is_active.is_(True))
        .order_by(CareCircleMembership.created_at.asc())
        .all()
    )


@router.post("/{circle_id}/members", response_model=CareCircleMembershipRead, status_code=201)
def add_circle_member(
    circle_id: str,
    payload: CareCircleMemberCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    circle = _get_circle_or_404(db, circle_id)
    actor_id = _session_caregiver_id(request)
    _require_circle_manager(db, circle, actor_id)

    role = payload.role.strip().lower()
    access_level = payload.access_level.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail="Unsupported care-circle role")
    if access_level not in ACCESS_PERMISSIONS:
        raise HTTPException(status_code=422, detail="Access level must be viewer, contributor, or coordinator")

    caregiver = (
        db.query(CaregiverModel)
        .filter(
            CaregiverModel.id == payload.member_caregiver_id,
            CaregiverModel.person_id == circle.person_id,
        )
        .first()
    )
    if not caregiver:
        raise HTTPException(status_code=422, detail="Member must already be linked to this care record")

    existing = (
        db.query(CareCircleMembership)
        .filter(
            CareCircleMembership.care_circle_id == circle.id,
            CareCircleMembership.member_caregiver_id == caregiver.id,
        )
        .first()
    )
    if existing and existing.is_active:
        raise HTTPException(status_code=409, detail="This caregiver is already a member of the circle")
    if existing:
        existing.is_active = True
        existing.left_at = None
        existing.role = role
        existing.role_description = payload.role_description
        existing.access_level = access_level
        existing.relationship_to_person = payload.relationship_to_person or caregiver.relationship
        existing.invitation_status = "active"
        for key, value in ACCESS_PERMISSIONS[access_level].items():
            setattr(existing, key, value)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    membership = CareCircleMembership(
        care_circle_id=circle.id,
        person_id=circle.person_id,
        member_caregiver_id=caregiver.id,
        role=role,
        role_description=payload.role_description,
        access_level=access_level,
        relationship_to_person=payload.relationship_to_person or caregiver.relationship,
        invitation_status="active",
        is_active=True,
        **ACCESS_PERMISSIONS[access_level],
    )
    db.add(membership)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Care-circle membership already exists") from exc
    db.refresh(membership)
    return membership
