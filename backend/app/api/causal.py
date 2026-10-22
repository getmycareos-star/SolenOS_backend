from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from app.core.database import get_db
from app.api.authentication import current_account
from app.core.authz import assert_person_access, require_person_access
from app.models.auth_identity import AuthAccount
from app.models.causal import (
    CausalHypothesis as CausalHypothesisModel,
    CausalEvidence as CausalEvidenceModel,
    AlternativeExplanation as AlternativeExplanationModel,
    Confounder as ConfounderModel,
)
from app.core.causal_enums import CausalRelationshipStatus, EvidenceStrength, EvidenceRole
from app.schemas.causal import (
    CausalHypothesis, CausalHypothesisCreate, CausalHypothesisUpdate,
    CausalEvidence, CausalEvidenceCreate, AlternativeExplanation,
    AlternativeExplanationCreate, AlternativeExplanationUpdate,
    Confounder, ConfounderCreate, CausalAnalysisResult, TemporalAssociation,
)
from app.services.causal_reasoning import CausalReasoningEngine

router = APIRouter(prefix="/causal", tags=["causal"])


def get_engine(db: Session = Depends(get_db)) -> CausalReasoningEngine:
    return CausalReasoningEngine(db)


def _load_hypothesis(db: Session, hypothesis_id: str) -> CausalHypothesisModel:
    hypothesis = db.query(CausalHypothesisModel).filter(CausalHypothesisModel.id == hypothesis_id).first()
    if hypothesis is None:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return hypothesis


@router.post("/hypotheses", response_model=CausalHypothesis)
def create_hypothesis(hypothesis: CausalHypothesisCreate, engine: CausalReasoningEngine = Depends(get_engine),
                      db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, hypothesis.person_id, "write")
    # Caregiver attribution must not be supplied by the caller.
    trusted_payload = hypothesis.model_copy(update={"created_by_caregiver_id": None, "updated_by_caregiver_id": None})
    return engine.create_hypothesis(trusted_payload)


@router.get("/hypotheses/{person_id}", response_model=List[CausalHypothesis],
            dependencies=[Depends(require_person_access("read"))])
def list_hypotheses(person_id: str, status: Optional[CausalRelationshipStatus] = None, is_active: bool = True,
                    engine: CausalReasoningEngine = Depends(get_engine)):
    return engine.list_hypotheses(person_id, status, is_active)


