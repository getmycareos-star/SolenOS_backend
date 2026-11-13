from collections import Counter
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
from app.core.database import get_db
from app.models.care import Evidence, Person
from app.models.uncertainty import Contradiction as ContradictionModel, OpenQuestion as OpenQuestionModel, OpenQuestionAnswerEvent as OpenQuestionAnswerEventModel, OpenQuestionReviewEvent as OpenQuestionReviewEventModel
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
from app.schemas.uncertainty import UncertaintyAnalysisRequest, UncertaintyAnalysisResponse, OpenQuestionReviewCreate, OpenQuestionReviewResponse
from app.schemas.uncertainty import ClaimDependencyCreate, ClaimDependency
from app.core.uncertainty_enums import GapLifecycleStatus, EpistemicState
from app.api.care_delta import _scoped_identity
from app.models.care_continuity import EvidenceClaimDependency, EvidenceClaimAssessment, CareAssertion
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.services.care_continuity_execution import execute_care_continuity_reconciliation

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


@router.get("/question-quality")
def question_quality_metrics(engine: UncertaintyEngine = Depends(get_engine)):
    """Care-record-scoped operational counts for question noise and review burden."""
    if not engine.person_id:
        raise HTTPException(status_code=403, detail="A session-bound care record is required")
    rows = engine.db.query(OpenQuestionModel).filter(
        OpenQuestionModel.person_id == engine.person_id,
    ).all()
    active = [row for row in rows if row.status in {"open", "unresolved"}]
    normalized = [
        (
            " ".join((row.question or "").casefold().split()),
            row.subject_type,
            row.subject_id,
            row.claim_id,
        )
        for row in active if (row.question or "").strip()
    ]
    frequencies = Counter(normalized)
    duplicate_groups = sum(1 for count in frequencies.values() if count > 1)
    duplicate_excess = sum(count - 1 for count in frequencies.values() if count > 1)
    source_active = [
        row for row in active
        if any(
            isinstance(item, dict) and item.get("source_kind")
            for item in (row.context or [])
        )
    ]
    from app.services.care_continuity import has_valid_caregiver_necessity
    validated_active = [
        row for row in active
        if row.necessity == "necessary"
        and has_valid_caregiver_necessity(row, engine.db, engine.person_id)
    ]
    unassessed_source = [row for row in source_active if row not in validated_active]
    auto_suppressed = [
        row for row in rows
        if row.status == "closed"
        and str(row.closed_reason or "").startswith("automatically_suppressed")
    ]
    auto_superseded = [
        row for row in rows
        if row.status == "closed"
        and str(row.closed_reason or "").startswith("automatically_superseded")
    ]
    return {
        "person_id": engine.person_id,
        "active_questions": len(active),
        "caregiver_actionable_questions": len(validated_active),
        "non_actionable_active_questions": len(active) - len(validated_active),
        "duplicate_question_groups": duplicate_groups,
        "duplicate_question_excess": duplicate_excess,
        "source_questions_awaiting_necessity_assessment": len(unassessed_source),
        "manual_review_backlog": len(unassessed_source),
        "automatically_suppressed_questions": len(auto_suppressed),
        "automatically_superseded_questions": len(auto_superseded),
        "answered_questions": sum(1 for row in rows if row.status == "answered"),
        "definitions": {
            "non_actionable_active_questions": "Open questions not marked necessary; these must not appear in the caregiver action list.",
            "manual_review_backlog": "Source-linked open questions without a valid necessary assessment.",
            "duplicate_question_excess": "Additional active records beyond one per normalized question text.",
            "automatic_transitions": "System-suppressed or superseded questions; neither is counted as a caregiver answer.",
        },
    }


