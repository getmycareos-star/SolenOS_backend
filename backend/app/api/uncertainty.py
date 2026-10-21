from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.api.authentication import current_account
from app.core.authz import assert_person_access, require_person_access
from app.models.auth_identity import AuthAccount
from app.models.uncertainty import InformationGap as InformationGapModel, OpenQuestion as OpenQuestionModel, Contradiction as ContradictionModel
from app.schemas.uncertainty import (
    InformationGapCreate, InformationGapUpdate, InformationGap,
    OpenQuestionCreate, OpenQuestionUpdate, OpenQuestion,
    ContradictionCreate, ContradictionUpdate, Contradiction,
    UncertaintyAssessmentCreate, UncertaintyAssessment,
    EpistemicView, SubjectUncertaintySummary,
)
from app.services.uncertainty import UncertaintyEngine
from app.core.uncertainty_enums import GapLifecycleStatus, EpistemicState

router = APIRouter(prefix="/uncertainty", tags=["uncertainty"], dependencies=[Depends(current_account)])


def get_engine(db: Session = Depends(get_db)) -> UncertaintyEngine:
    return UncertaintyEngine(db)


@router.post("/gaps", response_model=InformationGap)
def create_gap(gap: InformationGapCreate, engine: UncertaintyEngine = Depends(get_engine),
               db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, gap.person_id, "write")
    return engine.create_information_gap(gap)


@router.get("/gaps/{person_id}", response_model=List[InformationGap],
            dependencies=[Depends(require_person_access("read"))])
def list_gaps(person_id: str, subject_type: Optional[str] = None, subject_id: Optional[str] = None,
              status: Optional[GapLifecycleStatus] = None, engine: UncertaintyEngine = Depends(get_engine)):
    return engine.list_information_gaps(person_id, subject_type, subject_id, status)


@router.patch("/gaps/{gap_id}", response_model=InformationGap)
def update_gap(gap_id: str, update: InformationGapUpdate, engine: UncertaintyEngine = Depends(get_engine),
               db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    record = db.query(InformationGapModel).filter(InformationGapModel.id == gap_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Information gap not found")
    assert_person_access(db, account.id, record.person_id, "write")
    result = engine.update_information_gap(gap_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Information gap not found")
    return result


@router.post("/questions", response_model=OpenQuestion)
def create_question(question: OpenQuestionCreate, engine: UncertaintyEngine = Depends(get_engine),
                    db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, question.person_id, "write")
    return engine.create_open_question(question)


@router.get("/questions/{person_id}", response_model=List[OpenQuestion],
            dependencies=[Depends(require_person_access("read"))])
def list_questions(person_id: str, subject_type: Optional[str] = None, subject_id: Optional[str] = None,
                   status: Optional[str] = None, engine: UncertaintyEngine = Depends(get_engine)):
    return engine.list_open_questions(person_id, subject_type, subject_id, status)


@router.patch("/questions/{question_id}", response_model=OpenQuestion)
def update_question(question_id: str, update: OpenQuestionUpdate, engine: UncertaintyEngine = Depends(get_engine),
                    db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    record = db.query(OpenQuestionModel).filter(OpenQuestionModel.id == question_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Open question not found")
    assert_person_access(db, account.id, record.person_id, "write")
    result = engine.update_open_question(question_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Open question not found")
    return result


@router.post("/contradictions", response_model=Contradiction)
def create_contradiction(contradiction: ContradictionCreate, engine: UncertaintyEngine = Depends(get_engine),
                         db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, contradiction.person_id, "write")
    return engine.create_contradiction(contradiction)


@router.get("/contradictions/{person_id}", response_model=List[Contradiction],
            dependencies=[Depends(require_person_access("read"))])
def list_contradictions(person_id: str, subject_type: Optional[str] = None, subject_id: Optional[str] = None,
                        resolution_status: Optional[str] = None, engine: UncertaintyEngine = Depends(get_engine)):
    return engine.list_contradictions(person_id, subject_type, subject_id, resolution_status)


@router.patch("/contradictions/{contradiction_id}", response_model=Contradiction)
def update_contradiction(contradiction_id: str, update: ContradictionUpdate,
                         engine: UncertaintyEngine = Depends(get_engine), db: Session = Depends(get_db),
                         account: AuthAccount = Depends(current_account)):
    record = db.query(ContradictionModel).filter(ContradictionModel.id == contradiction_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    assert_person_access(db, account.id, record.person_id, "write")
    result = engine.update_contradiction(contradiction_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Contradiction not found")
    return result


@router.post("/assessments", response_model=UncertaintyAssessment)
def create_assessment(assessment: UncertaintyAssessmentCreate, engine: UncertaintyEngine = Depends(get_engine),
                      db: Session = Depends(get_db), account: AuthAccount = Depends(current_account)):
    assert_person_access(db, account.id, assessment.person_id, "write")
    return engine.create_uncertainty_assessment(assessment)


@router.get("/assessments/{person_id}", response_model=List[UncertaintyAssessment],
            dependencies=[Depends(require_person_access("read"))])
def list_assessments(person_id: str, subject_type: Optional[str] = None, subject_id: Optional[str] = None,
                     epistemic_state: Optional[EpistemicState] = None, engine: UncertaintyEngine = Depends(get_engine)):
    return engine.list_assessments(person_id, subject_type, subject_id, epistemic_state)


@router.get("/view/{person_id}/{subject_type}/{subject_id}", response_model=EpistemicView,
            dependencies=[Depends(require_person_access("read"))])
def get_epistemic_view(person_id: str, subject_type: str, subject_id: str,
                       engine: UncertaintyEngine = Depends(get_engine)):
    return engine.get_epistemic_view(person_id, subject_type, subject_id)


@router.get("/summary/{person_id}/{subject_type}/{subject_id}", response_model=SubjectUncertaintySummary,
            dependencies=[Depends(require_person_access("read"))])
def get_subject_summary(person_id: str, subject_type: str, subject_id: str,
                        engine: UncertaintyEngine = Depends(get_engine)):
    return engine.get_subject_summary(person_id, subject_type, subject_id)
