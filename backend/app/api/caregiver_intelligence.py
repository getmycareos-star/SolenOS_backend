from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import hashlib
from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.privacy_delivery import AuditLogEvent
from app.models.care import Caregiver as SessionCaregiverModel, Person as SessionPersonModel
from app.services.caregiver_profile_identity import ensure_caregiver_profile_for_session
from app.services.caregiver_permissions import require_caregiver_permission
from app.models.caregiver_intelligence import (
    CaregiverProfile as CaregiverProfileModel,
    CaregiverContext as CaregiverContextModel,
    CaregiverRole as CaregiverRoleModel,
    CaregiverResponsibility as CaregiverResponsibilityModel,
    CaregiverObservation as CaregiverObservationModel,
    CaregiverPerspective as CaregiverPerspectiveModel,
    CaregiverHandoff as CaregiverHandoffModel,
    CaregiverActivity as CaregiverActivityModel,
    CaregiverCommunication as CaregiverCommunicationModel,
    CareNetworkRelationship as CareNetworkRelationshipModel,
    ResponsibilityGap as ResponsibilityGapModel,
    PerspectiveConflict as PerspectiveConflictModel,
    DuplicateAction as DuplicateActionModel,
)
from app.schemas.caregiver_intelligence import (
    CaregiverProfileCreate,
    CaregiverProfile,
    CaregiverRoleCreate,
    CaregiverRole,
    CaregiverResponsibilityCreate,
    CaregiverResponsibility,
    CaregiverObservationCreate,
    CaregiverObservation,
    CaregiverPerspectiveCreate,
    CaregiverPerspective,
    CaregiverHandoffCreate,
    CaregiverHandoff,
    CaregiverActivityCreate,
    CaregiverActivity,
    CaregiverCommunicationCreate,
    CaregiverCommunication,
    CareNetworkRelationshipCreate,
    CareNetworkRelationship,
    ResponsibilityGapCreate,
    ResponsibilityGap,
    PerspectiveConflictCreate,
    PerspectiveConflict,
    DuplicateActionCreate,
    DuplicateAction,
    CaregiverContextCreate,
    CaregiverContext,
    CaregiverSummary,
    MultiCaregiverSummary,
    ResponsibilityGapReview,
    PerspectiveConflictReview,
    DuplicateActionReview,
)
from app.services.caregiver_intelligence import CaregiverIntelligenceService
from app.core.caregiver_enums import (
    CaregiverInvolvementStatus,
    HandoffAcceptanceStatus,
    CaregiverActivityType,
    InformationVisibility,
    IdentityMatchConfidence,
    ResponsibilityStatus,
    CoverageGapStatus,
    PerspectiveConflictStatus,
    DuplicateActionStatus,
)

router = APIRouter()


class CareRecipientProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    relationship: str = Field(default="", max_length=120)
    condition: Optional[str] = Field(default=None, max_length=500)


def _care_recipient_profile_json(
    db: Session,
    *,
    person: SessionPersonModel,
    caregiver_profile: CaregiverProfileModel,
) -> dict:
    contexts = (
        db.query(CaregiverContextModel)
        .filter(
            CaregiverContextModel.person_id == str(person.id),
            CaregiverContextModel.caregiver_id == str(caregiver_profile.id),
        )
        .order_by(CaregiverContextModel.effective_start.desc(), CaregiverContextModel.created_at.desc())
        .all()
    )
    profile_snapshot = None
    for context in contexts:
        metadata = context.context_metadata if isinstance(context.context_metadata, dict) else {}
        if metadata.get("record_type") == "care_recipient_profile":
            profile_snapshot = metadata
            break
    condition = profile_snapshot.get("condition") if profile_snapshot else None
    return {
        "name": profile_snapshot.get("recipient_name") if profile_snapshot and isinstance(profile_snapshot.get("recipient_name"), str) else person.name,
        "relationship": profile_snapshot.get("caregiver_relationship") if profile_snapshot and isinstance(profile_snapshot.get("caregiver_relationship"), str) else (caregiver_profile.relationship or ""),
        "condition": condition if isinstance(condition, str) and condition.strip() else None,
    }


def _resolve_scoped_session(request: Request, db: Session) -> tuple[str, str]:
    """Resolve the server-issued bearer session at the route boundary.

    Middleware state is a useful fast path, but this API must remain correct
    when an ASGI middleware layer does not propagate request.state as expected.
    The bearer token is revalidated against the same database session used by
    the endpoint; caller-supplied person/caregiver IDs are never credentials.
    """
    person_id = getattr(request.state, "scoped_person_id", None)
    session_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and session_caregiver_id:
        require_caregiver_permission(db, str(person_id), str(session_caregiver_id), "view_lcr")
        return str(person_id), str(session_caregiver_id)

    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid care-record session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="A valid care-record session is required")

    person_id = str(session.person_id)
    session_caregiver_id = str(session.caregiver_id)
    request.state.anonymous_session_id = session.id
    request.state.scoped_person_id = person_id
    request.state.scoped_caregiver_id = session_caregiver_id
    require_caregiver_permission(db, person_id, session_caregiver_id, "view_lcr")
    return person_id, session_caregiver_id


def _session_profile(request: Request, db: Session) -> tuple[str, str, CaregiverProfileModel]:
    person_id, session_caregiver_id = _resolve_scoped_session(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "view_lcr")
    try:
        profile = ensure_caregiver_profile_for_session(
            db,
            person_id=str(person_id),
            session_caregiver_id=str(session_caregiver_id),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail="Caregiver identity is not linked to this care record") from exc
    request.state.scoped_caregiver_profile_id = profile.id
    return str(person_id), str(session_caregiver_id), profile


