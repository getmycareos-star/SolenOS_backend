from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from app.core.database import get_db
from app.core.security import get_current_principal
from app.core.security import AuthenticatedPrincipal, require_authenticated, require_care_context_access
from app.models.causal import (
    CausalHypothesis as CausalHypothesisModel,
    CausalEvidence as CausalEvidenceModel,
    AlternativeExplanation as AlternativeExplanationModel,
    Confounder as ConfounderModel,
)
from app.core.causal_enums import (
    CausalRelationshipStatus,
    EvidenceStrength,
    EvidenceRole,
)
from app.schemas.causal import (
    CausalHypothesis,
    CausalHypothesisCreate,
    CausalHypothesisUpdate,
    CausalEvidence,
    CausalEvidenceCreate,
    AlternativeExplanation,
    AlternativeExplanationCreate,
    AlternativeExplanationUpdate,
    Confounder,
    ConfounderCreate,
    CausalAnalysisResult,
    TemporalAssociation,
)
from app.services.causal_reasoning import CausalReasoningEngine

async def authorize_causal_request(
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> None:
    """Resolve the owning person and enforce authorization for every causal route."""
    require_authenticated(principal)
    person_id = request.path_params.get("person_id") or request.query_params.get("person_id")
    payload = {}
    try:
        body = await request.json()
        if isinstance(body, dict):
            payload = body
    except Exception:
        pass
    if not person_id:
        person_id = payload.get("person_id")

    hypothesis_id = request.path_params.get("hypothesis_id") or payload.get("hypothesis_id")
    if not person_id and hypothesis_id:
        row = db.query(CausalHypothesisModel).filter(CausalHypothesisModel.id == hypothesis_id).first()
        if row:
            person_id = row.person_id

    explanation_id = request.path_params.get("explanation_id")
    if not person_id and explanation_id:
        row = db.query(AlternativeExplanationModel).filter(AlternativeExplanationModel.id == explanation_id).first()
        if row:
            person_id = row.person_id

    confounder_id = request.path_params.get("confounder_id")
    if not person_id and confounder_id:
        row = db.query(ConfounderModel).filter(ConfounderModel.id == confounder_id).first()
        if row:
            person_id = row.person_id

    if not person_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Person context is required for causal reasoning",
        )
    require_care_context_access(principal, str(person_id), "causal reasoning")


router = APIRouter(
    prefix="/causal",
    tags=["causal"],
    dependencies=[Depends(authorize_causal_request)],
)


def get_engine(db: Session = Depends(get_db)) -> CausalReasoningEngine:
    return CausalReasoningEngine(db)


@router.post("/hypotheses", response_model=CausalHypothesis)
def create_hypothesis(
    hypothesis: CausalHypothesisCreate, engine: CausalReasoningEngine = Depends(get_engine)
):
    return engine.create_hypothesis(hypothesis)


