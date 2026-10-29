from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.models.care import Evidence
from app.models.uncertainty import Contradiction as ContradictionModel, OpenQuestion as OpenQuestionModel
from app.schemas.uncertainty import (
    InformationGapCreate,
    InformationGapUpdate,
    InformationGap,
    OpenQuestionCreate,
    OpenQuestionUpdate,
    OpenQuestion,
    ContradictionCreate,
    ContradictionUpdate,
    Contradiction,
    UncertaintyAssessmentCreate,
    UncertaintyAssessment,
    EpistemicView,
    SubjectUncertaintySummary,
)
from app.services.uncertainty import UncertaintyEngine
from app.services.uncertainty_intelligence import analyze_uncertainty, evidence_record_to_uncertainty_item
from app.schemas.uncertainty import UncertaintyAnalysisRequest, UncertaintyAnalysisResponse
from app.core.uncertainty_enums import GapLifecycleStatus, EpistemicState, Priority
from app.api.care_delta import _scoped_identity

router = APIRouter(prefix="/uncertainty", tags=["uncertainty"])


def get_engine(request: Request, db: Session = Depends(get_db)) -> UncertaintyEngine:
    # Derive scope from the server-validated bearer session, never request body IDs.
    person_id, caregiver_id = _scoped_identity(request, db)
    return UncertaintyEngine(db, person_id=person_id, caregiver_id=caregiver_id)

def _require_person_scope(engine: UncertaintyEngine, person_id: str) -> None:
    if engine.person_id is not None and person_id != engine.person_id:
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")


@router.post("/gaps", response_model=InformationGap)
def create_gap(gap: InformationGapCreate, engine: UncertaintyEngine = Depends(get_engine)):
    try:
        return engine.create_information_gap(gap)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/gaps/{person_id}", response_model=List[InformationGap])
