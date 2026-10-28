from datetime import datetime, timezone
import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.uncertainty import (
    InformationGap as InformationGapModel,
    OpenQuestion as OpenQuestionModel,
    Contradiction as ContradictionModel,
    UncertaintyAssessment as UncertaintyAssessmentModel,
    ContradictionResolutionEvent as ContradictionResolutionEventModel,
)
from app.schemas.uncertainty import (
    InformationGapCreate,
    InformationGapUpdate,
    OpenQuestionCreate,
    OpenQuestionUpdate,
    ContradictionCreate,
    ContradictionUpdate,
    UncertaintyAssessmentCreate,
    EpistemicView,
    SubjectUncertaintySummary,
)
from app.core.uncertainty_enums import (
    EpistemicState,
    GapLifecycleStatus,
)


class UncertaintyEngine:
    def __init__(self, db: Session, person_id: Optional[str] = None, caregiver_id: Optional[str] = None):
        self.db = db
        self.person_id = person_id
        self.caregiver_id = caregiver_id

    def _scoped_payload(self, payload: dict) -> dict:
        data = dict(payload)
        if self.person_id is not None and "person_id" in data:
            data["person_id"] = self.person_id
        if self.caregiver_id is not None:
            for field in ("created_by_caregiver_id", "updated_by_caregiver_id", "assessed_by_caregiver_id", "resolved_by_caregiver_id"):
                if field in data:
                    data[field] = self.caregiver_id
        return data

    def _target_query(self, model, record_id: str):
        query = self.db.query(model).filter(model.id == record_id)
        if self.person_id is not None:
            query = query.filter(model.person_id == self.person_id)
        return query

    def create_information_gap(self, gap: InformationGapCreate) -> InformationGapModel:
        db_gap = InformationGapModel(**self._scoped_payload(gap.model_dump()))
        self.db.add(db_gap)
        self.db.commit()
        self.db.refresh(db_gap)
        return db_gap

    def update_information_gap(
        self, gap_id: str, update: InformationGapUpdate
    ) -> Optional[InformationGapModel]:
        db_gap = (
            self._target_query(InformationGapModel, gap_id).first()
        )
        if not db_gap:
            return None

        update_data = self._scoped_payload(update.model_dump(exclude_unset=True))
        if "lifecycle_status" in update_data:
            new_status = update_data["lifecycle_status"]
            if new_status == GapLifecycleStatus.RESOLVED:
                db_gap.resolution_date = datetime.now(timezone.utc)
            elif new_status == GapLifecycleStatus.OPEN and db_gap.lifecycle_status != GapLifecycleStatus.OPEN:
                db_gap.resolution_date = None
                db_gap.resolution_mechanism = None
                db_gap.resolution_notes = None

        for field, value in update_data.items():
            setattr(db_gap, field, value)

        self.db.commit()
        self.db.refresh(db_gap)
        return db_gap

    def list_information_gaps(
        self,
        person_id: str,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        status: Optional[GapLifecycleStatus] = None,
    ) -> List[InformationGapModel]:
        if self.person_id is not None and person_id != self.person_id:
            return []
        scoped_person_id = self.person_id or person_id
        query = self.db.query(InformationGapModel).filter(
            InformationGapModel.person_id == scoped_person_id
        )
        if subject_type:
            query = query.filter(InformationGapModel.subject_type == subject_type)
        if subject_id:
            query = query.filter(InformationGapModel.subject_id == subject_id)
        if status:
            query = query.filter(InformationGapModel.lifecycle_status == status)
        return query.order_by(InformationGapModel.created_at.desc()).all()

    def create_open_question(self, question: OpenQuestionCreate) -> OpenQuestionModel:
        db_question = OpenQuestionModel(**self._scoped_payload(question.model_dump()))
        self.db.add(db_question)
        self.db.commit()
        self.db.refresh(db_question)
        return db_question

    def update_open_question(
        self, question_id: str, update: OpenQuestionUpdate
    ) -> Optional[OpenQuestionModel]:
        db_question = (
            self._target_query(OpenQuestionModel, question_id).first()
        )
        if not db_question:
            return None

        update_data = self._scoped_payload(update.model_dump(exclude_unset=True))
        if "status" in update_data:
            new_status = update_data["status"]
            if new_status == "closed":
                db_question.closed_at = datetime.now(timezone.utc)
                db_question.answered_at = datetime.now(timezone.utc)
            elif new_status == "open" and db_question.status != "open":
                db_question.closed_at = None
                db_question.answer = None
                db_question.answer_provenance = None

        for field, value in update_data.items():
            setattr(db_question, field, value)

        self.db.commit()
        self.db.refresh(db_question)
        return db_question

    def list_open_questions(
        self,
        person_id: str,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[OpenQuestionModel]:
        if self.person_id is not None and person_id != self.person_id:
            return []
        scoped_person_id = self.person_id or person_id
        query = self.db.query(OpenQuestionModel).filter(
            OpenQuestionModel.person_id == scoped_person_id
        )
        if subject_type:
            query = query.filter(OpenQuestionModel.subject_type == subject_type)
        if subject_id:
            query = query.filter(OpenQuestionModel.subject_id == subject_id)
        if status:
            query = query.filter(OpenQuestionModel.status == status)
        return query.order_by(OpenQuestionModel.asked_at.desc()).all()

    def create_contradiction(self, contradiction: ContradictionCreate) -> ContradictionModel:
        db_contradiction = ContradictionModel(**self._scoped_payload(contradiction.model_dump()))
        self.db.add(db_contradiction)
        self.db.commit()
        self.db.refresh(db_contradiction)
        return db_contradiction

    def update_contradiction(
        self, contradiction_id: str, update: ContradictionUpdate
    ) -> Optional[ContradictionModel]:
        db_contradiction = self._target_query(ContradictionModel, contradiction_id).first()
        if not db_contradiction:
            return None

        update_data = self._scoped_payload(update.model_dump(exclude_unset=True))
        requested_status = update_data.get("resolution_status", db_contradiction.resolution_status)
        status_changed = requested_status != db_contradiction.resolution_status
        resolution_statuses = {"resolved", "partially_resolved", "qualified", "superseded", "reopened", "unresolved"}
        if requested_status not in resolution_statuses:
            raise ValueError("Unsupported conflict resolution status")
        rationale = (update_data.get("resolution_notes") or "").strip()
        if status_changed and requested_status in {"resolved", "partially_resolved", "qualified", "superseded", "reopened"} and not rationale:
            raise ValueError("A non-empty rationale is required to resolve, partially resolve, or reopen a conflict")

        evidence_ids = update_data.get("evidence_ids", db_contradiction.evidence_ids) or []
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Duplicate evidence IDs are not allowed in conflict resolution")
        if "evidence_ids" in update_data or status_changed:
            from app.models.care import Evidence
            if evidence_ids:
                evidence_count = (
                    self.db.query(Evidence)
                    .filter(Evidence.person_id == db_contradiction.person_id, Evidence.id.in_(evidence_ids))
                    .count()
                )
                if evidence_count != len(evidence_ids):
                    raise ValueError("Resolution evidence must belong to the same care record")

        try:
            if status_changed:
                event = ContradictionResolutionEventModel(
                    contradiction_id=db_contradiction.id,
                    person_id=db_contradiction.person_id,
                    actor_caregiver_id=self.caregiver_id,
                    previous_status=db_contradiction.resolution_status,
                    new_status=requested_status,
                    rationale=rationale,
                    evidence_ids=list(evidence_ids),
                )
                self.db.add(event)

            for field, value in update_data.items():
                if field == "resolved_by_caregiver_id":
                    # Actor identity comes only from the validated session.
                    continue
                setattr(db_contradiction, field, value)

            if status_changed and requested_status == "resolved":
                db_contradiction.resolved_at = datetime.now(timezone.utc)
                db_contradiction.resolved_by_caregiver_id = self.caregiver_id
            elif status_changed and requested_status in {"reopened", "unresolved", "partially_resolved", "qualified", "superseded"}:
                db_contradiction.resolved_at = None
                db_contradiction.resolved_by_caregiver_id = None

            self.db.commit()
            self.db.refresh(db_contradiction)
            return db_contradiction
        except Exception:
            self.db.rollback()
            raise

    def list_contradiction_resolution_events(self, contradiction_id: str):
        query = self.db.query(ContradictionResolutionEventModel).filter(
            ContradictionResolutionEventModel.contradiction_id == contradiction_id
        )
        if self.person_id is not None:
            query = query.filter(ContradictionResolutionEventModel.person_id == self.person_id)
        return query.order_by(ContradictionResolutionEventModel.created_at.asc()).all()

    def list_contradictions(
        self,
        person_id: str,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        resolution_status: Optional[str] = None,
    ) -> List[ContradictionModel]:
        if self.person_id is not None and person_id != self.person_id:
            return []
        scoped_person_id = self.person_id or person_id
        query = self.db.query(ContradictionModel).filter(
            ContradictionModel.person_id == scoped_person_id
        )
        if subject_type:
            query = query.filter(ContradictionModel.subject_type == subject_type)
        if subject_id:
            query = query.filter(ContradictionModel.subject_id == subject_id)
        if resolution_status:
            query = query.filter(ContradictionModel.resolution_status == resolution_status)
        return query.order_by(ContradictionModel.created_at.desc()).all()

    def create_uncertainty_assessment(
        self, assessment: UncertaintyAssessmentCreate
    ) -> UncertaintyAssessmentModel:
        payload = self._scoped_payload(assessment.model_dump())
        scoped_person_id = self.person_id or payload["person_id"]
        payload["person_id"] = scoped_person_id
        new_id = str(uuid.uuid4())
        try:
            existing = (
                self.db.query(UncertaintyAssessmentModel)
                .filter(
                    UncertaintyAssessmentModel.person_id == scoped_person_id,
                    UncertaintyAssessmentModel.subject_type == payload["subject_type"],
                    UncertaintyAssessmentModel.subject_id == payload["subject_id"],
                    UncertaintyAssessmentModel.field == payload["field"],
                    UncertaintyAssessmentModel.is_active,
                )
                .first()
            )
            if existing:
                existing.is_active = False
                existing.superseded_by_assessment_id = new_id

            db_assessment = UncertaintyAssessmentModel(id=new_id, **payload)
            self.db.add(db_assessment)
            # Old assessment supersession and new assessment creation are atomic.
            self.db.commit()
            self.db.refresh(db_assessment)
            return db_assessment
        except Exception:
            self.db.rollback()
            raise

    def list_assessments(
        self,
        person_id: str,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        epistemic_state: Optional[EpistemicState] = None,
    ) -> List[UncertaintyAssessmentModel]:
        if self.person_id is not None and person_id != self.person_id:
            return []
        scoped_person_id = self.person_id or person_id
        query = self.db.query(UncertaintyAssessmentModel).filter(
            UncertaintyAssessmentModel.person_id == scoped_person_id,
            UncertaintyAssessmentModel.is_active,
        )
        if subject_type:
            query = query.filter(UncertaintyAssessmentModel.subject_type == subject_type)
        if subject_id:
            query = query.filter(UncertaintyAssessmentModel.subject_id == subject_id)
        if epistemic_state:
            query = query.filter(UncertaintyAssessmentModel.epistemic_state == epistemic_state)
        return query.order_by(UncertaintyAssessmentModel.assessed_at.desc()).all()

    def get_epistemic_view(
        self, person_id: str, subject_type: str, subject_id: str
    ) -> EpistemicView:
        assessments = (
            self.db.query(UncertaintyAssessmentModel)
            .filter(
                UncertaintyAssessmentModel.person_id == person_id,
                UncertaintyAssessmentModel.subject_type == subject_type,
                UncertaintyAssessmentModel.subject_id == subject_id,
                UncertaintyAssessmentModel.is_active,
            )
            .all()
        )

        gaps = (
            self.db.query(InformationGapModel)
            .filter(
                InformationGapModel.person_id == person_id,
                InformationGapModel.subject_type == subject_type,
                InformationGapModel.subject_id == subject_id,
                InformationGapModel.lifecycle_status == GapLifecycleStatus.OPEN,
            )
            .all()
        )

        questions = (
            self.db.query(OpenQuestionModel)
            .filter(
                OpenQuestionModel.person_id == person_id,
                OpenQuestionModel.subject_type == subject_type,
                OpenQuestionModel.subject_id == subject_id,
                OpenQuestionModel.status == "open",
            )
            .all()
        )

        contradictions = (
            self.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == person_id,
                ContradictionModel.subject_type == subject_type,
                ContradictionModel.subject_id == subject_id,
                ContradictionModel.resolution_status == "unresolved",
            )
            .all()
        )

        primary = None
        if assessments:
            primary = assessments[0]

        return EpistemicView(
            subject_type=subject_type,
            subject_id=subject_id,
            field=primary.field if primary else "",
            epistemic_state=primary.epistemic_state if primary else EpistemicState.UNKNOWN,
            confidence=primary.confidence if primary else None,
            known_value=primary.known_value if primary else None,
            gap_reason=primary.gap_reason if primary else None,
            context=primary.context if primary else None,
            evidence_ids=primary.evidence_ids if primary else None,
            temporal_precision=primary.temporal_precision if primary else None,
            contradictions=[
                {
                    "id": c.id,
                    "field": c.field,
                    "description": c.description,
                    "evidence_ids": c.evidence_ids or [],
                }
                for c in contradictions
            ],
            open_questions=[
                {
                    "id": q.id,
                    "question": q.question,
                    "priority": q.priority.value if q.priority else None,
                    "asked_at": q.asked_at.isoformat(),
                }
                for q in questions
            ],
            gaps=[
                {
                    "id": g.id,
                    "field": g.field,
                    "gap_reason": g.gap_reason.value if g.gap_reason else None,
                    "priority": g.priority.value if g.priority else None,
                    "lifecycle_status": g.lifecycle_status.value if g.lifecycle_status else None,
                    "description": g.description,
                }
                for g in gaps
            ],
        )

    def get_subject_summary(
        self, person_id: str, subject_type: str, subject_id: str
    ) -> SubjectUncertaintySummary:
        assessments = (
            self.db.query(UncertaintyAssessmentModel)
            .filter(
                UncertaintyAssessmentModel.person_id == person_id,
                UncertaintyAssessmentModel.subject_type == subject_type,
                UncertaintyAssessmentModel.subject_id == subject_id,
                UncertaintyAssessmentModel.is_active,
            )
            .all()
        )

        open_gaps = (
            self.db.query(InformationGapModel)
            .filter(
                InformationGapModel.person_id == person_id,
                InformationGapModel.subject_type == subject_type,
                InformationGapModel.subject_id == subject_id,
                InformationGapModel.lifecycle_status == GapLifecycleStatus.OPEN,
            )
            .count()
        )

        open_questions = (
            self.db.query(OpenQuestionModel)
            .filter(
                OpenQuestionModel.person_id == person_id,
                OpenQuestionModel.subject_type == subject_type,
                OpenQuestionModel.subject_id == subject_id,
                OpenQuestionModel.status == "open",
            )
            .count()
        )

        contradictions = (
            self.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == person_id,
                ContradictionModel.subject_type == subject_type,
                ContradictionModel.subject_id == subject_id,
                ContradictionModel.resolution_status == "unresolved",
            )
            .count()
        )

        counts = {
            "total": len(assessments),
            EpistemicState.KNOWN: 0,
            EpistemicState.PARTIALLY_KNOWN: 0,
            EpistemicState.AMBIGUOUS: 0,
            EpistemicState.CONFLICTING: 0,
            EpistemicState.UNKNOWN: 0,
            EpistemicState.NOT_DOCUMENTED: 0,
            EpistemicState.NOT_ASSESSED: 0,
            EpistemicState.STALE: 0,
            EpistemicState.HISTORICAL: 0,
        }

        for a in assessments:
            state = a.epistemic_state
            if state in counts:
                counts[state] += 1

        return SubjectUncertaintySummary(
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id,
            total_fields=counts["total"],
            known_count=counts[EpistemicState.KNOWN],
            partially_known_count=counts[EpistemicState.PARTIALLY_KNOWN],
            ambiguous_count=counts[EpistemicState.AMBIGUOUS],
            conflicting_count=counts[EpistemicState.CONFLICTING],
            unknown_count=counts[EpistemicState.UNKNOWN],
            not_documented_count=counts[EpistemicState.NOT_DOCUMENTED],
            not_assessed_count=counts[EpistemicState.NOT_ASSESSED],
            stale_count=counts[EpistemicState.STALE],
            historical_count=counts[EpistemicState.HISTORICAL],
            open_gaps_count=open_gaps,
            open_questions_count=open_questions,
            contradictions_count=contradictions,
        )
