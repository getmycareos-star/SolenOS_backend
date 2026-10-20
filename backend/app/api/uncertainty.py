from fastapi import APIRouter, Depends, HTTPException, Request
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
from app.core.uncertainty_enums import GapLifecycleStatus, EpistemicState
from app.core.security import (
    AuthenticatedPrincipal,
    get_current_principal,
    require_authenticated,
    require_care_context_access,
)
from app.models.uncertainty import (
    InformationGap as InformationGapModel,
    OpenQuestion as OpenQuestionModel,
    Contradiction as ContradictionModel,
    UncertaintyAssessment as UncertaintyAssessmentModel,
)

async def authorize_uncertainty_context(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    """Authenticate and authorize the database-owned care context for this request."""
    require_authenticated(principal)

    payload = {}
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}

    supplied_contexts = [
        value for value in (
            request.path_params.get("person_id"),
            request.query_params.get("person_id"),
            payload.get("person_id"),
        )
        if value not in (None, "")
    ]

    resource_models = (
        ("gap_id", InformationGapModel),
        ("question_id", OpenQuestionModel),
        ("contradiction_id", ContradictionModel),
        ("assessment_id", UncertaintyAssessmentModel),
    )
    owner_person_id = None
    for parameter, model in resource_models:
        resource_id = (
            request.path_params.get(parameter)
            or request.query_params.get(parameter)
            or payload.get(parameter)
        )
        if not resource_id:
            continue
        resource = db.query(model).filter(model.id == resource_id).first()
        if resource is None:
            raise HTTPException(status_code=404, detail="Uncertainty resource not found")
        owner_person_id = getattr(resource, "person_id", None)
        if not owner_person_id:
            raise HTTPException(status_code=404, detail="Uncertainty resource has no care context")
        break

    if owner_person_id:
        # Never trust a caller-supplied person_id over the resource's stored owner.
        if any(str(value) != str(owner_person_id) for value in supplied_contexts):
            raise HTTPException(status_code=403, detail="Care context does not match resource ownership")
        person_id = str(owner_person_id)
    else:
        person_id = str(supplied_contexts[0]) if supplied_contexts else ""

        # Conflicting path/query/body person IDs are ambiguous and must not be
        # resolved by choosing whichever value happens to be checked first.
        if any(str(value) != person_id for value in supplied_contexts):
            raise HTTPException(status_code=403, detail="Conflicting care contexts are not allowed")

    if not person_id:
        raise HTTPException(
            status_code=400,
            detail="A care context is required for uncertainty access.",
        )
    require_care_context_access(principal, person_id, "uncertainty access")


router = APIRouter(prefix="/uncertainty", tags=["uncertainty"], dependencies=[Depends(authorize_uncertainty_context)])


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
