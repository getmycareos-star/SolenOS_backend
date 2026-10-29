from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.models.care import Evidence, Person
from app.models.uncertainty import Contradiction as ContradictionModel, OpenQuestion as OpenQuestionModel, OpenQuestionAnswerEvent as OpenQuestionAnswerEventModel
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
from app.schemas.uncertainty import ClaimDependencyCreate, ClaimDependency
from app.core.uncertainty_enums import GapLifecycleStatus, EpistemicState, Priority
from app.api.care_delta import _scoped_identity
from app.models.care_continuity import EvidenceClaimDependency, CareAssertion

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
    try:
        return engine.create_open_question(question)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@router.get("/questions/{question_id}/answer-history")
def list_question_answer_history(
    question_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Read immutable answer versions for a question in the active care record."""
    question = engine.db.query(OpenQuestionModel.id).filter(
        OpenQuestionModel.id == question_id,
        OpenQuestionModel.person_id == engine.person_id,
    ).first()
    if question is None:
        raise HTTPException(status_code=404, detail="Open question not found")
    rows = engine.db.query(OpenQuestionAnswerEventModel).filter(
        OpenQuestionAnswerEventModel.question_id == question_id,
        OpenQuestionAnswerEventModel.person_id == engine.person_id,
    ).order_by(
        OpenQuestionAnswerEventModel.created_at.asc(),
        OpenQuestionAnswerEventModel.id.asc(),
    ).all()
    return [{
        "id": row.id,
        "question_id": row.question_id,
        "person_id": row.person_id,
        "answer": row.answer,
        "answer_provenance": row.answer_provenance,
        "answered_by_caregiver_id": row.answered_by_caregiver_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


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


@router.post("/claims/dependencies", response_model=ClaimDependency)
def create_claim_dependency(
    dependency: ClaimDependencyCreate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Persist a same-person, append-only, acyclic claim dependency."""
    if engine.person_id is None or str(dependency.person_id) != str(engine.person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if engine.caregiver_id is None:
        raise HTTPException(status_code=403, detail="A session-bound reviewer is required")
    if dependency.claim_id == dependency.depends_on_claim_id:
        raise HTTPException(status_code=422, detail="A claim cannot depend on itself")
    allowed_types = {"derived_from", "requires", "supports", "qualifies", "supersedes"}
    if dependency.dependency_type not in allowed_types:
        raise HTTPException(status_code=422, detail=f"dependency_type must be one of: {', '.join(sorted(allowed_types))}")
    rationale = dependency.rationale.strip()
    if not rationale:
        raise HTTPException(status_code=422, detail="A dependency requires a non-empty rationale")

    claims = engine.db.query(CareAssertion.id).filter(
        CareAssertion.person_id == engine.person_id,
        CareAssertion.id.in_([dependency.claim_id, dependency.depends_on_claim_id]),
    ).all()
    if {row[0] for row in claims} != {dependency.claim_id, dependency.depends_on_claim_id}:
        raise HTTPException(status_code=404, detail="Both claims must exist in the authenticated care record")

    # Serialize graph writes per care record on PostgreSQL. Without a parent-row
    # lock, two concurrent inverse-edge requests could each pass a cycle check
    # against the same pre-commit graph and create a cycle together.
    scoped_person = engine.db.query(Person.id).filter(
        Person.id == engine.person_id,
    ).with_for_update().first()
    if scoped_person is None:
        raise HTTPException(status_code=404, detail="Care record not found")

    existing = engine.db.query(EvidenceClaimDependency).filter(
        EvidenceClaimDependency.person_id == engine.person_id,
        EvidenceClaimDependency.claim_id == dependency.claim_id,
        EvidenceClaimDependency.depends_on_claim_id == dependency.depends_on_claim_id,
        EvidenceClaimDependency.dependency_type == dependency.dependency_type,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="This claim dependency already exists")

    edges = engine.db.query(
        EvidenceClaimDependency.claim_id,
        EvidenceClaimDependency.depends_on_claim_id,
    ).filter(EvidenceClaimDependency.person_id == engine.person_id).all()
    graph = {}
    for claim_id, depends_on_claim_id in edges:
        graph.setdefault(claim_id, set()).add(depends_on_claim_id)
    # A -> B means A depends on B. Adding A -> B creates a cycle iff B
    # already reaches A through existing dependency edges.
    pending = [dependency.depends_on_claim_id]
    visited = set()
    while pending:
        node = pending.pop()
        if node == dependency.claim_id:
            raise HTTPException(status_code=422, detail="Claim dependency would create a cycle")
        if node in visited:
            continue
        visited.add(node)
        pending.extend(graph.get(node, ()))

    record = EvidenceClaimDependency(
        person_id=engine.person_id,
        claim_id=dependency.claim_id,
        depends_on_claim_id=dependency.depends_on_claim_id,
        dependency_type=dependency.dependency_type,
        rationale=rationale,
        created_by_caregiver_id=engine.caregiver_id,
    )
    try:
        engine.db.add(record)
        engine.db.commit()
        engine.db.refresh(record)
        return record
    except Exception:
        engine.db.rollback()
        raise


@router.get("/claims/{claim_id}/dependencies")
def list_claim_dependencies(
    claim_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Return both dependencies and dependents for one scoped claim."""
    claim = engine.db.query(CareAssertion.id).filter(
        CareAssertion.id == claim_id,
        CareAssertion.person_id == engine.person_id,
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    rows = engine.db.query(EvidenceClaimDependency).filter(
        EvidenceClaimDependency.person_id == engine.person_id,
        (EvidenceClaimDependency.claim_id == claim_id)
        | (EvidenceClaimDependency.depends_on_claim_id == claim_id),
    ).order_by(EvidenceClaimDependency.created_at.asc(), EvidenceClaimDependency.id.asc()).all()
    return {
        "claim_id": claim_id,
        "dependencies": [ClaimDependency.model_validate(row).model_dump(mode="json") for row in rows if row.claim_id == claim_id],
        "dependents": [ClaimDependency.model_validate(row).model_dump(mode="json") for row in rows if row.depends_on_claim_id == claim_id],
        "notice": "Dependency links record declared relationships; they do not prove semantic entailment or clinical truth.",
    }


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
    for candidate in (*request.assumptions, *request.alternatives):
        if not isinstance(candidate, dict):
            raise HTTPException(status_code=422, detail="Assumptions and alternatives must be structured objects")
        candidate_status = str(candidate.get("status") or "").strip().lower()
        reviewer_id = str(candidate.get("reviewed_by_caregiver_id") or "").strip()
        if candidate_status in {"accepted", "rejected", "superseded"} and reviewer_id:
            if engine.caregiver_id is None or reviewer_id != str(engine.caregiver_id):
                raise HTTPException(
                    status_code=403,
                    detail="Assumption and alternative lifecycle decisions must be attributed to the authenticated caregiver",
                )
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

    # Bring persisted source-lineage review into the analyzer. A lineage
    # relationship is person-scoped and never inferred from cross-person IDs.
    from app.models.care_continuity import EvidenceSourceRelationship

    from app.models.care_continuity import EvidenceSourceRelationship
    from app.services.evidence_lineage import build_evidence_lineage

    relationships = []
    if requested_evidence_ids:
        relationships = engine.db.query(EvidenceSourceRelationship).filter(
            EvidenceSourceRelationship.person_id == request.person_id,
            (EvidenceSourceRelationship.source_evidence_id.in_(requested_evidence_ids))
            | (EvidenceSourceRelationship.related_evidence_id.in_(requested_evidence_ids)),
        ).all()
    lineage_by_id, independence_by_id = build_evidence_lineage(
        evidence_rows, relationships, requested_evidence_ids
    )

    try:
        analysis = analyze_uncertainty(
            claim=request.claim,
            task=request.task,
            evidence=[
                evidence_record_to_uncertainty_item(
                    row,
                    role="baseline" if row.id in requested_baseline_ids else "current",
                    is_baseline=row.id in requested_baseline_ids,
                    source_lineage_id=lineage_by_id.get(row.id),
                    source_independence_status=independence_by_id.get(row.id),
                )
                for row in evidence_rows
            ],
            required_fields=request.required_fields,
            unresolved_conflicts=[{"id": row.id, "field": row.field, "description": row.description, "evidence_ids": row.evidence_ids, "status": row.resolution_status} for row in conflict_rows if row.resolution_status != "resolved"],
            assumptions=request.assumptions,
            alternatives=request.alternatives,
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