@router.get("/hypotheses/{person_id}", response_model=List[CausalHypothesis])
def list_hypotheses(
    person_id: str,
    status: Optional[CausalRelationshipStatus] = None,
    is_active: bool = True,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    return engine.list_hypotheses(person_id, status, is_active)


@router.get("/hypotheses/item/{hypothesis_id}", response_model=CausalHypothesis)
def get_hypothesis(
    hypothesis_id: str, engine: CausalReasoningEngine = Depends(get_engine)
):
    result = engine.get_hypothesis(hypothesis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.patch("/hypotheses/{hypothesis_id}", response_model=CausalHypothesis)
def update_hypothesis(
    hypothesis_id: str,
    update: CausalHypothesisUpdate,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    result = engine.update_hypothesis(hypothesis_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.post("/hypotheses/{hypothesis_id}/reassess", response_model=CausalHypothesis)
def reassess_hypothesis(
    hypothesis_id: str,
    caregiver_id: Optional[str] = None,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    result = engine.reassess_hypothesis(hypothesis_id, caregiver_id)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.post("/evidence", response_model=CausalEvidence)
def add_evidence(
    evidence: CausalEvidenceCreate, engine: CausalReasoningEngine = Depends(get_engine)
):
    return engine.add_evidence(evidence)


@router.get("/evidence/{hypothesis_id}", response_model=List[CausalEvidence])
def list_evidence(
    hypothesis_id: str,
    role: Optional[EvidenceRole] = None,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    return engine.list_evidence(hypothesis_id, role)


@router.post("/alternatives", response_model=AlternativeExplanation)
def add_alternative_explanation(
    explanation: AlternativeExplanationCreate, engine: CausalReasoningEngine = Depends(get_engine)
):
    return engine.add_alternative_explanation(explanation)


@router.patch("/alternatives/{explanation_id}", response_model=AlternativeExplanation)
def update_alternative_explanation(
    explanation_id: str,
    update: AlternativeExplanationUpdate,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    result = engine.update_alternative_explanation(explanation_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Alternative explanation not found")
    return result


@router.post("/confounders", response_model=Confounder)
def add_confounder(
    confounder: ConfounderCreate, engine: CausalReasoningEngine = Depends(get_engine)
):
    return engine.add_confounder(confounder)


@router.get("/trace/{hypothesis_id}")
def get_evidence_trace(
    hypothesis_id: str, engine: CausalReasoningEngine = Depends(get_engine)
):
    result = engine.get_evidence_trace(hypothesis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.post("/temporal-associations/{person_id}", response_model=List[TemporalAssociation])
def identify_temporal_associations(
    person_id: str,
    reference_event_id: Optional[str] = None,
    window_days: int = 14,
    cause_types: Optional[List[str]] = None,
    outcome_types: Optional[List[str]] = None,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    window = timedelta(days=window_days)
    return engine.identify_temporal_associations(
        person_id, reference_event_id, window, cause_types, outcome_types
    )


@router.post("/assess", response_model=CausalAnalysisResult)
def assess_relationship(
    person_id: str,
    potential_cause_type: str,
    potential_cause_description: str,
    outcome_type: str,
    outcome_description: str,
    cause_time: Optional[datetime] = None,
    outcome_time: Optional[datetime] = None,
    evidence_ids: Optional[List[str]] = None,
    evidence_strength: EvidenceStrength = EvidenceStrength.WEAK,
    caregiver_id: Optional[str] = None,
    window_days: int = 14,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    window = timedelta(days=window_days)
    return engine.assess_relationship(
        person_id=person_id,
        potential_cause_type=potential_cause_type,
        potential_cause_id=None,
        potential_cause_description=potential_cause_description,
        outcome_type=outcome_type,
        outcome_id=None,
        outcome_description=outcome_description,
        cause_time=cause_time,
        outcome_time=outcome_time,
        evidence_ids=evidence_ids,
        evidence_strength=evidence_strength,
        caregiver_id=caregiver_id,
        window=window,
    )


@router.post("/assess-and-persist", response_model=CausalHypothesis)
def assess_and_persist(
    person_id: str,
    potential_cause_type: str,
    potential_cause_description: str,
    outcome_type: str,
    outcome_description: str,
    cause_time: Optional[datetime] = None,
    outcome_time: Optional[datetime] = None,
    evidence_ids: Optional[List[str]] = None,
    evidence_strength: EvidenceStrength = EvidenceStrength.WEAK,
    caregiver_id: Optional[str] = None,
    window_days: int = 14,
    engine: CausalReasoningEngine = Depends(get_engine),
):
    window = timedelta(days=window_days)
    analysis = engine.assess_relationship(
        person_id=person_id,
        potential_cause_type=potential_cause_type,
        potential_cause_id=None,
        potential_cause_description=potential_cause_description,
        outcome_type=outcome_type,
        outcome_id=None,
        outcome_description=outcome_description,
        cause_time=cause_time,
        outcome_time=outcome_time,
        evidence_ids=evidence_ids,
        evidence_strength=evidence_strength,
        caregiver_id=caregiver_id,
        window=window,
    )
    return engine.create_hypothesis_from_analysis(person_id, analysis, caregiver_id)