@router.get("/questions/recommended/{person_id}")
def recommended_caregiver_questions(
    person_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Return a small, evidence-validated caregiver question queue.

    This is a projection over the existing LCR question records, not a second
    question store. Non-necessary, stale, cross-record, or unsupported items
    are excluded. Critical items are never hidden by the ordinary display cap.
    """
    _require_person_scope(engine, person_id)
    from app.services.care_continuity import (
        has_valid_caregiver_necessity,
        prioritize_caregiver_questions,
    )

    rows = engine.db.query(OpenQuestionModel).filter(
        OpenQuestionModel.person_id == person_id,
        OpenQuestionModel.status.in_(["open", "unresolved"]),
    ).all()
    eligible = [
        row for row in rows
        if row.necessity == "necessary"
        and has_valid_caregiver_necessity(row, engine.db, person_id)
    ]
    unique, visible, hidden_count = prioritize_caregiver_questions(eligible, ordinary_limit=2)
    critical_count = sum(
        1 for row in unique
        if str(getattr(row.priority, "value", row.priority)) == "critical"
    )
    ordinary_visible_count = len(visible) - critical_count
    return {
        "person_id": person_id,
        "questions": [OpenQuestion.model_validate(row).model_dump(mode="json") for row in visible],
        "eligible_question_ids": [row.id for row in unique],
        "recommended_question_ids": [row.id for row in visible],
        "critical_count": critical_count,
        "ordinary_visible_count": ordinary_visible_count,
        "additional_ordinary_count": hidden_count,
        "status": "questions_to_review" if visible else "nothing_to_ask",
        "interpretation_limit": (
            "These are source-backed questions whose stored assessment says an answer "
            "is necessary for a care decision. This does not establish clinical urgency."
        ),
    }


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
    ).order_by(OpenQuestionAnswerEventModel.answer_version.asc()).all()
    return [{
        "id": row.id,
        "question_id": row.question_id,
        "person_id": row.person_id,
        "answer": row.answer,
        "answer_provenance": row.answer_provenance,
        "evidence_ids": list(row.evidence_ids or []),
        "answer_version": row.answer_version,
        "answered_by_caregiver_id": row.answered_by_caregiver_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


@router.post(
    "/questions/{question_id}/reviews",
    response_model=OpenQuestionReviewResponse,
)
def review_question_answer(
    question_id: str,
    review: OpenQuestionReviewCreate,
    engine: UncertaintyEngine = Depends(get_engine),
):
    """Append a scoped decision for the latest answer; accepted reports enter LCR as unverified assertions."""
    if not engine.person_id or not engine.caregiver_id:
        raise HTTPException(status_code=403, detail="A session-bound caregiver reviewer is required")

    question = engine._target_query(OpenQuestionModel, question_id).with_for_update().first()
    if question is None:
        raise HTTPException(status_code=404, detail="Clarification question not found")
    if question.status != "answered":
        raise HTTPException(status_code=409, detail="Only a submitted answer can be reviewed")

    answer_event = (
        engine.db.query(OpenQuestionAnswerEventModel)
        .filter(
            OpenQuestionAnswerEventModel.question_id == question.id,
            OpenQuestionAnswerEventModel.person_id == engine.person_id,
        )
        .order_by(OpenQuestionAnswerEventModel.answer_version.desc())
        .first()
    )
    if answer_event is None:
        raise HTTPException(status_code=409, detail="No persisted answer event is available to review")

    existing = engine.db.query(OpenQuestionReviewEventModel).filter(
        OpenQuestionReviewEventModel.question_id == question.id,
        OpenQuestionReviewEventModel.answer_version == answer_event.answer_version,
    ).first()
    if existing:
        if existing.decision == review.decision and existing.rationale == review.rationale.strip():
            return existing
        raise HTTPException(status_code=409, detail="This answer version already has a review decision; submit a new answer version before another decision")

    resulting_claim = None
    claim_assessment = None
    if review.decision == "accepted":
        if not question.claim_id:
            raise HTTPException(status_code=422, detail="This clarification has no linked care claim; it cannot be reconciled into the LCR until a reviewer links the correct claim")
        source_claim = engine.db.query(CareAssertion).filter(
            CareAssertion.id == question.claim_id,
            CareAssertion.person_id == engine.person_id,
            CareAssertion.subject_type == question.subject_type,
            CareAssertion.subject_id == question.subject_id,
        ).first()
        if source_claim is None:
            raise HTTPException(status_code=409, detail="The linked care claim is no longer available in this care record")
        if source_claim.assertion_kind == "current_state" and review.review_after_days is None:
            raise HTTPException(status_code=422, detail="Accepting a current-state clarification requires a caregiver-selected review interval")
        if source_claim.assertion_kind == "time_bound_observation" and source_claim.observed_at is None:
            raise HTTPException(status_code=422, detail="The linked time-bound claim has no actual event time; this clarification cannot inherit an invented timestamp")
        evidence_ids = list(answer_event.evidence_ids or [])
        if not evidence_ids:
            raise HTTPException(status_code=422, detail="An accepted clarification requires persisted supporting evidence")
        evidence_rows = engine.db.query(Evidence).filter(
            Evidence.person_id == engine.person_id,
            Evidence.id.in_(evidence_ids),
        ).all()
        evidence_by_id = {row.id: row for row in evidence_rows}
        if set(evidence_by_id) != set(evidence_ids):
            raise HTTPException(status_code=409, detail="One or more answer evidence records are no longer available in this care record")

        # Acceptance means the submitted report is admitted to the LCR as a
        # source-attributed assertion. It does NOT mean the reported value is verified.
        resulting_claim = create_assertion(
            engine.db,
            CareAssertionCreate(
                person_id=engine.person_id,
                subject_type=source_claim.subject_type,
                subject_id=source_claim.subject_id,
                field=source_claim.field,
                assertion_kind=source_claim.assertion_kind,
                epistemic_state="reported",
                asserted_value=answer_event.answer,
                source_label="Reviewed caregiver clarification",
                source_text=f"Caregiver answer: {answer_event.answer}\nSource or basis: {answer_event.answer_provenance}",
                evidence_ids=evidence_ids,
                observed_at=source_claim.observed_at,
                review_after_days=review.review_after_days,
                created_by_caregiver_id=answer_event.answered_by_caregiver_id,
                updated_by_caregiver_id=engine.caregiver_id,
            ),
            commit=False,
        )

        latest_assessment = engine.db.query(EvidenceClaimAssessment).filter(
            EvidenceClaimAssessment.person_id == engine.person_id,
            EvidenceClaimAssessment.claim_id == resulting_claim.id,
        ).order_by(EvidenceClaimAssessment.claim_version.desc()).first()
        claim_assessment = EvidenceClaimAssessment(
            person_id=engine.person_id,
            claim_id=resulting_claim.id,
            claim_version=(latest_assessment.claim_version + 1) if latest_assessment else 1,
            status="unverified",
            rationale="Clarification review accepted the caregiver's attributed report into the LCR. This records admissibility as a report, not factual or clinical verification.",
            policy_version="claim-assessment-v1",
            evidence_links=[
                {"evidence_id": evidence_id, "relationship": "neutral", "source_passage": None,
                 "scope_note": "Linked as the caregiver-reported source for this clarification; not independently verified."}
                for evidence_id in evidence_ids
            ],
            reviewed_by_caregiver_id=engine.caregiver_id,
        )
        engine.db.add(claim_assessment)
        engine.db.flush()

        # Preserve the connection to the original claim without replacing it.
        engine.db.add(EvidenceClaimDependency(
            person_id=engine.person_id,
            claim_id=resulting_claim.id,
            depends_on_claim_id=source_claim.id,
            dependency_type="qualifies",
            rationale="This source-attributed assertion was created from the reviewed clarification answer and retains the original question's linked claim as context.",
            created_by_caregiver_id=engine.caregiver_id,
        ))

    event = OpenQuestionReviewEventModel(
        question_id=question.id,
        person_id=engine.person_id,
        answer_event_id=answer_event.id,
        answer_version=answer_event.answer_version,
        decision=review.decision,
        rationale=review.rationale.strip(),
        reviewer_caregiver_id=engine.caregiver_id,
        claim_assessment_id=claim_assessment.id if claim_assessment else None,
        resulting_claim_id=resulting_claim.id if resulting_claim else None,
    )
    execution = None
    try:
        engine.db.add(event)
        if review.decision == "accepted":
            try:
                execution = execute_care_continuity_reconciliation(
                    engine.db,
                    engine.person_id,
                    workflow="clarification_answer_review",
                    source_evidence_ids=evidence_ids,
                )
            except Exception as exc:
                engine.db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail="Care-continuity reconciliation failed; the clarification review was not committed. Retry the review.",
                ) from exc
        engine.db.commit()
        engine.db.refresh(event)
        if execution is not None:
            execution["stages"]["reconciliation"] = "committed"
            event.care_continuity_execution = execution
        return event
    except IntegrityError as exc:
        engine.db.rollback()
        concurrent = engine.db.query(OpenQuestionReviewEventModel).filter(
            OpenQuestionReviewEventModel.question_id == question.id,
            OpenQuestionReviewEventModel.answer_version == answer_event.answer_version,
        ).first()
        if concurrent and concurrent.decision == review.decision and concurrent.rationale == review.rationale.strip():
            return concurrent
        raise HTTPException(status_code=409, detail="This answer version was reviewed concurrently; reload its review history") from exc
    except Exception:
        engine.db.rollback()
        raise


@router.get(
    "/questions/{question_id}/review-history",
    response_model=List[OpenQuestionReviewResponse],
)
def list_question_review_history(
    question_id: str,
    engine: UncertaintyEngine = Depends(get_engine),
):
    question = engine._target_query(OpenQuestionModel, question_id).first()
    if question is None:
        raise HTTPException(status_code=404, detail="Clarification question not found")
    rows = engine.db.query(OpenQuestionReviewEventModel).filter(
        OpenQuestionReviewEventModel.question_id == question_id,
        OpenQuestionReviewEventModel.person_id == engine.person_id,
    ).order_by(
        OpenQuestionReviewEventModel.answer_version.asc(),
        OpenQuestionReviewEventModel.created_at.asc(),
    ).all()
    return rows


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

    if len(requested_evidence_ids) > 250:
        raise HTTPException(status_code=422, detail="An uncertainty analysis may reference at most 250 evidence records; split the review into bounded assessments.")

    # Resolve the full bounded, same-person provenance closure. Parent-only
    # walking misses shared original-source references and reviewed relationships
    # that connect selected records through unselected ancestors or siblings.
    from app.services.evidence_lineage import (
        assess_corroboration,
        build_evidence_lineage,
        find_exact_content_duplicate_candidates,
        load_evidence_lineage_scope,
    )

    lineage_scope = load_evidence_lineage_scope(
        engine.db, request.person_id, requested_evidence_ids,
        max_depth=12, max_nodes=250, max_relationships=450,
    )
    lineage_by_id, independence_by_id = build_evidence_lineage(
        lineage_scope["evidence_rows"],
        lineage_scope["relationships"],
        requested_evidence_ids,
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

    analysis["source_lineage_scope"] = {
        "status": "truncated" if lineage_scope["truncated"] else "bounded_closure_loaded",
        "evidence_nodes_examined": len(lineage_scope["evidence_rows"]),
        "relationship_records_examined": len(lineage_scope["relationships"]),
        "interpretation_limit": (
            "Lineage follows persisted same-person parent/source references and reviewed relationships; "
            "it does not establish claim truth or prove independent witnessing."
        ),
    }
    exact_duplicate_candidates = find_exact_content_duplicate_candidates(
        lineage_scope["evidence_rows"], requested_evidence_ids
    )
    analysis["exact_content_duplicate_candidates"] = exact_duplicate_candidates
    analysis["corroboration_assessment"] = assess_corroboration(
        lineage_scope["evidence_rows"],
        lineage_scope["relationships"],
        requested_evidence_ids,
        lineage_truncated=bool(lineage_scope["truncated"]),
    )
    if exact_duplicate_candidates and not lineage_scope["truncated"]:
        # Matching hashes prove identical stored text, not a shared author or
        # common origin. Until a human reviews source independence, do not let
        # these copies inflate a numeric corroboration count.
        analysis["independent_evidence_record_count"] = None
        analysis["independent_evidence_count_status"] = "requires_source_independence_review"
        analysis["review_required"] = True
        analysis["classifications"] = sorted(
            set(analysis.get("classifications", [])) | {"exact_content_duplicate_candidate_review"}
        )
        analysis.setdefault("limitations", []).append(
            "Identical stored source text was found. Common origin and independent witnessing remain unestablished; review source relationships before counting corroboration."
        )
    if lineage_scope["truncated"]:
        analysis["classifications"] = sorted(set(analysis.get("classifications", [])) | {"source_lineage_scope_truncated"})
        analysis.setdefault("limitations", []).append(
            "The source-lineage traversal reached its safety bound. Independent corroboration cannot be established from this incomplete lineage scope."
        )
        analysis["review_required"] = True
        analysis["independent_evidence_record_count"] = None
        analysis["independent_evidence_count_status"] = "unavailable_due_to_truncated_lineage"

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
    # Model-generated clarification wording is retained with the internal
    # uncertainty assessment, but it is not a caregiver request. A separate
    # evidence-backed necessity assessment must promote it through the shared
    # question lifecycle before it can appear in caregiver-facing projections.
    internal_candidates = list(analysis.get("clarification_questions") or [])
    analysis["internal_clarification_candidates"] = internal_candidates
    analysis["clarification_questions"] = []
    analysis["clarification_candidate_status"] = "not_assessed_not_caregiver_work"
    try:
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
