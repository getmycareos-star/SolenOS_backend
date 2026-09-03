from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from app.core.database import get_db
from app.models.caregiver_intelligence import (
    CaregiverRole as CaregiverRoleModel,
    CaregiverPerspective as CaregiverPerspectiveModel,
    CaregiverCommunication as CaregiverCommunicationModel,
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
)

router = APIRouter()


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
def create_caregiver_profile(profile: CaregiverProfileCreate, db: Session = Depends(get_db)):
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
def list_caregiver_profiles(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.list_caregiver_profiles(person_id)


@router.get("/caregiver-profiles/detail/{caregiver_id}", response_model=CaregiverProfile)
def get_caregiver_profile(caregiver_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    profile = service.get_caregiver_profile(caregiver_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Caregiver profile not found")
    return profile


@router.patch("/caregiver-profiles/{caregiver_id}/involvement")
def update_caregiver_involvement(caregiver_id: str, status: str, db: Session = Depends(get_db)):
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
def create_caregiver_role(role: CaregiverRoleCreate, db: Session = Depends(get_db)):
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
def create_responsibility(responsibility: CaregiverResponsibilityCreate, db: Session = Depends(get_db)):
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
def create_observation(observation: CaregiverObservationCreate, db: Session = Depends(get_db)):
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
def list_observations(person_id: str, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    if caregiver_id:
        return service.get_observations_for_caregiver(person_id, caregiver_id)
    return service.get_shared_observations(person_id)


@router.get("/caregiver-observations/{person_id}/caregiver/{caregiver_id}", response_model=List[CaregiverObservation])
def list_caregiver_observations(person_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_observations_for_caregiver(person_id, caregiver_id)


@router.post("/caregiver-observations/{observation_id}/promote")
def promote_observation(observation_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    observation = service.promote_observation_to_shared(observation_id, caregiver_id)
    if not observation:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"id": observation.id, "is_promoted_to_shared": observation.is_promoted_to_shared, "visibility": observation.information_visibility.value}


@router.post("/caregiver-perspectives", response_model=CaregiverPerspective)
def create_perspective(perspective: CaregiverPerspectiveCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_persp = service.record_perspective(perspective)
    return db_persp


@router.get("/caregiver-perspectives/{person_id}", response_model=List[CaregiverPerspective])
def list_perspectives(person_id: str, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    if caregiver_id:
        return service.get_perspectives_for_caregiver(person_id, caregiver_id)
    return (
        db.query(CaregiverPerspectiveModel)
        .filter(CaregiverPerspectiveModel.person_id == person_id, CaregiverPerspectiveModel.is_promoted_to_shared)
        .order_by(CaregiverPerspectiveModel.timestamp.desc())
        .all()
    )


@router.post("/caregiver-perspectives/{perspective_id}/promote")
def promote_perspective(perspective_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    perspective = service.promote_perspective_to_shared(perspective_id, caregiver_id)
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {"id": perspective.id, "is_promoted_to_shared": perspective.is_promoted_to_shared, "visibility": perspective.information_visibility.value}


@router.post("/caregiver-handoffs", response_model=CaregiverHandoff)
def create_handoff(handoff: CaregiverHandoffCreate, db: Session = Depends(get_db)):
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
def accept_handoff(handoff_id: str, acceptance_evidence: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    handoff = service.accept_handoff(handoff_id, acceptance_evidence)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    return {"id": handoff.id, "acceptance_status": handoff.acceptance_status.value, "is_completed": handoff.is_completed}


@router.get("/caregiver-handoffs/{person_id}", response_model=List[CaregiverHandoff])
def list_handoffs(person_id: str, caregiver_id: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    handoffs = service.get_pending_handoffs(person_id, caregiver_id)
    if status:
        try:
            status_enum = HandoffAcceptanceStatus(status)
            handoffs = [h for h in handoffs if h.acceptance_status == status_enum]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid handoff status: {status}")
    return handoffs


@router.post("/caregiver-activities", response_model=CaregiverActivity)
def create_activity(activity: CaregiverActivityCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_activity = service.record_activity(activity)
    return db_activity


@router.get("/caregiver-activities/{person_id}", response_model=List[CaregiverActivity])
def list_activities(person_id: str, caregiver_id: Optional[str] = None, limit: int = 100, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_activity_history(person_id, caregiver_id, limit)


@router.post("/caregiver-communications", response_model=CaregiverCommunication)
def create_communication(communication: CaregiverCommunicationCreate, db: Session = Depends(get_db)):
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
def list_communications(person_id: str, caregiver_id: Optional[str] = None, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    if caregiver_id:
        return service.get_communications_for_caregiver(person_id, caregiver_id)
    return (
        db.query(CaregiverCommunicationModel)
        .filter(CaregiverCommunicationModel.person_id == person_id)
        .order_by(CaregiverCommunicationModel.communication_time.desc())
        .all()
    )


@router.post("/care-network-relationships", response_model=CareNetworkRelationship)
def create_network_relationship(relationship: CareNetworkRelationshipCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_rel = service.create_care_network_relationship(relationship)
    return db_rel


@router.get("/care-network-relationships/{person_id}", response_model=List[CareNetworkRelationship])
def list_network_relationships(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_care_network(person_id)


@router.post("/caregiver-contexts", response_model=CaregiverContext)
def create_context(context: CaregiverContextCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_context = service.create_context(context)
    return db_context


@router.get("/caregiver-contexts/{person_id}", response_model=List[CaregiverContext])
def list_contexts(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_contexts(person_id)


@router.post("/responsibility-gaps", response_model=ResponsibilityGap)
def create_responsibility_gap(gap: ResponsibilityGapCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_gap = service.create_responsibility_gap(gap)
    return db_gap


@router.get("/responsibility-gaps/{person_id}", response_model=List[ResponsibilityGap])
def list_responsibility_gaps(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_open_gaps(person_id)


@router.post("/perspective-conflicts", response_model=PerspectiveConflict)
def create_perspective_conflict(conflict: PerspectiveConflictCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_conflict = service.create_perspective_conflict(conflict)
    return db_conflict


@router.get("/perspective-conflicts/{person_id}", response_model=List[PerspectiveConflict])
def list_perspective_conflicts(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_conflicts(person_id)


@router.post("/duplicate-actions", response_model=DuplicateAction)
def create_duplicate_action(duplicate: DuplicateActionCreate, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    db_dup = service.detect_duplicate_actions(
        duplicate.person_id,
        duplicate.caregiver_a_id,
        duplicate.caregiver_b_id,
        duplicate.action_type,
        duplicate.action_description_a,
        duplicate.action_description_b,
    )
    return db_dup


@router.get("/duplicate-actions/{person_id}", response_model=List[DuplicateAction])
def list_duplicate_actions(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_duplicate_actions(person_id)


@router.get("/caregiver-summary/{person_id}/{caregiver_id}", response_model=CaregiverSummary)
def get_caregiver_summary(person_id: str, caregiver_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_caregiver_summary(person_id, caregiver_id)


@router.get("/multi-caregiver-summary/{person_id}", response_model=MultiCaregiverSummary)
def get_multi_caregiver_summary(person_id: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    return service.get_multi_caregiver_summary(person_id)


@router.get("/care-network-state/{person_id}")
def get_care_network_state(person_id: str, db: Session = Depends(get_db)):
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
def get_caregiver_by_email(email: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    profile = service.get_caregiver_by_email(email)
    if not profile:
        raise HTTPException(status_code=404, detail="Caregiver not found")
    return {"id": profile.id, "name": profile.name, "person_id": profile.person_id}


@router.get("/caregivers/by-name/{person_id}")
def get_caregivers_by_name(person_id: str, name: str, db: Session = Depends(get_db)):
    service = CaregiverIntelligenceService(db)
    profiles = service.get_caregiver_by_name(person_id, name)
    return [{"id": p.id, "name": p.name, "email": p.email} for p in profiles]