def _assert_person_scope(request: Request, db: Session, person_id: str) -> tuple[str, str]:
    scoped_person_id, scoped_caregiver_id = _resolve_scoped_session(request, db)
    require_caregiver_permission(db, scoped_person_id, scoped_caregiver_id, "view_lcr")
    if str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return str(scoped_person_id), str(scoped_caregiver_id)


def _require_profile_in_person(db: Session, profile_id: str, person_id: str) -> CaregiverProfileModel:
    profile = db.query(CaregiverProfileModel).filter(
        CaregiverProfileModel.id == str(profile_id),
        CaregiverProfileModel.person_id == str(person_id),
    ).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Caregiver profile not found")
    return profile


def _resolve_profile_target(db: Session, target_id: str, person_id: str) -> CaregiverProfileModel:
    profile = db.query(CaregiverProfileModel).filter(
        CaregiverProfileModel.id == str(target_id),
        CaregiverProfileModel.person_id == str(person_id),
    ).first()
    if profile is not None:
        return profile
    session_caregiver = db.query(SessionCaregiverModel).filter(
        SessionCaregiverModel.id == str(target_id),
        SessionCaregiverModel.person_id == str(person_id),
    ).first()
    if session_caregiver is not None:
        try:
            profile = ensure_caregiver_profile_for_session(
                db,
                person_id=str(person_id),
                session_caregiver_id=str(session_caregiver.id),
            )
            db.commit()
            return profile
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=403, detail="Caregiver identity is not linked to this care record") from exc
    raise HTTPException(status_code=404, detail="Caregiver profile not found")


def _assert_actor_id(claimed_id: Optional[str], profile_id: str, session_caregiver_id: str) -> None:
    if claimed_id and str(claimed_id) not in {str(profile_id), str(session_caregiver_id)}:
        raise HTTPException(status_code=403, detail="Caregiver attribution does not match this session")


def _log_and_learn(db: Session, person_id: str, event_type: str, detail: str, source_type: str, source_id: str = None, caregiver_id: str = None):
    from app.services.learning import LearningEngine, SavePipelineEvent
    engine = LearningEngine(db)
    engine.log_pipeline_event(
        SavePipelineEvent(step=f"{event_type}_saved", person_id=person_id, detail=detail)
    )
    engine.record_learning_event(
        person_id, event_type, detail, source_type, source_id, caregiver_id
    )
    engine.after_save(person_id)