@router.get("/hypotheses/item/{hypothesis_id}", response_model=CausalHypothesis)
def get_hypothesis(hypothesis_id: str, engine: CausalReasoningEngine = Depends(get_engine),
                   db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    hypothesis = _load_hypothesis(db, hypothesis_id)
    assert_person_access(db, account.id, hypothesis.person_id, "read")
    return engine.get_hypothesis(hypothesis_id)


@router.patch("/hypotheses/{hypothesis_id}", response_model=CausalHypothesis)
def update_hypothesis(hypothesis_id: str, update: CausalHypothesisUpdate,
                      engine: CausalReasoningEngine = Depends(get_engine), db: Session = Depends(get_db),
                      account: AuthAccount = Depends(current_account)):
    hypothesis = _load_hypothesis(db, hypothesis_id)
    assert_person_access(db, account.id, hypothesis.person_id, "write")
    trusted_update = update.model_copy(update={"updated_by_caregiver_id": None})
    result = engine.update_hypothesis(hypothesis_id, trusted_update)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.post("/hypotheses/{hypothesis_id}/reassess", response_model=CausalHypothesis)
def reassess_hypothesis(hypothesis_id: str, engine: CausalReasoningEngine = Depends(get_engine),
                        db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    hypothesis = _load_hypothesis(db, hypothesis_id)
    assert_person_access(db, account.id, hypothesis.person_id, "write")
    result = engine.reassess_hypothesis(hypothesis_id, caregiver_id=None)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.post("/evidence", response_model=CausalEvidence)
def add_evidence(evidence: CausalEvidenceCreate, engine: CausalReasoningEngine = Depends(get_engine),
                 db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, evidence.person_id, "write")
    hypothesis = _load_hypothesis(db, evidence.hypothesis_id)
    if hypothesis.person_id != evidence.person_id:
        raise HTTPException(status_code=400, detail="Evidence person does not match hypothesis person")
    trusted_payload = evidence.model_copy(update={"caregiver_id": None})
    return engine.add_evidence(trusted_payload)


@router.get("/evidence/{hypothesis_id}", response_model=List[CausalEvidence])
def list_evidence(hypothesis_id: str, role: Optional[EvidenceRole] = None,
                  engine: CausalReasoningEngine = Depends(get_engine), db: Session = Depends(get_db),
                  account: AuthAccount = Depends(current_account)):
    hypothesis = _load_hypothesis(db, hypothesis_id)
    assert_person_access(db, account.id, hypothesis.person_id, "read")
    return engine.list_evidence(hypothesis_id, role)


@router.post("/alternatives", response_model=AlternativeExplanation)
def add_alternative_explanation(explanation: AlternativeExplanationCreate,
                                engine: CausalReasoningEngine = Depends(get_engine),
                                db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, explanation.person_id, "write")
    hypothesis = _load_hypothesis(db, explanation.hypothesis_id)
    if hypothesis.person_id != explanation.person_id:
        raise HTTPException(status_code=400, detail="Alternative person does not match hypothesis person")
    trusted_payload = explanation.model_copy(update={"created_by_caregiver_id": None})
    return engine.add_alternative_explanation(trusted_payload)


@router.patch("/alternatives/{explanation_id}", response_model=AlternativeExplanation)
def update_alternative_explanation(explanation_id: str, update: AlternativeExplanationUpdate,
                                   engine: CausalReasoningEngine = Depends(get_engine), db: Session = Depends(get_db),
                                   account: AuthAccount = Depends(current_account)):
    record = db.query(AlternativeExplanationModel).filter(AlternativeExplanationModel.id == explanation_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Alternative explanation not found")
    assert_person_access(db, account.id, record.person_id, "write")
    result = engine.update_alternative_explanation(explanation_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Alternative explanation not found")
    return result


@router.post("/confounders", response_model=Confounder)
def add_confounder(confounder: ConfounderCreate, engine: CausalReasoningEngine = Depends(get_engine),
                   db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, confounder.person_id, "write")
    hypothesis = _load_hypothesis(db, confounder.hypothesis_id)
    if hypothesis.person_id != confounder.person_id:
        raise HTTPException(status_code=400, detail="Confounder person does not match hypothesis person")
    trusted_payload = confounder.model_copy(update={"created_by_caregiver_id": None})
    return engine.add_confounder(trusted_payload)


@router.get("/trace/{hypothesis_id}")
def get_evidence_trace(hypothesis_id: str, engine: CausalReasoningEngine = Depends(get_engine),
                       db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    hypothesis = _load_hypothesis(db, hypothesis_id)
    assert_person_access(db, account.id, hypothesis.person_id, "read")
    result = engine.get_evidence_trace(hypothesis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Causal hypothesis not found")
    return result


@router.post("/temporal-associations/{person_id}", response_model=List[TemporalAssociation],
             dependencies=[Depends(require_person_access("read"))])
def identify_temporal_associations(person_id: str, reference_event_id: Optional[str] = None,
                                   window_days: int = 14, cause_types: Optional[List[str]] = None,
                                   outcome_types: Optional[List[str]] = None,
                                   engine: CausalReasoningEngine = Depends(get_engine)):
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=422, detail="window_days must be between 1 and 365")
    return engine.identify_temporal_associations(person_id, reference_event_id, timedelta(days=window_days),
                                                 cause_types, outcome_types)


@router.post("/assess", response_model=CausalAnalysisResult)
def assess_relationship(person_id: str, potential_cause_type: str, potential_cause_description: str,
                        outcome_type: str, outcome_description: str, cause_time: Optional[datetime] = None,
                        outcome_time: Optional[datetime] = None, evidence_ids: Optional[List[str]] = None,
                        evidence_strength: EvidenceStrength = EvidenceStrength.WEAK, window_days: int = 14,
                        engine: CausalReasoningEngine = Depends(get_engine), db: Session = Depends(get_db),
                        account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, person_id, "read")
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=422, detail="window_days must be between 1 and 365")
    return engine.assess_relationship(
        person_id=person_id, potential_cause_type=potential_cause_type, potential_cause_id=None,
        potential_cause_description=potential_cause_description, outcome_type=outcome_type, outcome_id=None,
        outcome_description=outcome_description, cause_time=cause_time, outcome_time=outcome_time,
        evidence_ids=evidence_ids, evidence_strength=evidence_strength, caregiver_id=None,
        window=timedelta(days=window_days),
    )


@router.post("/assess-and-persist", response_model=CausalHypothesis)
def assess_and_persist(person_id: str, potential_cause_type: str, potential_cause_description: str,
                       outcome_type: str, outcome_description: str, cause_time: Optional[datetime] = None,
                       outcome_time: Optional[datetime] = None, evidence_ids: Optional[List[str]] = None,
                       evidence_strength: EvidenceStrength = EvidenceStrength.WEAK, window_days: int = 14,
                       engine: CausalReasoningEngine = Depends(get_engine), db: Session = Depends(get_db),
                       account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, person_id, "write")
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=422, detail="window_days must be between 1 and 365")
    analysis = engine.assess_relationship(
        person_id=person_id, potential_cause_type=potential_cause_type, potential_cause_id=None,
        potential_cause_description=potential_cause_description, outcome_type=outcome_type, outcome_id=None,
        outcome_description=outcome_description, cause_time=cause_time, outcome_time=outcome_time,
        evidence_ids=evidence_ids, evidence_strength=evidence_strength, caregiver_id=None,
        window=timedelta(days=window_days),
    )
    return engine.create_hypothesis_from_analysis(person_id, analysis, caregiver_id=None)
