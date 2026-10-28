from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
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
from app.services.uncertainty_intelligence import analyze_uncertainty
from app.schemas.uncertainty import UncertaintyAnalysisRequest, UncertaintyAnalysisResponse
from app.core.uncertainty_enums import GapLifecycleStatus, EpistemicState

router = APIRouter(prefix="/uncertainty", tags=["uncertainty"])


def get_engine(db: Session = Depends(get_db)) -> UncertaintyEngine:
    return UncertaintyEngine(db)


@router.post("/gaps", response_model=InformationGap)
def create_gap(gap: InformationGapCreate, engine: UncertaintyEngine = Depends(get_engine)):
    return engine.create_information_gap(gap)


@router.get("/gaps/{person_id}", response_model=List[InformationGap])
def list_gaps(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    status: Optional[GapLifecycleStatus] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
    return engine.list_information_gaps(
        person_id, subject_type, subject_id, status
    )


@router.patch("/gaps/{gap_id}", response_model=InformationGap)
def update_gap(
    gap_id: str,
    update: InformationGapUpdate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    result = engine.update_information_gap(gap_id, update)
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
    return engine.list_open_questions(person_id, subject_type, subject_id, status)


@router.patch("/questions/{question_id}", response_model=OpenQuestion)
def update_question(
    question_id: str,
    update: OpenQuestionUpdate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    result = engine.update_open_question(question_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Open question not found")
    return result


@router.post("/contradictions", response_model=Contradiction)
def create_contradiction(
    contradiction: ContradictionCreate, engine: UncertaintyEngine = Depends(get_engine)
):
    return engine.create_contradiction(contradiction)


@router.get("/contradictions/{person_id}", response_model=List[Contradiction])
def list_contradictions(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    resolution_status: Optional[str] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
    return engine.list_contradictions(
        person_id, subject_type, subject_id, resolution_status
    )


@router.patch("/contradictions/{contradiction_id}", response_model=Contradiction)
def update_contradiction(
    contradiction_id: str,
    update: ContradictionUpdate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    result = engine.update_contradiction(contradiction_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    return result


@router.post("/assessments", response_model=UncertaintyAssessment)
def create_assessment(
    assessment: UncertaintyAssessmentCreate, engine: UncertaintyEngine = Depends(get_engine)
):
    return engine.create_uncertainty_assessment(assessment)


@router.get("/assessments/{person_id}", response_model=List[UncertaintyAssessment])
def list_assessments(
    person_id: str,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    epistemic_state: Optional[EpistemicState] = None,
    engine: UncertaintyEngine = Depends(get_engine),
):
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
    return engine.get_epistemic_view(person_id, subject_type, subject_id)


@router.get("/summary/{person_id}/{subject_type}/{subject_id}", response_model=SubjectUncertaintySummary)
def get_subject_summary(
    person_id: str,
    subject_type: str,
    subject_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    return engine.get_subject_summary(person_id, subject_type, subject_id)


@router.post("/analyze", response_model=UncertaintyAnalysisResponse)
def analyze_and_persist_uncertainty(
    request: UncertaintyAnalysisRequest,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Analyze a real workflow claim and persist the epistemic result.

    The endpoint does not treat supplied prose as verified evidence and never emits
    a calibrated probability. Evidence retrieval and person authorization must be
    performed by the authenticated calling workflow before invoking this route.
    """
    try:
        analysis = analyze_uncertainty(
            claim=request.claim,
            task=request.task,
            evidence=request.evidence,
            required_fields=request.required_fields,
            unresolved_conflicts=request.unresolved_conflicts,
            source_access=request.source_access,
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
    evidence_ids = [
        str(item["id"]) for item in request.evidence
        if isinstance(item, dict) and item.get("id") is not None
    ]
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
            assessed_by_caregiver_id=request.assessed_by_caregiver_id,
        )
    )
    return {
        "assessment_id": persisted.id,
        "person_id": persisted.person_id,
        "subject_type": persisted.subject_type,
        "subject_id": persisted.subject_id,
        "field": persisted.field,
        "analysis": analysis,
    }