@router.post("/caregiver-profiles", response_model=CaregiverProfile)
def create_caregiver_profile(profile: CaregiverProfileCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id = _assert_person_scope(request, db, profile.person_id)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    # Names/emails are user-supplied profile data, never proof of identity.
    # Link only the first profile created by this authenticated session to its
    # server-resolved caregiver ID. Additional profiles remain unlinked until
    # their own authenticated session establishes that relationship.
    profile = CaregiverProfileCreate(**{
        **profile.model_dump(),
        "person_id": person_id,
        "identity_match_confidence": IdentityMatchConfidence.UNKNOWN,
        "is_primary_designated": False,
        "primary_designation_source": None,
    })
    service = CaregiverIntelligenceService(db)
    db_profile = service.create_caregiver_profile(profile)
    linked_profile = db.query(CaregiverProfileModel.id).filter(
        CaregiverProfileModel.session_caregiver_id == str(session_caregiver_id)
    ).first()
    if linked_profile is None:
        db_profile.session_caregiver_id = str(session_caregiver_id)
        db.commit()
        db.refresh(db_profile)
    _log_and_learn(
        db,
        db_profile.person_id,
        "caregiver_profile_created",
        f"Created caregiver profile for {db_profile.name}",
        "caregiver_profile",
        source_id=db_profile.id,
        caregiver_id=session_caregiver_id,
    )
    return db_profile


@router.get("/caregiver-profiles/current", response_model=CaregiverProfile)
def get_current_caregiver_profile(request: Request, db: Session = Depends(get_db)):
    _person_id, _session_caregiver_id, profile = _session_profile(request, db)
    return profile


@router.get("/care-profile")
def get_care_recipient_profile(request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, caregiver_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, _session_caregiver_id, "view_lcr")
    person = db.query(SessionPersonModel).filter(SessionPersonModel.id == person_id).first()
    if person is None:
        raise HTTPException(status_code=404, detail="Care record not found")
    return _care_recipient_profile_json(db, person=person, caregiver_profile=caregiver_profile)


@router.put("/care-profile")
def update_care_recipient_profile(
    payload: CareRecipientProfileUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, session_caregiver_id, caregiver_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "add_evidence")
    person = (
        db.query(SessionPersonModel)
        .filter(SessionPersonModel.id == person_id)
        .with_for_update()
        .first()
    )
    caregiver_profile = (
        db.query(CaregiverProfileModel)
        .filter(
            CaregiverProfileModel.id == caregiver_profile.id,
            CaregiverProfileModel.person_id == person_id,
            CaregiverProfileModel.session_caregiver_id == session_caregiver_id,
        )
        .with_for_update()
        .first()
    )
    if person is None or caregiver_profile is None:
        raise HTTPException(status_code=404, detail="Care record profile not found")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Enter a care recipient name.")
    relationship = payload.relationship.strip()
    condition = payload.condition.strip() if payload.condition and payload.condition.strip() else None

    try:
        person.name = name
        caregiver_profile.relationship = relationship

        contexts = (
            db.query(CaregiverContextModel)
            .filter(
                CaregiverContextModel.person_id == person_id,
                CaregiverContextModel.caregiver_id == caregiver_profile.id,
            )
            .order_by(CaregiverContextModel.effective_start.desc(), CaregiverContextModel.created_at.desc())
            .with_for_update()
            .all()
        )
        latest_profile_context = None
        for context in contexts:
            metadata = context.context_metadata if isinstance(context.context_metadata, dict) else {}
            if metadata.get("record_type") == "care_recipient_profile":
                latest_profile_context = context
                break
        previous_snapshot = (
            latest_profile_context.context_metadata
            if latest_profile_context is not None and isinstance(latest_profile_context.context_metadata, dict)
            else {}
        )
        next_snapshot = {
            "record_type": "care_recipient_profile",
            "recipient_name": name,
            "caregiver_relationship": relationship,
            "condition": condition,
            "epistemic_status": "caregiver_reported",
            "reported_by_session_caregiver_id": session_caregiver_id,
        }
        snapshot_fields = ("recipient_name", "caregiver_relationship", "condition")
        if any(previous_snapshot.get(field) != next_snapshot.get(field) for field in snapshot_fields):
            now = datetime.now(timezone.utc)
            if latest_profile_context is not None and latest_profile_context.effective_end is None:
                latest_profile_context.effective_end = now
            db.add(CaregiverContextModel(
                person_id=person_id,
                caregiver_id=caregiver_profile.id,
                proximity_category=None,
                observation_window_start=None,
                observation_window_end=None,
                availability_notes=None,
                context_metadata=next_snapshot,
                effective_start=now,
                effective_end=None,
            ))
        db.commit()
        db.refresh(person)
        db.refresh(caregiver_profile)
        return _care_recipient_profile_json(db, person=person, caregiver_profile=caregiver_profile)
    except Exception:
        db.rollback()
        raise


@router.get("/caregiver-profiles/{person_id}", response_model=List[CaregiverProfile])
def list_caregiver_profiles(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.list_caregiver_profiles(person_id)


@router.get("/caregiver-profiles/detail/{caregiver_id}", response_model=CaregiverProfile)
def get_caregiver_profile(caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    profile = service.get_caregiver_profile(caregiver_id)
    if not profile or str(profile.person_id) != person_id:
        raise HTTPException(status_code=404, detail="Caregiver profile not found")
    return profile


@router.patch("/caregiver-profiles/{caregiver_id}/involvement")
def update_caregiver_involvement(caregiver_id: str, status: str, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    if str(caregiver_id) != str(current_profile.id):
        raise HTTPException(status_code=403, detail="Only the linked caregiver can update their own involvement status")
    service = CaregiverIntelligenceService(db)
    try:
        involvement_status = CaregiverInvolvementStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid involvement status: {status}")
    profile = service.update_caregiver_involvement(caregiver_id, involvement_status)
    if not profile:
        raise HTTPException(status_code=404, detail="Caregiver profile not found")
    return {"id": profile.id, "involvement_status": profile.involvement_status.value}


@router.post("/caregiver-roles", response_model=CaregiverRole)
def create_caregiver_role(role: CaregiverRoleCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    _assert_person_scope(request, db, role.person_id)
    _assert_actor_id(role.caregiver_id, current_profile.id, session_caregiver_id)
    _require_profile_in_person(db, role.caregiver_id if role.caregiver_id != session_caregiver_id else current_profile.id, person_id)
    role = CaregiverRoleCreate(**{**role.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id})
    service = CaregiverIntelligenceService(db)
    db_role = service.create_caregiver_role(role)
    _log_and_learn(
        db,
        person_id,
        "caregiver_role_assigned",
        f"Care role assigned: {db_role.role_type.value}",
        "caregiver_role",
        db_role.id,
        session_caregiver_id,
    )
    return db_role


@router.get("/caregiver-roles/{person_id}", response_model=List[CaregiverRole])
def list_caregiver_roles(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    if caregiver_id:
        return service.get_current_roles(caregiver_id, person_id)
    return (
        db.query(CaregiverRoleModel)
        .filter(CaregiverRoleModel.person_id == person_id, CaregiverRoleModel.is_current)
        .all()
    )


@router.patch("/caregiver-roles/{role_id}/end", response_model=CaregiverRole)
def end_caregiver_role(role_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    role = db.query(CaregiverRoleModel).filter(
        CaregiverRoleModel.id == str(role_id),
        CaregiverRoleModel.person_id == person_id,
        CaregiverRoleModel.caregiver_id == current_profile.id,
    ).with_for_update().first()
    if role is None:
        raise HTTPException(status_code=404, detail="Care role not found")
    if not role.is_current:
        return role
    role.is_current = False
    role.effective_end = datetime.now(timezone.utc)
    db.add(role)
    _log_and_learn(
        db,
        person_id,
        "caregiver_role_ended",
        f"Care role ended: {role.role_type.value}",
        "caregiver_role",
        role.id,
        session_caregiver_id,
    )
    db.commit()
    db.refresh(role)
    return role


@router.post("/caregiver-responsibilities", response_model=CaregiverResponsibility)
def create_responsibility(responsibility: CaregiverResponsibilityCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    _assert_person_scope(request, db, responsibility.person_id)
    _assert_actor_id(responsibility.caregiver_id, current_profile.id, session_caregiver_id)
    _require_profile_in_person(db, responsibility.caregiver_id if responsibility.caregiver_id != session_caregiver_id else current_profile.id, person_id)
    responsibility = CaregiverResponsibilityCreate(**{**responsibility.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id})
    service = CaregiverIntelligenceService(db)
    db_resp = service.assign_responsibility(responsibility)
    _log_and_learn(
        db,
        db_resp.person_id,
        "responsibility_assigned",
        f"Assigned {db_resp.responsibility_type} to caregiver {db_resp.caregiver_id}",
        "responsibility",
        source_id=db_resp.id,
        caregiver_id=session_caregiver_id,
    )
    return db_resp


@router.get("/caregiver-responsibilities/{person_id}", response_model=List[CaregiverResponsibility])
def list_responsibilities(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_active_responsibilities(person_id, caregiver_id)


@router.patch("/caregiver-responsibilities/{responsibility_id}/end", response_model=CaregiverResponsibility)
def end_responsibility(responsibility_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    responsibility = db.query(CaregiverResponsibilityModel).filter(
        CaregiverResponsibilityModel.id == str(responsibility_id),
        CaregiverResponsibilityModel.person_id == person_id,
        CaregiverResponsibilityModel.caregiver_id == current_profile.id,
    ).with_for_update().first()
    if responsibility is None:
        raise HTTPException(status_code=404, detail="Care responsibility not found")
    if getattr(responsibility.status, "value", responsibility.status) in {"historical", "gap"}:
        return responsibility
    responsibility.status = ResponsibilityStatus.HISTORICAL
    responsibility.effective_end = datetime.now(timezone.utc)
    db.add(responsibility)
    _log_and_learn(
        db,
        person_id,
        "responsibility_ended",
        f"Ended responsibility: {responsibility.responsibility_type}",
        "responsibility",
        responsibility.id,
        session_caregiver_id,
    )
    db.commit()
    db.refresh(responsibility)
    return responsibility


@router.get("/caregiver-responsibilities/history/{caregiver_id}", response_model=List[CaregiverResponsibility])
def get_responsibility_history(caregiver_id: str, person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    _resolve_profile_target(db, caregiver_id, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_responsibility_history(person_id, caregiver_id)


@router.post("/caregiver-observations", response_model=CaregiverObservation)
def create_observation(observation: CaregiverObservationCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "add_evidence")
    _assert_person_scope(request, db, observation.person_id)
    _assert_actor_id(observation.caregiver_id, current_profile.id, session_caregiver_id)
    if observation.is_promoted_to_shared or observation.promoted_by_caregiver_id or observation.promoted_at:
        raise HTTPException(status_code=422, detail="Observations must be explicitly promoted after creation")
    from app.core.caregiver_enums import InformationVisibility
    if observation.information_visibility != InformationVisibility.PRIVATE:
        raise HTTPException(status_code=422, detail="New observations must start private and be explicitly shared")
    observation = CaregiverObservationCreate(**{**observation.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id, "information_visibility": InformationVisibility.PRIVATE, "is_promoted_to_shared": False, "promoted_by_caregiver_id": None, "promoted_at": None})
    service = CaregiverIntelligenceService(db)
    db_obs = service.record_observation(observation)
    _log_and_learn(
        db,
        db_obs.person_id,
        "caregiver_observation_recorded",
        f"Recorded observation by caregiver {db_obs.caregiver_id}",
        "caregiver_observation",
        source_id=db_obs.id,
        caregiver_id=session_caregiver_id,
    )
    return db_obs


@router.get("/caregiver-observations/{person_id}", response_model=List[CaregiverObservation])
def list_observations(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    # Private observations are visible only to their author. A stale/local
    # caregiver query key must never select another caregiver's private rows.
    if caregiver_id and str(caregiver_id) in {str(current_profile.id), str(session_caregiver_id)}:
        return service.get_observations_for_caregiver(scoped_person_id, current_profile.id)
    return service.get_shared_observations(scoped_person_id)


@router.get("/caregiver-observations/{person_id}/caregiver/{caregiver_id}", response_model=List[CaregiverObservation])
def list_caregiver_observations(person_id: str, caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    if str(caregiver_id) not in {str(current_profile.id), str(session_caregiver_id)}:
        raise HTTPException(status_code=403, detail="Private observations are only available to their caregiver")
    service = CaregiverIntelligenceService(db)
    return service.get_observations_for_caregiver(scoped_person_id, current_profile.id)


@router.post("/caregiver-observations/{observation_id}/promote")
def promote_observation(observation_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "share_observations")
    _assert_actor_id(caregiver_id, current_profile.id, session_caregiver_id)
    service = CaregiverIntelligenceService(db)
    observation = service.promote_observation_to_shared(observation_id, current_profile.id, person_id=person_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"id": observation.id, "is_promoted_to_shared": observation.is_promoted_to_shared, "visibility": observation.information_visibility.value}


@router.post("/caregiver-perspectives", response_model=CaregiverPerspective)
def create_perspective(perspective: CaregiverPerspectiveCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "add_evidence")
    _assert_person_scope(request, db, perspective.person_id)
    _assert_actor_id(perspective.caregiver_id, current_profile.id, session_caregiver_id)
    if perspective.is_promoted_to_shared or perspective.promoted_by_caregiver_id or perspective.promoted_at:
        raise HTTPException(status_code=422, detail="Perspectives must be explicitly promoted after creation")
    if perspective.information_visibility != InformationVisibility.PRIVATE:
        raise HTTPException(status_code=422, detail="New perspectives must start private and be explicitly shared")
    perspective = CaregiverPerspectiveCreate(**{**perspective.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id, "information_visibility": InformationVisibility.PRIVATE, "is_promoted_to_shared": False, "promoted_by_caregiver_id": None, "promoted_at": None})
    service = CaregiverIntelligenceService(db)
    db_persp = service.record_perspective(perspective)
    return db_persp


@router.get("/caregiver-perspectives/{person_id}", response_model=List[CaregiverPerspective])
def list_perspectives(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    if caregiver_id and str(caregiver_id) in {str(current_profile.id), str(session_caregiver_id)}:
        return service.get_perspectives_for_caregiver(scoped_person_id, current_profile.id)
    return (
        db.query(CaregiverPerspectiveModel)
        .filter(CaregiverPerspectiveModel.person_id == scoped_person_id, CaregiverPerspectiveModel.is_promoted_to_shared)
        .order_by(CaregiverPerspectiveModel.timestamp.desc())
        .all()
    )


@router.post("/caregiver-perspectives/{perspective_id}/promote")
def promote_perspective(perspective_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "share_observations")
    _assert_actor_id(caregiver_id, current_profile.id, session_caregiver_id)
    service = CaregiverIntelligenceService(db)
    perspective = service.promote_perspective_to_shared(perspective_id, current_profile.id, person_id=person_id)
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {"id": perspective.id, "is_promoted_to_shared": perspective.is_promoted_to_shared, "visibility": perspective.information_visibility.value}


@router.post("/caregiver-handoffs", response_model=CaregiverHandoff)
def create_handoff(handoff: CaregiverHandoffCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_handoffs")
    _assert_person_scope(request, db, handoff.person_id)
    _assert_actor_id(handoff.from_caregiver_id, current_profile.id, session_caregiver_id)
    recipient = _resolve_profile_target(db, handoff.to_caregiver_id, person_id)
    handoff = CaregiverHandoffCreate(**{**handoff.model_dump(), "person_id": person_id, "from_caregiver_id": current_profile.id, "to_caregiver_id": recipient.id})
    service = CaregiverIntelligenceService(db)
    db_handoff = service.record_handoff(handoff)
    _log_and_learn(
        db,
        db_handoff.person_id,
        "caregiver_handoff_recorded",
        f"Handoff from {db_handoff.from_caregiver_id} to {db_handoff.to_caregiver_id} for {db_handoff.responsibility_type}",
        "handoff",
        source_id=db_handoff.id,
        caregiver_id=session_caregiver_id,
    )
    return db_handoff


@router.post("/caregiver-handoffs/{handoff_id}/accept")
def accept_handoff(handoff_id: str, request: Request, acceptance_evidence: Optional[str] = None, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_handoffs")
    service = CaregiverIntelligenceService(db)
    handoff = service.accept_handoff(handoff_id, acceptance_evidence, person_id=person_id, accepting_caregiver_id=current_profile.id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    return {"id": handoff.id, "acceptance_status": handoff.acceptance_status.value, "is_completed": handoff.is_completed}


@router.get("/caregiver-handoffs/{person_id}", response_model=List[CaregiverHandoff])
def list_handoffs(person_id: str, request: Request, caregiver_id: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    # The inbox is always scoped to the authenticated recipient, never a caller-selected ID.
    handoffs = service.get_pending_handoffs(scoped_person_id, current_profile.id)
    if status:
        try:
            status_enum = HandoffAcceptanceStatus(status)
            handoffs = [h for h in handoffs if h.acceptance_status == status_enum]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid handoff status: {status}")
    return handoffs


@router.post("/caregiver-activities", response_model=CaregiverActivity)
def create_activity(activity: CaregiverActivityCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "add_evidence")
    _assert_person_scope(request, db, activity.person_id)
    _assert_actor_id(activity.caregiver_id, current_profile.id, session_caregiver_id)
    if activity.is_anonymous:
        raise HTTPException(status_code=422, detail="Anonymous activity must use the dedicated unknown-actor workflow")
    activity = CaregiverActivityCreate(**{**activity.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id, "is_anonymous": False, "shared_account_id": None})
    service = CaregiverIntelligenceService(db)
    db_activity = service.record_activity(activity)
    return db_activity


@router.get("/caregiver-activities/{person_id}", response_model=List[CaregiverActivity])
def list_activities(person_id: str, request: Request, caregiver_id: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    return service.get_activity_history(scoped_person_id, current_profile.id, min(max(limit, 1), 100))


@router.post("/caregiver-communications", response_model=CaregiverCommunication)
def create_communication(communication: CaregiverCommunicationCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "view_communications")
    _assert_person_scope(request, db, communication.person_id)
    _assert_actor_id(communication.from_caregiver_id, current_profile.id, session_caregiver_id)
    target_id = communication.to_caregiver_id
    if target_id:
        target_id = _resolve_profile_target(db, target_id, person_id).id
    recipients = []
    for recipient_id in communication.recipient_caregiver_ids or []:
        recipients.append(_resolve_profile_target(db, recipient_id, person_id).id)
    communication = CaregiverCommunicationCreate(**{**communication.model_dump(), "person_id": person_id, "from_caregiver_id": current_profile.id, "to_caregiver_id": target_id, "recipient_caregiver_ids": recipients})
    service = CaregiverIntelligenceService(db)
    db_comm = service.record_communication(communication)
    _log_and_learn(
        db,
        db_comm.person_id,
        "caregiver_communication_recorded",
        f"Communication from {db_comm.from_caregiver_id} to {db_comm.to_caregiver_id or 'group'}",
        "communication",
        source_id=db_comm.id,
        caregiver_id=session_caregiver_id,
    )
    return db_comm


@router.get("/caregiver-communications", response_model=List[CaregiverCommunication])
def list_scoped_communications(request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, _session_caregiver_id, "view_communications")
    communications = (
        db.query(CaregiverCommunicationModel)
        .filter(CaregiverCommunicationModel.person_id == person_id)
        .order_by(CaregiverCommunicationModel.communication_time.desc())
        .all()
    )
    visible = []
    for item in communications:
        recipients = set(item.recipient_caregiver_ids or [])
        is_party = (
            str(item.from_caregiver_id) == str(current_profile.id)
            or str(item.to_caregiver_id or "") == str(current_profile.id)
            or str(current_profile.id) in {str(value) for value in recipients}
        )
        visibility = getattr(item.visibility, "value", item.visibility)
        is_restricted = bool(item.is_private) or str(visibility).lower() in {"private", "restricted"}
        if is_restricted and not is_party:
            continue
        visible.append(item)
    return visible


@router.get("/caregiver-communications/{person_id}", response_model=List[CaregiverCommunication])
def list_communications(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    communications = (
        db.query(CaregiverCommunicationModel)
        .filter(CaregiverCommunicationModel.person_id == scoped_person_id)
        .order_by(CaregiverCommunicationModel.communication_time.desc())
        .all()
    )
    visible = []
    for item in communications:
        recipients = set(item.recipient_caregiver_ids or [])
        is_party = (
            str(item.from_caregiver_id) == str(current_profile.id)
            or str(item.to_caregiver_id or "") == str(current_profile.id)
            or str(current_profile.id) in {str(value) for value in recipients}
        )
        visibility = getattr(item.visibility, "value", item.visibility)
        is_restricted = bool(item.is_private) or str(visibility).lower() in {"private", "restricted"}
        if is_restricted and not is_party:
            continue
        visible.append(item)
    return visible


@router.post("/care-network-relationships", response_model=CareNetworkRelationship)
def create_network_relationship(relationship: CareNetworkRelationshipCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    _assert_person_scope(request, db, relationship.person_id)
    _assert_actor_id(relationship.from_caregiver_id, current_profile.id, session_caregiver_id)
    target_id = _resolve_profile_target(db, relationship.to_caregiver_id, person_id).id if relationship.to_caregiver_id else None
    relationship = CareNetworkRelationshipCreate(**{**relationship.model_dump(), "person_id": person_id, "from_caregiver_id": current_profile.id, "to_caregiver_id": target_id})
    service = CaregiverIntelligenceService(db)
    db_rel = service.create_care_network_relationship(relationship)
    return db_rel


@router.get("/care-network-relationships/{person_id}", response_model=List[CareNetworkRelationship])
def list_network_relationships(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_care_network(person_id)


@router.post("/caregiver-contexts", response_model=CaregiverContext)
def create_context(context: CaregiverContextCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "add_evidence")
    _assert_person_scope(request, db, context.person_id)
    _assert_actor_id(context.caregiver_id, current_profile.id, session_caregiver_id)
    context = CaregiverContextCreate(**{**context.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id})
    service = CaregiverIntelligenceService(db)
    db_context = service.create_context(context)
    return db_context


@router.get("/caregiver-contexts", response_model=List[CaregiverContext])
def list_scoped_contexts(request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id = _resolve_scoped_session(request, db)
    service = CaregiverIntelligenceService(db)
    return service.get_contexts(person_id)


@router.get("/caregiver-contexts/{person_id}", response_model=List[CaregiverContext])
def list_contexts(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_contexts(person_id)


@router.post("/responsibility-gaps", response_model=ResponsibilityGap)
def create_responsibility_gap(gap: ResponsibilityGapCreate, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, _session_caregiver_id, "manage_responsibilities")
    _assert_person_scope(request, db, gap.person_id)
    if gap.status != CoverageGapStatus.OPEN or gap.resolution or gap.resolved_at:
        raise HTTPException(
            status_code=422,
            detail="A new responsibility gap must start open; review and resolution require a separate decision.",
        )
    if gap.last_known_caregiver_id:
        _require_profile_in_person(db, gap.last_known_caregiver_id, person_id)
    gap = ResponsibilityGapCreate(**{
        **gap.model_dump(),
        "person_id": person_id,
        "status": CoverageGapStatus.OPEN,
        "resolution": None,
        "resolved_at": None,
    })
    service = CaregiverIntelligenceService(db)
    db_gap = service.create_responsibility_gap(gap)
    return db_gap


@router.get("/responsibility-gaps/{person_id}", response_model=List[ResponsibilityGap])
def list_responsibility_gaps(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_open_gaps(person_id)

@router.post("/responsibility-gaps/{gap_id}/review", response_model=ResponsibilityGap)
def review_responsibility_gap(gap_id: str, review: ResponsibilityGapReview, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, _current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "manage_responsibilities")
    gap = db.query(ResponsibilityGapModel).filter(
        ResponsibilityGapModel.id == str(gap_id),
        ResponsibilityGapModel.person_id == person_id,
    ).with_for_update().first()
    if gap is None:
        raise HTTPException(status_code=404, detail="Responsibility gap not found")
    if gap.status not in {CoverageGapStatus.OPEN, CoverageGapStatus.PARTIALLY_COVERED}:
        raise HTTPException(status_code=409, detail="This responsibility gap has already been closed")
    if review.status not in {
        CoverageGapStatus.COVERED,
        CoverageGapStatus.PARTIALLY_COVERED,
        CoverageGapStatus.RESOLVED,
    }:
        raise HTTPException(status_code=422, detail="Choose a reviewed coverage outcome")
    resolution = review.resolution.strip()
    if len(resolution) < 5:
        raise HTTPException(status_code=422, detail="Add a brief explanation of the coverage decision")
    previous_status = getattr(gap.status, "value", gap.status)
    gap.status = review.status
    gap.resolution = resolution
    gap.resolved_at = datetime.now(timezone.utc) if review.status in {
        CoverageGapStatus.COVERED,
        CoverageGapStatus.RESOLVED,
    } else None
    db.add(AuditLogEvent(
        actor_id=session_caregiver_id, subject_id=person_id, person_id=person_id,
        caregiver_id=session_caregiver_id, action="caregiver_responsibility_gap_reviewed",
        target_type="responsibility_gap", target_id=gap.id, outcome="success",
        request_id=request.headers.get("x-request-id"),
        policy_version="caregiver-intelligence-review-v1",
        record_metadata={
            "previous_status": previous_status,
            "new_status": review.status.value,
            "resolution_sha256": hashlib.sha256(resolution.encode("utf-8")).hexdigest(),
        },
    ))
    db.commit()
    db.refresh(gap)
    return gap


@router.post("/perspective-conflicts", response_model=PerspectiveConflict)
def create_perspective_conflict(conflict: PerspectiveConflictCreate, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, _session_caregiver_id, "share_observations")
    _assert_person_scope(request, db, conflict.person_id)
    if str(conflict.caregiver_a_id) == str(conflict.caregiver_b_id):
        raise HTTPException(status_code=422, detail="A perspective comparison requires two different caregivers.")
    _require_profile_in_person(db, conflict.caregiver_a_id, person_id)
    _require_profile_in_person(db, conflict.caregiver_b_id, person_id)
    if (
        conflict.status in {PerspectiveConflictStatus.ALIGNED, PerspectiveConflictStatus.RESOLVED}
        or conflict.resolution
        or conflict.resolved_at
    ):
        raise HTTPException(
            status_code=422,
            detail="A new perspective difference cannot be pre-resolved; preserve both reports for separate review.",
        )
    conflict = PerspectiveConflictCreate(**{
        **conflict.model_dump(),
        "person_id": person_id,
        "resolution": None,
        "resolved_at": None,
        "evidence_ref": "caregiver_shared_conflict_submission",
    })
    service = CaregiverIntelligenceService(db)
    db_conflict = service.create_perspective_conflict(conflict)
    return db_conflict


@router.get("/perspective-conflicts/{person_id}", response_model=List[PerspectiveConflict])
def list_perspective_conflicts(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_conflicts(person_id)

@router.post("/perspective-conflicts/{conflict_id}/resolve", response_model=PerspectiveConflict)
def resolve_perspective_conflict(conflict_id: str, review: PerspectiveConflictReview, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, _current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "share_observations")
    conflict = db.query(PerspectiveConflictModel).filter(
        PerspectiveConflictModel.id == str(conflict_id),
        PerspectiveConflictModel.person_id == person_id,
    ).with_for_update().first()
    if conflict is None:
        raise HTTPException(status_code=404, detail="Perspective difference not found")
    service = CaregiverIntelligenceService(db)
    if not any(item.id == conflict.id for item in service.get_conflicts(person_id)):
        raise HTTPException(status_code=404, detail="Perspective difference is not shared with this care circle")
    if conflict.status == PerspectiveConflictStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="This perspective difference has already been reviewed")
    resolution = review.resolution.strip()
    if len(resolution) < 5:
        raise HTTPException(status_code=422, detail="Add a brief explanation of the review outcome")
    previous_status = getattr(conflict.status, "value", conflict.status)
    conflict.status = PerspectiveConflictStatus.RESOLVED
    conflict.resolution = resolution
    conflict.resolved_at = datetime.now(timezone.utc)
    db.add(AuditLogEvent(
        actor_id=session_caregiver_id, subject_id=person_id, person_id=person_id,
        caregiver_id=session_caregiver_id, action="caregiver_perspective_conflict_resolved",
        target_type="perspective_conflict", target_id=conflict.id, outcome="success",
        request_id=request.headers.get("x-request-id"),
        policy_version="caregiver-intelligence-review-v1",
        record_metadata={
            "previous_status": previous_status,
            "new_status": "resolved",
            "resolution_sha256": hashlib.sha256(resolution.encode("utf-8")).hexdigest(),
        },
    ))
    db.commit()
    db.refresh(conflict)
    return conflict


@router.post("/duplicate-actions", response_model=DuplicateAction)
def create_duplicate_action(duplicate: DuplicateActionCreate, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, _session_caregiver_id, "correct_records")
    _assert_person_scope(request, db, duplicate.person_id)
    _require_profile_in_person(db, duplicate.caregiver_a_id, person_id)
    _require_profile_in_person(db, duplicate.caregiver_b_id, person_id)
    service = CaregiverIntelligenceService(db)
    db_dup = service.detect_duplicate_actions(
        person_id,
        duplicate.caregiver_a_id,
        duplicate.caregiver_b_id,
        duplicate.action_type,
        duplicate.action_description_a,
        duplicate.action_description_b,
    )
    return db_dup


@router.get("/duplicate-actions/{person_id}", response_model=List[DuplicateAction])
def list_duplicate_actions(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_duplicate_actions(person_id)

@router.post("/duplicate-actions/{duplicate_id}/review", response_model=DuplicateAction)
def review_duplicate_action(duplicate_id: str, review: DuplicateActionReview, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, _current_profile = _session_profile(request, db)
    require_caregiver_permission(db, person_id, session_caregiver_id, "correct_records")
    duplicate = db.query(DuplicateActionModel).filter(
        DuplicateActionModel.id == str(duplicate_id),
        DuplicateActionModel.person_id == person_id,
    ).with_for_update().first()
    if duplicate is None:
        raise HTTPException(status_code=404, detail="Possible duplicate action not found")
    if duplicate.status != DuplicateActionStatus.POTENTIAL_DUPLICATE:
        raise HTTPException(status_code=409, detail="This possible duplicate has already been reviewed")
    if review.status not in {
        DuplicateActionStatus.CONFIRMED_DUPLICATE,
        DuplicateActionStatus.APPROPRIATE_PARALLEL,
    }:
        raise HTTPException(status_code=422, detail="Choose a reviewed action outcome")
    resolution = review.resolution.strip()
    if len(resolution) < 5:
        raise HTTPException(status_code=422, detail="Add a brief explanation of the review outcome")
    duplicate.status = review.status
    duplicate.resolution = resolution
    db.add(AuditLogEvent(
        actor_id=session_caregiver_id, subject_id=person_id, person_id=person_id,
        caregiver_id=session_caregiver_id, action="caregiver_duplicate_action_reviewed",
        target_type="duplicate_action", target_id=duplicate.id, outcome="success",
        request_id=request.headers.get("x-request-id"),
        policy_version="caregiver-intelligence-review-v1",
        record_metadata={
            "previous_status": "potential_duplicate",
            "new_status": review.status.value,
            "resolution_sha256": hashlib.sha256(resolution.encode("utf-8")).hexdigest(),
        },
    ))
    db.commit()
    db.refresh(duplicate)
    return duplicate


@router.get("/caregiver-summary/{person_id}/{caregiver_id}", response_model=CaregiverSummary)
def get_caregiver_summary(person_id: str, caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, db, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    if str(caregiver_id) != str(current_profile.id):
        raise HTTPException(status_code=403, detail="Private caregiver summaries are only available to their caregiver")
    service = CaregiverIntelligenceService(db)
    return service.get_caregiver_summary(scoped_person_id, current_profile.id)


@router.get("/multi-caregiver-summary/{person_id}", response_model=MultiCaregiverSummary)
def get_multi_caregiver_summary(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_multi_caregiver_summary(person_id)

@router.get("/multi-caregiver-summary", response_model=MultiCaregiverSummary)
def get_scoped_multi_caregiver_summary(
    request: Request,
    person_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    scoped_person_id, _caregiver_id = _resolve_scoped_session(request, db)
    # The bearer session is authoritative. If a caller supplies a person_id,
    # treat it only as an assertion about the requested scope; never silently
    # ignore a mismatch and return another record under HTTP 200.
    if person_id is not None and str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="Care record access is not permitted")
    service = CaregiverIntelligenceService(db)
    return service.get_multi_caregiver_summary(scoped_person_id)


@router.get("/care-network-state")
def get_scoped_care_network_state(
    request: Request,
    person_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    scoped_person_id, _session_caregiver_id = _resolve_scoped_session(request, db)
    if person_id is not None and str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="Care record access is not permitted")
    service = CaregiverIntelligenceService(db)
    return service.get_care_network_state(scoped_person_id)


@router.get("/care-network-state/{person_id}")
def get_care_network_state(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_care_network_state(person_id)


@router.post("/unknown-actor-activity")
def record_unknown_actor_activity(person_id: str, activity_type: str, description: str, occurred_at: str, request: Request, shared_account_id: Optional[str] = None, db: Session = Depends(get_db)):
    person_id, session_caregiver_id = _assert_person_scope(request, db, person_id)
    require_caregiver_permission(db, person_id, session_caregiver_id, "add_evidence")
    service = CaregiverIntelligenceService(db)
    try:
        activity_type_enum = CaregiverActivityType(activity_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid activity type: {activity_type}")
    occurred_dt = datetime.fromisoformat(occurred_at)
    activity = service.record_unknown_actor_activity(person_id, activity_type_enum, description, occurred_dt, shared_account_id)
    return {"id": activity.id, "caregiver_id": activity.caregiver_id, "is_anonymous": activity.is_anonymous}


@router.get("/caregivers/by-email")
def get_caregiver_by_email(email: str, person_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    profile = service.get_caregiver_by_email(email, person_id=scoped_person_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Caregiver not found")
    return {"id": profile.id, "name": profile.name, "person_id": profile.person_id}


@router.get("/caregivers/by-name/{person_id}")
def get_caregivers_by_name(person_id: str, name: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, db, person_id)
    service = CaregiverIntelligenceService(db)
    profiles = service.get_caregiver_by_name(person_id, name)
    return [{"id": p.id, "name": p.name, "email": p.email} for p in profiles]
