from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.core.database import get_db
from app.models.care import Caregiver as SessionCaregiverModel
from app.services.caregiver_profile_identity import ensure_caregiver_profile_for_session
from app.models.caregiver_intelligence import (
    CaregiverProfile as CaregiverProfileModel,
    CaregiverRole as CaregiverRoleModel,
    CaregiverResponsibility as CaregiverResponsibilityModel,
    CaregiverObservation as CaregiverObservationModel,
    CaregiverPerspective as CaregiverPerspectiveModel,
    CaregiverHandoff as CaregiverHandoffModel,
    CaregiverActivity as CaregiverActivityModel,
    CaregiverCommunication as CaregiverCommunicationModel,
    CareNetworkRelationship as CareNetworkRelationshipModel,
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
)
from app.services.caregiver_intelligence import CaregiverIntelligenceService
from app.core.caregiver_enums import (
    CaregiverInvolvementStatus,
    HandoffAcceptanceStatus,
    CaregiverActivityType,
    InformationVisibility,
)

router = APIRouter()


def _session_profile(request: Request, db: Session) -> tuple[str, str, CaregiverProfileModel]:
    person_id = getattr(request.state, "scoped_person_id", None)
    session_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not session_caregiver_id:
        raise HTTPException(status_code=401, detail="A valid care-record session is required")
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


def _assert_person_scope(request: Request, person_id: str) -> tuple[str, str]:
    scoped_person_id = getattr(request.state, "scoped_person_id", None)
    scoped_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not scoped_person_id or not scoped_caregiver_id:
        raise HTTPException(status_code=401, detail="A valid care-record session is required")
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
    _assert_person_scope(request, profile.person_id)
    service = CaregiverIntelligenceService(db)
    db_profile = service.create_caregiver_profile(profile)
    _log_and_learn(
        db,
        db_profile.person_id,
        "caregiver_profile_created",
        f"Created caregiver profile for {db_profile.name}",
        "caregiver_profile",
        source_id=db_profile.id,
        caregiver_id=db_profile.id,
    )
    return db_profile


