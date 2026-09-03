from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from app.core.database import get_db
from app.models.action import Action as ActionModel, ActionStatus
from app.schemas.action import (
    ActionCreate,
    Action as ActionSchema,
    ActionCandidate,
    ActionUpdate,
    ActionCompletionEvidence,
    ActionDeduplicationResult,
    ActionSafetyCheck,
)
from app.services.action_extraction import ActionExtractionEngine
from app.services.action_normalization import ActionNormalizationService
from app.services.action_lifecycle import ActionLifecycleService, ActionIdentityService

router = APIRouter()

extraction_engine = ActionExtractionEngine()
normalization_service = ActionNormalizationService()
lifecycle_service = ActionLifecycleService()
identity_service = ActionIdentityService()


def _get_action_or_404(db: Session, action_id: str) -> ActionModel:
    action = db.query(ActionModel).filter(ActionModel.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


@router.post("/documents/{document_id}/extract-actions", response_model=List[ActionSchema])
def extract_actions_from_document(
    document_id: str,
    document_text: str,
    source_document_type: Optional[str] = None,
    source_authority: str = "unknown",
    reference_date: Optional[str] = None,
    caregiver_id: str = None,
    db: Session = Depends(get_db),
):
    ref_dt = datetime.fromisoformat(reference_date) if reference_date else datetime.utcnow()

    candidates = extraction_engine.extract(
        document_text=document_text,
        source_evidence_id=document_id,
        source_document_type=source_document_type,
        source_authority=source_authority,
        reference_date=ref_dt,
    )

    created_actions = []
    for candidate in candidates:
        candidate = normalization_service.validate_and_normalize(candidate)

        safety = normalization_service.safety_check(candidate)
        if not safety.is_safe_to_create and safety.blocked_reason:
            continue

        action_model = ActionModel(
            person_id=None,
            source_evidence_id=candidate.source_evidence_id,
            source_passage=candidate.source_passage,
            source_document_type=source_document_type,
            source_authority=source_authority,
            extraction_confidence=candidate.extraction_confidence,
            provenance_notes=candidate.provenance_notes,
            actor_type=candidate.actor_type,
            actor_label=candidate.actor_label,
            subject_type="patient",
            action_type=candidate.action_type,
            action_object=candidate.action_object,
            normalized_action=candidate.normalized_action,
            original_text=candidate.original_text,
            modality=candidate.modality,
            condition_text=candidate.condition_text,
            trigger_text=candidate.trigger_text,
            deadline=candidate.deadline,
            has_condition=candidate.has_condition,
            is_explicit=candidate.is_explicit,
            is_medication_action=candidate.is_medication_action,
            risk_tier=candidate.risk_tier,
            requires_confirmation=candidate.requires_confirmation,
            is_recurring=candidate.is_recurring,
            status=ActionStatus.PENDING_CONFIRMATION if candidate.requires_confirmation else ActionStatus.ACTIVE,
            created_by_caregiver_id=caregiver_id,
        )
        db.add(action_model)
        db.commit()
        db.refresh(action_model)
        created_actions.append(action_model)

    return created_actions


@router.post("/actions", response_model=ActionSchema)
def create_action(action: ActionCreate, db: Session = Depends(get_db)):
    if not action.person_id:
        raise HTTPException(status_code=400, detail="person_id is required")

    candidate = ActionCandidate(
        source_evidence_id=action.source_evidence_id,
        source_passage=action.source_passage,
        original_text=action.original_text,
        extraction_confidence=action.extraction_confidence,
        actor_type=action.actor_type,
        actor_label=action.actor_label,
        action_type=action.action_type,
        action_object=action.action_object,
        normalized_action=action.normalized_action,
        modality=action.modality,
        condition_text=action.condition_text,
        trigger_text=action.trigger_text,
        is_explicit=action.is_explicit,
        is_medication_action=action.is_medication_action,
        risk_tier=action.risk_tier,
        requires_confirmation=action.requires_confirmation,
        has_condition=action.has_condition,
        is_recurring=action.is_recurring,
        provenance_notes=action.provenance_notes,
    )

    candidate = normalization_service.validate_and_normalize(candidate)
    safety = normalization_service.safety_check(candidate)

    if not safety.is_safe_to_create:
        raise HTTPException(status_code=400, detail={"blocked_reason": safety.blocked_reason, "warnings": safety.warnings})

    db_action = ActionModel(
        person_id=action.person_id,
        source_evidence_id=candidate.source_evidence_id,
        source_passage=candidate.source_passage,
        source_document_type=action.source_document_type,
        source_authority=action.source_authority,
        extraction_confidence=candidate.extraction_confidence,
        provenance_notes=candidate.provenance_notes,
        actor_type=candidate.actor_type,
        actor_id=action.actor_id,
        actor_label=candidate.actor_label,
        subject_type=action.subject_type,
        subject_id=action.subject_id,
        subject_label=action.subject_label,
        action_type=candidate.action_type,
        action_object=candidate.action_object,
        normalized_action=candidate.normalized_action,
        original_text=candidate.original_text,
        modality=candidate.modality,
        condition_text=candidate.condition_text,
        trigger_text=candidate.trigger_text,
        deadline=action.deadline,
        start_time=action.start_time,
        duration=action.duration,
        recurrence=action.recurrence,
        reference_frame_id=action.reference_frame_id,
        prerequisite_action_ids=action.prerequisite_action_ids,
        depends_on_event_id=action.depends_on_event_id,
        status=ActionStatus.PENDING_CONFIRMATION if candidate.requires_confirmation else ActionStatus.ACTIVE,
        completion_evidence_ids=action.completion_evidence_ids,
        completion_confidence=action.completion_confidence,
        superseded_by_action_id=action.superseded_by_action_id,
        is_explicit=candidate.is_explicit,
        is_medication_action=candidate.is_medication_action,
        risk_tier=candidate.risk_tier,
        requires_confirmation=candidate.requires_confirmation,
        has_condition=candidate.has_condition,
        is_recurring=candidate.is_recurring,
        created_by_caregiver_id=action.created_by_caregiver_id,
    )
    db.add(db_action)
    db.commit()
    db.refresh(db_action)
    return db_action


@router.get("/actions/{person_id}", response_model=List[ActionSchema])
def list_actions(person_id: str, status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(ActionModel).filter(ActionModel.person_id == person_id)
    if status:
        query = query.filter(ActionModel.status == status)
    return query.order_by(ActionModel.created_at.desc()).all()


@router.get("/actions/detail/{action_id}", response_model=ActionSchema)
def get_action(action_id: str, db: Session = Depends(get_db)):
    return _get_action_or_404(db, action_id)


@router.patch("/actions/{action_id}", response_model=ActionSchema)
def update_action(action_id: str, update: ActionUpdate, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    update_data = update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if value is not None:
            setattr(db_action, field, value)

    db.add(db_action)
    db.commit()
    db.refresh(db_action)
    return db_action


@router.post("/actions/{action_id}/complete", response_model=ActionSchema)
def complete_action(action_id: str, evidence: ActionCompletionEvidence, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    return lifecycle_service.record_completion(db_action, evidence, db)


@router.post("/actions/{action_id}/cancel", response_model=ActionSchema)
def cancel_action(action_id: str, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    return lifecycle_service.cancel_action(db_action, db)


@router.post("/actions/{action_id}/supersede", response_model=ActionSchema)
def supersede_action(action_id: str, new_action_id: str, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    return lifecycle_service.supersede_action(db_action, new_action_id, db)


@router.post("/actions/{action_id}/activate", response_model=ActionSchema)
def activate_action(action_id: str, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    return lifecycle_service.activate_action(db_action, db)


@router.post("/actions/{action_id}/block", response_model=ActionSchema)
def block_action(action_id: str, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    return lifecycle_service.block_action(db_action, db)


@router.post("/actions/{action_id}/expire", response_model=ActionSchema)
def expire_action(action_id: str, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    return lifecycle_service.expire_action(db_action, db)


@router.get("/actions/{action_id}/duplicates", response_model=List[ActionDeduplicationResult])
def find_duplicate_actions(action_id: str, db: Session = Depends(get_db)):
    db_action = _get_action_or_404(db, action_id)
    existing = db.query(ActionModel).filter(ActionModel.person_id == db_action.person_id).all()
    duplicates = identity_service.find_duplicates(db_action, existing)
    return [ActionDeduplicationResult(**d) for d in duplicates]


@router.post("/actions/safety-check", response_model=ActionSafetyCheck)
def check_action_safety(candidate: ActionCandidate):
    return normalization_service.safety_check(candidate)


@router.get("/actions/{person_id}/uncompleted", response_model=List[ActionSchema])
def list_uncompleted_actions(person_id: str, db: Session = Depends(get_db)):
    active_statuses = ["active", "conditional", "pending_confirmation", "blocked"]
    return (
        db.query(ActionModel)
        .filter(ActionModel.person_id == person_id, ActionModel.status.in_(active_statuses))
        .order_by(ActionModel.created_at.desc())
        .all()
    )
