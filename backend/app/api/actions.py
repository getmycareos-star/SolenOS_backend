from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional
from app.core.database import get_db
from app.models.action import Action as ActionModel, ActionStatus
from app.models.care import Caregiver as CaregiverModel, Evidence
from app.services.medication_authority import medication_instruction_requires_authority, get_verified_medication_authority, action_has_verified_medication_authority
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


def _session_scope(request: Request):
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    person_id = getattr(request.state, "scoped_person_id", None)
    if not caregiver_id or not person_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    return str(caregiver_id), str(person_id)


def _get_action_or_404(db: Session, action_id: str) -> ActionModel:
    action = db.query(ActionModel).filter(ActionModel.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


def _get_scoped_action_or_404(db: Session, request: Request, action_id: str) -> ActionModel:
    _, person_id = _session_scope(request)
    action = _get_action_or_404(db, action_id)
    if str(action.person_id) != person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care action")
    return action


@router.post("/documents/{document_id}/extract-actions", response_model=List[ActionSchema])
def extract_actions_from_document(
    document_id: str,
    request: Request,
    document_text: str,
    source_document_type: Optional[str] = None,
    source_authority: str = "unknown",
    reference_date: Optional[str] = None,
    caregiver_id: str = None,
    db: Session = Depends(get_db),
):
    scoped_person_id = getattr(request.state, "scoped_person_id", None)
    scoped_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not scoped_person_id or not scoped_caregiver_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    source_evidence = db.query(Evidence).filter(Evidence.id == document_id, Evidence.person_id == scoped_person_id).first()
    if source_evidence is None:
        raise HTTPException(status_code=404, detail="Source evidence not found in this care record")
    if (document_text or "").strip() != (source_evidence.source_text or "").strip():
        raise HTTPException(status_code=422, detail="Action extraction must use the stored source evidence text")
    ref_dt = datetime.fromisoformat(reference_date) if reference_date else datetime.now().astimezone()

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
        if not (candidate.original_text or "").strip() or (candidate.original_text or "").strip() not in (source_evidence.source_text or ""):
            continue
        if not (candidate.source_passage or "").strip() or (candidate.source_passage or "").strip() not in (source_evidence.source_text or ""):
            continue
        medication_text = " ".join((candidate.original_text or "", candidate.source_passage or "", candidate.normalized_action or "", candidate.action_object or ""))
        if medication_instruction_requires_authority(medication_text, candidate.is_medication_action):
            verification = get_verified_medication_authority(db, person_id=scoped_person_id, evidence_id=document_id, source_authority=source_authority)
            if verification is None:
                continue

        action_model = ActionModel(
            person_id=scoped_person_id,
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
            created_by_caregiver_id=scoped_caregiver_id,
        )
        db.add(action_model)
        db.commit()
        db.refresh(action_model)
        created_actions.append(action_model)

    return created_actions


@router.post("/actions", response_model=ActionSchema)
def create_action(action: ActionCreate, request: Request, db: Session = Depends(get_db)):
    scoped_person_id = getattr(request.state, "scoped_person_id", None)
    scoped_caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not scoped_person_id or not scoped_caregiver_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    if str(action.person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
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

    source_evidence = db.query(Evidence).filter(Evidence.id == action.source_evidence_id, Evidence.person_id == scoped_person_id).first()
    if source_evidence is None:
        raise HTTPException(status_code=422, detail="Source evidence must exist and belong to this care record")
    stored_source = source_evidence.source_text or ""
    if not (candidate.source_passage or "").strip() or candidate.source_passage.strip() not in stored_source:
        raise HTTPException(status_code=422, detail="source_passage must quote the stored source evidence")
    if not (candidate.original_text or "").strip() or candidate.original_text.strip() not in stored_source:
        raise HTTPException(status_code=422, detail="original_text must quote the stored source evidence")
    medication_text = " ".join((candidate.original_text or "", candidate.source_passage or "", candidate.normalized_action or "", candidate.action_object or ""))
    if medication_instruction_requires_authority(medication_text, candidate.is_medication_action):
        verification = get_verified_medication_authority(db, person_id=scoped_person_id, evidence_id=action.source_evidence_id, source_authority=action.source_authority)
        if verification is None:
            raise HTTPException(status_code=409, detail={"blocked_reason": "Medication instructions are not authoritative until source evidence has a valid trusted prescriber verification", "requires_clinical_source_verification": True})

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
        created_by_caregiver_id=scoped_caregiver_id,
    )
    db.add(db_action)
    db.commit()
    db.refresh(db_action)
    return db_action


@router.get("/actions/{person_id}", response_model=List[ActionSchema])
def list_actions(person_id: str, request: Request, status: Optional[str] = None, db: Session = Depends(get_db)):
    _, scoped_person_id = _session_scope(request)
    if str(person_id) != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    query = db.query(ActionModel).filter(ActionModel.person_id == scoped_person_id)
    if status:
        query = query.filter(ActionModel.status == status)
    return query.order_by(ActionModel.created_at.desc()).all()


@router.get("/actions/detail/{action_id}", response_model=ActionSchema)
def get_action(action_id: str, request: Request, db: Session = Depends(get_db)):
    return _get_scoped_action_or_404(db, request, action_id)


@router.patch("/actions/{action_id}", response_model=ActionSchema)
def update_action(action_id: str, update: ActionUpdate, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    update_data = update.model_dump(exclude_unset=True)
    if update_data.get("status") == ActionStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Use the completion endpoint to record evidence and authenticated caregiver attribution")
    if update_data.get("status") == ActionStatus.ACTIVE and not action_has_verified_medication_authority(db, db_action):
        raise HTTPException(status_code=409, detail="Medication source authority is missing, expired, or no longer matches the source evidence")

    # Assignment labels and actor types are derived from a validated caregiver ID;
    # callers cannot spoof them independently.
    if ("actor_type" in update_data or "actor_label" in update_data) and "actor_id" not in update_data:
        raise HTTPException(status_code=422, detail="Assignment identity must be updated through a linked caregiver ID")

    # An assignee must be an existing caregiver linked to this same care record.
    # Never trust a client-supplied caregiver ID without verifying membership.
    if "actor_id" in update_data:
        assignee_id = update_data["actor_id"]
        if assignee_id:
            assignee = (
                db.query(CaregiverModel)
                .filter(CaregiverModel.id == assignee_id, CaregiverModel.person_id == db_action.person_id)
                .first()
            )
            if not assignee:
                raise HTTPException(status_code=422, detail="Assignee must be linked to this care record")
            update_data["actor_type"] = "caregiver"
            update_data["actor_label"] = assignee.name
        else:
            update_data["actor_type"] = "unknown"
            update_data["actor_label"] = None

    for field, value in update_data.items():
        if value is not None or field in {"actor_id", "actor_label"}:
            setattr(db_action, field, value)

    db.add(db_action)
    db.commit()
    db.refresh(db_action)
    return db_action


@router.post("/actions/{action_id}/complete", response_model=ActionSchema)
def complete_action(action_id: str, evidence: ActionCompletionEvidence, request: Request, db: Session = Depends(get_db)):
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    person_id = getattr(request.state, "scoped_person_id", None)
    if not caregiver_id or not person_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    db_action = _get_action_or_404(db, action_id)
    if str(db_action.person_id) != str(person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care action")
    try:
        return lifecycle_service.record_completion(db_action, evidence, db, completed_by_caregiver_id=str(caregiver_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/actions/{action_id}/cancel", response_model=ActionSchema)
def cancel_action(action_id: str, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    return lifecycle_service.cancel_action(db_action, db)


@router.post("/actions/{action_id}/supersede", response_model=ActionSchema)
def supersede_action(action_id: str, new_action_id: str, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    new_action = _get_scoped_action_or_404(db, request, new_action_id)
    return lifecycle_service.supersede_action(db_action, new_action.id, db)


@router.post("/actions/{action_id}/activate", response_model=ActionSchema)
def activate_action(action_id: str, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    return lifecycle_service.activate_action(db_action, db)


@router.post("/actions/{action_id}/block", response_model=ActionSchema)
def block_action(action_id: str, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    return lifecycle_service.block_action(db_action, db)


@router.post("/actions/{action_id}/expire", response_model=ActionSchema)
def expire_action(action_id: str, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    return lifecycle_service.expire_action(db_action, db)


@router.get("/actions/{action_id}/duplicates", response_model=List[ActionDeduplicationResult])
def find_duplicate_actions(action_id: str, request: Request, db: Session = Depends(get_db)):
    db_action = _get_scoped_action_or_404(db, request, action_id)
    existing = db.query(ActionModel).filter(ActionModel.person_id == db_action.person_id).all()
    duplicates = identity_service.find_duplicates(db_action, existing)
    return [ActionDeduplicationResult(**d) for d in duplicates]


@router.post("/actions/safety-check", response_model=ActionSafetyCheck)
def check_action_safety(candidate: ActionCandidate):
    return normalization_service.safety_check(candidate)


@router.get("/actions/{person_id}/uncompleted", response_model=List[ActionSchema])
def list_uncompleted_actions(person_id: str, request: Request, db: Session = Depends(get_db)):
    _, scoped_person_id = _session_scope(request)
    if str(person_id) != scoped_person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    active_statuses = ["active", "conditional", "pending_confirmation", "blocked"]
    return (
        db.query(ActionModel)
        .filter(ActionModel.person_id == scoped_person_id, ActionModel.status.in_(active_statuses))
        .order_by(ActionModel.created_at.desc())
        .all()
    )