def list_gaps(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    status: Optional[GapLifecycleStatus] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
    _require_person_scope(engine, person_id)
    return engine.list_information_gaps(
        person_id, subject_type, subject_id, status
    )


@router.patch("/gaps/{gap_id}", response_model=InformationGap)
def update_gap(
    gap_id: str,
    update: InformationGapUpdate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    try:
        result = engine.update_information_gap(gap_id, update)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Information gap not found")
    return result


@router.post("/questions", response_model=OpenQuestion)
def create_question(question: OpenQuestionCreate, engine: UncertaintyEngine = Depends(get_engine)):
    return engine.create_open_question(question)


@router.get("/questions/{person_id}", response_model=List[OpenQuestion])
def list_questions(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    status: Optional[str] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
    _require_person_scope(engine, person_id)
    return engine.list_open_questions(person_id, subject_type, subject_id, status)


@router.patch("/questions/{question_id}", response_model=OpenQuestion)
def update_question(
    question_id: str,
    update: OpenQuestionUpdate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    try:
        result = engine.update_open_question(question_id, update)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Open question not found")
    return result


@router.post("/contradictions", response_model=Contradiction)
def create_contradiction(
    contradiction: ContradictionCreate, engine: UncertaintyEngine = Depends(get_engine)
):
    try:
        return engine.create_contradiction(contradiction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/contradictions/{person_id}", response_model=List[Contradiction])
def list_contradictions(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    resolution_status: Optional[str] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
    _require_person_scope(engine, person_id)
    return engine.list_contradictions(
        person_id, subject_type, subject_id, resolution_status
    )


@router.patch("/contradictions/{contradiction_id}", response_model=Contradiction)
def update_contradiction(
    contradiction_id: str,
    update: ContradictionUpdate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    try:
        result = engine.update_contradiction(contradiction_id, update)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    return result


@router.get("/contradictions/{contradiction_id}/history")
def list_contradiction_resolution_history(
    contradiction_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    conflict = engine._target_query(ContradictionModel, contradiction_id).first()
    if not conflict:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    return engine.list_contradiction_resolution_events(contradiction_id)


@router.post("/assessments", response_model=UncertaintyAssessment)
def create_assessment(
    assessment: UncertaintyAssessmentCreate, engine: UncertaintyEngine = Depends(get_engine)
):
    if assessment.confidence is not None:
        raise HTTPException(
            status_code=422,
            detail="Numeric confidence is unavailable until a calibrated assessment method is configured.",
        )
    if assessment.epistemic_state == EpistemicState.KNOWN:
        evidence_ids = list(dict.fromkeys(assessment.evidence_ids or []))
        if not evidence_ids:
            raise HTTPException(
                status_code=422,
                detail="A known assessment requires linked evidence; a populated form alone is insufficient.",
            )
        if len(evidence_ids) != len(assessment.evidence_ids or []):
            raise HTTPException(status_code=422, detail="Duplicate evidence IDs are not allowed.")
        count = (
            engine.db.query(Evidence)
            .filter(
                Evidence.person_id == engine.person_id,
                Evidence.id.in_(evidence_ids),
            )
            .count()
        )
        if count != len(evidence_ids):
            raise HTTPException(
                status_code=422,
                detail="Every evidence reference must exist and belong to the authenticated care record.",
            )
    return engine.create_uncertainty_assessment(assessment)


@router.get("/assessments/{person_id}", response_model=List[UncertaintyAssessment])
def list_assessments(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    epistemic_state: Optional[EpistemicState] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
    _require_person_scope(engine, person_id)
    return engine.list_assessments(
        person_id, subject_type, subject_id, epistemic_state
    )


@router.get("/view/{person_id}/{subject_type}/{subject_id}", response_model=EpistemicView)
def get_epistemic_view(
    person_id: str,
    subject_type: str,
    subject_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    _require_person_scope(engine, person_id)
    return engine.get_epistemic_view(person_id, subject_type, subject_id)


@router.get("/summary/{person_id}/{subject_type}/{subject_id}", response_model=SubjectUncertaintySummary)
def get_subject_summary(
    person_id: str,
    subject_type: str,
    subject_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    _require_person_scope(engine, person_id)
    return engine.get_subject_summary(person_id, subject_type, subject_id)


@router.post("/analyze", response_model=UncertaintyAnalysisResponse)
def analyze_and_persist_uncertainty(
    request: UncertaintyAnalysisRequest,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Analyze a real workflow claim and persist the epistemic result.

    The endpoint resolves evidence and conflict IDs from person-scoped persisted
    records, persists the assessment, and never emits a calibrated probability.
    Session scope is derived by get_engine from the server-validated bearer token.
    """
    if engine.person_id is None or str(request.person_id) != str(engine.person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if len(set(request.conflict_ids)) != len(request.conflict_ids):
        raise HTTPException(status_code=422, detail="Duplicate conflict IDs are not allowed")
    if len(set(request.evidence_ids)) != len(request.evidence_ids):
        raise HTTPException(status_code=422, detail="Duplicate evidence IDs are not allowed")

    requested_conflict_ids = list(request.conflict_ids)
    if requested_conflict_ids:
        conflict_rows = (
            engine.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == request.person_id,
                ContradictionModel.subject_type == request.subject_type,
                ContradictionModel.subject_id == request.subject_id,
                ContradictionModel.field == request.field,
                ContradictionModel.id.in_(requested_conflict_ids),
            )
            .all()
        )
        if len(conflict_rows) != len(requested_conflict_ids):
            raise HTTPException(status_code=404, detail="One or more conflict records were not found for this person")
    else:
        conflict_rows = []

    requested_evidence_ids = list(request.evidence_ids)
    requested_baseline_ids = list(request.baseline_evidence_ids)
    if len(requested_baseline_ids) != len(set(requested_baseline_ids)):
        raise HTTPException(status_code=422, detail="Duplicate baseline evidence IDs are not allowed.")
    if not set(requested_baseline_ids).issubset(set(requested_evidence_ids)):
        raise HTTPException(status_code=422, detail="Every baseline evidence ID must also be included in evidence_ids.")
    if requested_evidence_ids:
        evidence_rows = (
            engine.db.query(Evidence)
            .filter(
                Evidence.person_id == request.person_id,
                Evidence.id.in_(requested_evidence_ids),
            )
            .all()
        )
        if len(evidence_rows) != len(requested_evidence_ids):
            raise HTTPException(status_code=404, detail="One or more evidence records were not found for this person")
    else:
        evidence_rows = []

    try:
        analysis = analyze_uncertainty(
            claim=request.claim,
            task=request.task,
            evidence=[
                evidence_record_to_uncertainty_item(
                    row,
                    role="baseline" if row.id in requested_baseline_ids else "current",
                    is_baseline=row.id in requested_baseline_ids,
                )
                for row in evidence_rows
            ],
            required_fields=request.required_fields,
            unresolved_conflicts=[{"id": row.id, "field": row.field, "description": row.description, "evidence_ids": row.evidence_ids, "status": row.resolution_status} for row in conflict_rows if row.resolution_status != "resolved"],
            source_access="available",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from app.schemas.uncertainty import UncertaintyAssessmentCreate
    from app.core.uncertainty_enums import EpistemicState, GapReason

    state = EpistemicState(analysis["epistemic_state"])
    reason_by_classification = {
        "missing_information": GapReason.NOT_DOCUMENTED,
        "possible_reference_ambiguity": GapReason.AMBIGUOUS,
        "conflicting_evidence": GapReason.CONFLICTING,
        "insufficient_evidence": GapReason.INSUFFICIENT_EVIDENCE,
        "outdated_information": GapReason.OUTDATED,
        "knowledge_boundary": GapReason.INACCESSIBLE,
        "source_verification_uncertainty": GapReason.LOW_QUALITY,
        "causal_inference_review": GapReason.INSUFFICIENT_EVIDENCE,
        "temporal_uncertainty": GapReason.MISSING_CONTEXT,
    }
    gap_reason = next(
        (reason_by_classification[item] for item in analysis["classifications"] if item in reason_by_classification),
        None,
    )
    evidence_ids = [row.id for row in evidence_rows]
    persisted = engine.create_uncertainty_assessment(
        UncertaintyAssessmentCreate(
            person_id=request.person_id,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            field=request.field,
            epistemic_state=state,
            confidence=None,
            known_value=None,
            gap_reason=gap_reason,
            context=[analysis],
            evidence_ids=evidence_ids or None,
        ),
        commit=False,
    )
    try:
        # Clarification questions are durable care-continuity records, not merely
        # text in an API response. Deduplicate against existing open questions and
        # commit them atomically with the assessment/supersession transition.
        for question_data in analysis.get("clarification_questions", []):
            question_text = str(question_data.get("question") or "").strip()
            if not question_text:
                continue
            target_field = str(question_data.get("target_field") or request.field)
            priority_value = str(question_data.get("priority") or "medium").lower()
            priority = Priority.HIGH if priority_value == "high" else (
                Priority.CRITICAL if priority_value == "critical" else Priority.MEDIUM
            )
            existing_question = engine.db.query(OpenQuestionModel).filter(
                OpenQuestionModel.person_id == request.person_id,
                OpenQuestionModel.subject_type == request.subject_type,
                OpenQuestionModel.subject_id == request.subject_id,
                OpenQuestionModel.question == question_text,
                OpenQuestionModel.status == "open",
            ).first()
            context_link = {
                "origin": "uncertainty_analysis",
                "assessment_id": persisted.id,
                "target_field": target_field,
                "reason": str(question_data.get("reason") or "Clarification required by the uncertainty assessment."),
            }
            if existing_question is not None:
                existing_context = list(existing_question.context or [])
                if not any(
                    isinstance(item, dict) and item.get("assessment_id") == persisted.id
                    for item in existing_context
                ):
                    existing_context.append(context_link)
                    existing_question.context = existing_context
                continue
            engine.db.add(OpenQuestionModel(
                person_id=request.person_id,
                question=question_text,
                subject_type=request.subject_type,
                subject_id=request.subject_id,
                status="open",
                priority=priority,
                evidence_ids=None,
                context=[context_link],
                created_by_caregiver_id=engine.caregiver_id,
            ))
        engine.db.commit()
        engine.db.refresh(persisted)
    except Exception:
        engine.db.rollback()
        raise
    return {
        "assessment_id": persisted.id,
        "person_id": persisted.person_id,
        "subject_type": persisted.subject_type,
        "subject_id": persisted.subject_id,
        "field": persisted.field,
        "analysis": analysis,
    }