@router.get("/caregiver-profiles/{person_id}", response_model=List[CaregiverProfile])
def list_caregiver_profiles(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
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
    person_id, _session_caregiver_id, current_profile = _session_profile(request, db)
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
    _assert_person_scope(request, role.person_id)
    _assert_actor_id(role.caregiver_id, current_profile.id, session_caregiver_id)
    _require_profile_in_person(db, role.caregiver_id if role.caregiver_id != session_caregiver_id else current_profile.id, person_id)
    role = CaregiverRoleCreate(**{**role.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id})
    service = CaregiverIntelligenceService(db)
    db_role = service.create_caregiver_role(role)
    return db_role


@router.get("/caregiver-roles/{person_id}", response_model=List[CaregiverRole])
def list_caregiver_roles(person_id: str, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    if caregiver_id:
        return service.get_current_roles(caregiver_id, person_id)
    return (
        db.query(CaregiverRoleModel)
        .filter(CaregiverRoleModel.person_id == person_id, CaregiverRoleModel.is_current)
        .all()
    )


@router.post("/caregiver-responsibilities", response_model=CaregiverResponsibility)
def create_responsibility(responsibility: CaregiverResponsibilityCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_person_scope(request, responsibility.person_id)
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
        caregiver_id=db_resp.caregiver_id,
    )
    return db_resp


@router.get("/caregiver-responsibilities/{person_id}", response_model=List[CaregiverResponsibility])
def list_responsibilities(person_id: str, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_active_responsibilities(person_id, caregiver_id)


@router.get("/caregiver-responsibilities/history/{caregiver_id}", response_model=List[CaregiverResponsibility])
def get_responsibility_history(caregiver_id: str, person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_responsibility_history(person_id, caregiver_id)


@router.post("/caregiver-observations", response_model=CaregiverObservation)
def create_observation(observation: CaregiverObservationCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_person_scope(request, observation.person_id)
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
        caregiver_id=db_obs.caregiver_id,
    )
    return db_obs


@router.get("/caregiver-observations/{person_id}", response_model=List[CaregiverObservation])
def list_observations(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, session_caregiver_id = _assert_person_scope(request, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    # Private observations are visible only to their author. A stale/local
    # caregiver query key must never select another caregiver's private rows.
    if caregiver_id and str(caregiver_id) in {str(current_profile.id), str(session_caregiver_id)}:
        return service.get_observations_for_caregiver(scoped_person_id, current_profile.id)
    return service.get_shared_observations(scoped_person_id)


@router.get("/caregiver-observations/{person_id}/caregiver/{caregiver_id}", response_model=List[CaregiverObservation])
def list_caregiver_observations(person_id: str, caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, session_caregiver_id = _assert_person_scope(request, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    if str(caregiver_id) not in {str(current_profile.id), str(session_caregiver_id)}:
        raise HTTPException(status_code=403, detail="Private observations are only available to their caregiver")
    service = CaregiverIntelligenceService(db)
    return service.get_observations_for_caregiver(scoped_person_id, current_profile.id)


@router.post("/caregiver-observations/{observation_id}/promote")
def promote_observation(observation_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_actor_id(caregiver_id, current_profile.id, session_caregiver_id)
    service = CaregiverIntelligenceService(db)
    observation = service.promote_observation_to_shared(observation_id, current_profile.id, person_id=person_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"id": observation.id, "is_promoted_to_shared": observation.is_promoted_to_shared, "visibility": observation.information_visibility.value}


@router.post("/caregiver-perspectives", response_model=CaregiverPerspective)
def create_perspective(perspective: CaregiverPerspectiveCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_person_scope(request, perspective.person_id)
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
    scoped_person_id, session_caregiver_id = _assert_person_scope(request, person_id)
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
    _assert_actor_id(caregiver_id, current_profile.id, session_caregiver_id)
    service = CaregiverIntelligenceService(db)
    perspective = service.promote_perspective_to_shared(perspective_id, current_profile.id, person_id=person_id)
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {"id": perspective.id, "is_promoted_to_shared": perspective.is_promoted_to_shared, "visibility": perspective.information_visibility.value}


@router.post("/caregiver-handoffs", response_model=CaregiverHandoff)
def create_handoff(handoff: CaregiverHandoffCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_person_scope(request, handoff.person_id)
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
        caregiver_id=db_handoff.from_caregiver_id,
    )
    return db_handoff


@router.post("/caregiver-handoffs/{handoff_id}/accept")
def accept_handoff(handoff_id: str, request: Request, acceptance_evidence: Optional[str] = None, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    handoff = service.accept_handoff(handoff_id, acceptance_evidence, person_id=person_id, accepting_caregiver_id=current_profile.id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    return {"id": handoff.id, "acceptance_status": handoff.acceptance_status.value, "is_completed": handoff.is_completed}


@router.get("/caregiver-handoffs/{person_id}", response_model=List[CaregiverHandoff])
def list_handoffs(person_id: str, request: Request, caregiver_id: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, person_id)
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
    _assert_person_scope(request, activity.person_id)
    _assert_actor_id(activity.caregiver_id, current_profile.id, session_caregiver_id)
    if activity.is_anonymous:
        raise HTTPException(status_code=422, detail="Anonymous activity must use the dedicated unknown-actor workflow")
    activity = CaregiverActivityCreate(**{**activity.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id, "is_anonymous": False, "shared_account_id": None})
    service = CaregiverIntelligenceService(db)
    db_activity = service.record_activity(activity)
    return db_activity


@router.get("/caregiver-activities/{person_id}", response_model=List[CaregiverActivity])
def list_activities(person_id: str, request: Request, caregiver_id: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    service = CaregiverIntelligenceService(db)
    return service.get_activity_history(scoped_person_id, current_profile.id, min(max(limit, 1), 100))


@router.post("/caregiver-communications", response_model=CaregiverCommunication)
def create_communication(communication: CaregiverCommunicationCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_person_scope(request, communication.person_id)
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
        caregiver_id=db_comm.from_caregiver_id,
    )
    return db_comm


@router.get("/caregiver-communications/{person_id}", response_model=List[CaregiverCommunication])
def list_communications(person_id: str, request: Request, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, person_id)
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
    _assert_person_scope(request, relationship.person_id)
    _assert_actor_id(relationship.from_caregiver_id, current_profile.id, session_caregiver_id)
    target_id = _resolve_profile_target(db, relationship.to_caregiver_id, person_id).id if relationship.to_caregiver_id else None
    relationship = CareNetworkRelationshipCreate(**{**relationship.model_dump(), "person_id": person_id, "from_caregiver_id": current_profile.id, "to_caregiver_id": target_id})
    service = CaregiverIntelligenceService(db)
    db_rel = service.create_care_network_relationship(relationship)
    return db_rel


@router.get("/care-network-relationships/{person_id}", response_model=List[CareNetworkRelationship])
def list_network_relationships(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_care_network(person_id)


@router.post("/caregiver-contexts", response_model=CaregiverContext)
def create_context(context: CaregiverContextCreate, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, current_profile = _session_profile(request, db)
    _assert_person_scope(request, context.person_id)
    _assert_actor_id(context.caregiver_id, current_profile.id, session_caregiver_id)
    context = CaregiverContextCreate(**{**context.model_dump(), "person_id": person_id, "caregiver_id": current_profile.id})
    service = CaregiverIntelligenceService(db)
    db_context = service.create_context(context)
    return db_context


@router.get("/caregiver-contexts/{person_id}", response_model=List[CaregiverContext])
def list_contexts(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_contexts(person_id)


@router.post("/responsibility-gaps", response_model=ResponsibilityGap)
def create_responsibility_gap(gap: ResponsibilityGapCreate, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    _assert_person_scope(request, gap.person_id)
    gap = ResponsibilityGapCreate(**{**gap.model_dump(), "person_id": person_id})
    service = CaregiverIntelligenceService(db)
    db_gap = service.create_responsibility_gap(gap)
    return db_gap


@router.get("/responsibility-gaps/{person_id}", response_model=List[ResponsibilityGap])
def list_responsibility_gaps(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_open_gaps(person_id)


@router.post("/perspective-conflicts", response_model=PerspectiveConflict)
def create_perspective_conflict(conflict: PerspectiveConflictCreate, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    _assert_person_scope(request, conflict.person_id)
    conflict = PerspectiveConflictCreate(**{**conflict.model_dump(), "person_id": person_id})
    service = CaregiverIntelligenceService(db)
    db_conflict = service.create_perspective_conflict(conflict)
    return db_conflict


@router.get("/perspective-conflicts/{person_id}", response_model=List[PerspectiveConflict])
def list_perspective_conflicts(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_conflicts(person_id)


@router.post("/duplicate-actions", response_model=DuplicateAction)
def create_duplicate_action(duplicate: DuplicateActionCreate, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, _current_profile = _session_profile(request, db)
    _assert_person_scope(request, duplicate.person_id)
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
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_duplicate_actions(person_id)


@router.get("/caregiver-summary/{person_id}/{caregiver_id}", response_model=CaregiverSummary)
def get_caregiver_summary(person_id: str, caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, person_id)
    _scoped_person, _session_id, current_profile = _session_profile(request, db)
    if str(caregiver_id) != str(current_profile.id):
        raise HTTPException(status_code=403, detail="Private caregiver summaries are only available to their caregiver")
    service = CaregiverIntelligenceService(db)
    return service.get_caregiver_summary(scoped_person_id, current_profile.id)


@router.get("/multi-caregiver-summary/{person_id}", response_model=MultiCaregiverSummary)
def get_multi_caregiver_summary(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_multi_caregiver_summary(person_id)


@router.get("/care-network-state/{person_id}")
def get_care_network_state(person_id: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    return service.get_care_network_state(person_id)


@router.post("/unknown-actor-activity")
def record_unknown_actor_activity(person_id: str, activity_type: str, description: str, occurred_at: str, shared_account_id: Optional[str] = None, db: Session = Depends(get_db)):
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
    scoped_person_id, _session_caregiver_id = _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    profile = service.get_caregiver_by_email(email, person_id=scoped_person_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Caregiver not found")
    return {"id": profile.id, "name": profile.name, "person_id": profile.person_id}


@router.get("/caregivers/by-name/{person_id}")
def get_caregivers_by_name(person_id: str, name: str, request: Request, db: Session = Depends(get_db)):
    _assert_person_scope(request, person_id)
    service = CaregiverIntelligenceService(db)
    profiles = service.get_caregiver_by_name(person_id, name)
    return [{"id": p.id, "name": p.name, "email": p.email} for p in profiles]
