from datetime import datetime, timezone, timedelta
import re
import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from app.models.care import CareEvent as CareEventModel, Evidence as EvidenceModel
from app.models.care_continuity import CareAssertion as CareAssertionModel
from app.models.uncertainty import (
    InformationGap as InformationGapModel,
    OpenQuestion as OpenQuestionModel,
    OpenQuestionAnswerEvent as OpenQuestionAnswerEventModel,
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
    Priority,
)


def _has_decision_dependency(context) -> bool:
    """Only a structured, explicit current-decision dependency can justify a request."""
    for item in context or []:
        if not isinstance(item, dict):
            continue
        direct = item.get("decision_dependency")
        nested = item.get("necessity_assessment")
        nested_value = nested.get("decision_dependency") if isinstance(nested, dict) else None
        if any(isinstance(value, str) and value.strip() for value in (direct, nested_value)):
            return True
    return False


def _question_is_answered_by_current_lcr(
    db: Session,
    person_id: str,
    question_text: str,
) -> bool:
    """Conservatively suppress a question when an accepted, evidenced LCR field answers it.

    This is deliberately a field-label match, not semantic inference. It only
    suppresses when every meaningful token in a structured current-state field
    appears in the question and the assertion has same-person supporting evidence.
    """
    question_tokens = {
        token for token in re.findall(r"[a-z0-9]+", question_text.casefold())
        if len(token) > 2
    }
    if not question_tokens:
        return False

    assertions = db.query(CareAssertionModel).filter(
        CareAssertionModel.person_id == person_id,
        CareAssertionModel.subject_type == "person",
        CareAssertionModel.subject_id == person_id,
        CareAssertionModel.assertion_kind.in_([
            "current_state", "document_reviewed_observation", "time_bound_observation",
        ]),
        CareAssertionModel.assertion_status.in_(["accepted", "caregiver_reviewed"]),
    ).all()
    now = datetime.now(timezone.utc)
    for assertion in assertions:
        if assertion.assertion_kind in {"document_reviewed_observation", "time_bound_observation"}:
            if not assertion.observed_at or not assertion.review_after_days or assertion.review_after_days <= 0:
                continue
            observed_at = assertion.observed_at
            observed_at = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at.astimezone(timezone.utc)
            if now > observed_at + timedelta(days=assertion.review_after_days):
                continue
        field_tokens = {
            token for token in re.findall(r"[a-z0-9]+", (assertion.field or "").replace("_", " ").casefold())
            if len(token) > 2
        }
        evidence_ids = list(dict.fromkeys(
            str(value) for value in (assertion.evidence_ids or []) if value
        ))
        if not field_tokens or not field_tokens.issubset(question_tokens) or not evidence_ids:
            continue
        # An accepted assertion cannot settle a source question while the
        # same LCR field still has an unresolved competing report.
        conflict = db.query(ContradictionModel.id).filter(
            ContradictionModel.person_id == person_id,
            ContradictionModel.subject_type == assertion.subject_type,
            ContradictionModel.subject_id == assertion.subject_id,
            ContradictionModel.field == assertion.field,
            ContradictionModel.resolution_status.in_(("unresolved", "reopened", "qualified", "partially_resolved")),
        ).first()
        if conflict is not None:
            continue
        valid_ids = {
            str(row[0]) for row in db.query(EvidenceModel.id).filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.id.in_(evidence_ids),
            ).all()
        }
        if valid_ids == set(evidence_ids):
            return True
    return False


def _clarification_question_key(value: str) -> tuple[str, ...]:
    """Conservative deterministic identity for repeated source-review questions.

    This is intentionally not an open-ended semantic guess. It normalizes
    common grammatical variants and a small set of unambiguous care terms,
    while retaining dates, negation, quantities and temporal qualifiers so
    questions about different circumstances are not collapsed together.
    """
    text = " ".join(value.casefold().split())
    text = re.sub(r"\bhow\s+many\s+times\b|\bhow\s+often\b", " frequency ", text)
    text = re.sub(r"\b(?:during\s+the\s+night|at\s+night)\b", " overnight ", text)
    aliases = {
        "meds": "medication",
        "medicine": "medication",
        "medicines": "medication",
        "medications": "medication",
        "dosage": "dose",
        "doses": "dose",
        "waking": "wake",
        "wakes": "wake",
        "woke": "wake",
        "awakened": "wake",
        "awakes": "wake",
        "regimen": "schedule",
        "regimens": "schedule",
        "currently": "current",
        "now": "current",
    }
    ignored = {
        "a", "an", "the", "what", "which", "who", "whom", "how", "when",
        "where", "why", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did", "has", "have", "had", "can", "could", "would",
        "should", "will", "may", "might", "must", "please", "you", "your",
        "she", "he", "they", "them", "her", "his", "their", "it", "its",
        "of", "to", "for", "from", "with", "about", "up",
    }
    tokens = [
        aliases.get(token, token)
        for token in re.findall(r"[a-z0-9]+", text)
        if token not in ignored
    ]
    return tuple(sorted(tokens))


def persist_source_clarification_questions(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    source_kind: str,
    source_record_type: str,
    source_record_id: str,
    evidence_ids: Optional[List[str]],
    questions: Optional[List[str]],
) -> List[OpenQuestionModel]:
    """Persist review-only questions against the exact saved source, without committing.

    Question wording is a suggestion from source review, not a verified claim.
    The caller owns the transaction that saves the source and its clarification.
    """
    if not all(isinstance(value, str) and value.strip() for value in (
        person_id, caregiver_id, source_kind, source_record_type, source_record_id
    )):
        raise ValueError("A care record, caregiver, and persisted source reference are required.")
    if source_kind not in {"daily_capture_extraction", "document_extraction"}:
        raise ValueError("Unsupported clarification source.")
    if source_record_type == "care_event":
        source_row = db.query(CareEventModel).filter(
            CareEventModel.id == source_record_id,
            CareEventModel.person_id == person_id,
        ).first()
        if source_row is None:
            raise ValueError("The clarification source event is missing or belongs to another care record.")
        linked_ids = list(dict.fromkeys(str(value) for value in (evidence_ids or []) if value))
        source_refs = [{"record_type": "care_event", "record_id": source_record_id}]
        if linked_ids:
            rows = db.query(EvidenceModel.id).filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.id.in_(linked_ids),
            ).all()
            if {str(row[0]) for row in rows} != set(linked_ids):
                raise ValueError("Every clarification source must belong to the same care record.")
            event_evidence_ids = {str(value) for value in (source_row.evidence_ids or [])}
            if not set(linked_ids).issubset(event_evidence_ids):
                raise ValueError("Clarification evidence must be linked to its saved source event.")
            source_refs.extend(
                {"record_type": "evidence", "record_id": evidence_id}
                for evidence_id in linked_ids
            )
    elif source_record_type == "evidence":
        source_row = db.query(EvidenceModel).filter(
            EvidenceModel.id == source_record_id,
            EvidenceModel.person_id == person_id,
        ).first()
        if source_row is None:
            raise ValueError("The clarification source document is missing or belongs to another care record.")
        # Another caregiver may review a document already saved in this same care record.
        # The evidence row preserves its uploader; this question records the reviewer.
        linked_ids = list(dict.fromkeys([source_record_id, *[
            str(value) for value in (evidence_ids or []) if value
        ]]))
        rows = db.query(EvidenceModel.id).filter(
            EvidenceModel.person_id == person_id,
            EvidenceModel.id.in_(linked_ids),
        ).all()
        if {str(row[0]) for row in rows} != set(linked_ids):
            raise ValueError("Every clarification source must belong to the same care record.")
        source_refs = [{"record_type": "evidence", "record_id": value} for value in linked_ids]
    else:
        raise ValueError("Unsupported clarification source type.")

    existing_rows = db.query(OpenQuestionModel).filter(
        OpenQuestionModel.person_id == person_id,
        OpenQuestionModel.status.in_(["open", "unresolved"]),
    ).order_by(OpenQuestionModel.asked_at.desc()).limit(250).all()
    existing_by_text = {}
    # Rows are newest first; keep the newest matching question as the row to
    # refresh, rather than allowing an older duplicate to overwrite it.
    for row in existing_rows:
        existing_by_text.setdefault(_clarification_question_key(row.question or ""), row)
    # Source extraction writes must use the same append-only question
    # lifecycle as other care workflows. Import locally to avoid a module
    # import cycle; this service is called by Daily Capture and Documents.
    from app.services.care_continuity import (
        _open_question_state,
        _record_open_question_revision,
        reassess_open_question_lifecycle,
    )

    persisted: List[OpenQuestionModel] = []
    seen: set[tuple[str, ...]] = set()
    # Source extraction writes their own immutable revision rows. Suppress the
    # ORM listener while these explicit revisions are recorded, or each insert
    # and update would be audited twice.
    manual_revision_objects = db.info.setdefault("manual_care_state_revision_objects", set())
    for value in (questions or [])[:20]:
        if not isinstance(value, str):
            continue
        question_text = " ".join(value.strip().split())[:300]
        normalized = _clarification_question_key(question_text)
        if len(question_text) < 5 or not normalized or normalized in seen:
            continue
        # Operational extraction warnings belong in the document review panel,
        # not in the caregiver's list of questions.
        if not (
            question_text.endswith(("?", "？"))
            or re.match(
                r"^(who|what|when|where|why|how|is|are|was|were|do|does|did|has|have|had|can|could|would|should|will|which|any)\b",
                question_text,
                re.IGNORECASE,
            )
        ):
            continue
        seen.add(normalized)
        # Do not retain a candidate as an open question when an accepted,
        # source-backed current-state LCR assertion already covers its field.
        if _question_is_answered_by_current_lcr(db, person_id, question_text):
            continue
        # Only inherit necessity from one unambiguous, same-record LCR gap
        # that already carries a structured decision dependency and evidence.
        # Source extraction wording or model confidence alone never qualifies.
        question_tokens = {
            token for token in re.findall(r"[a-z0-9]+", question_text.casefold())
            if len(token) > 2
        }
        necessity_gaps = []
        for gap in db.query(InformationGapModel).filter(
            InformationGapModel.person_id == person_id,
            InformationGapModel.lifecycle_status == GapLifecycleStatus.OPEN,
        ).all():
            field_tokens = {
                token for token in re.findall(
                    r"[a-z0-9]+", (gap.field or "").replace("_", " ").casefold()
                ) if len(token) > 2
            }
            if not field_tokens or not field_tokens.issubset(question_tokens):
                continue
            for assessment in (gap.context or []):
                if not isinstance(assessment, dict):
                    continue
                rationale = assessment.get("necessity_rationale")
                dependency = assessment.get("decision_dependency")
                gap_evidence_ids = list(dict.fromkeys(
                    str(value) for value in (gap.evidence_ids or []) if value
                ))
                if (
                    assessment.get("caregiver_attention_required") is True
                    and isinstance(rationale, str) and rationale.strip()
                    and isinstance(dependency, str) and dependency.strip()
                    and gap_evidence_ids
                ):
                    necessity_gaps.append((gap, rationale.strip(), dependency.strip(), gap_evidence_ids))
        matched_gap = necessity_gaps[0] if len(necessity_gaps) == 1 else None

        source_context = {
            "source_kind": source_kind,
            "source_id": source_record_id,
            "source_record_refs": source_refs,
            "reason": "Suggested while reviewing saved care information. Please check the wording before relying on it.",
            "grounding_status": "source_record_linked; question wording not independently verified",
        }
        contexts_to_add = [source_context]
        assessed_evidence_ids = list(linked_ids)
        subject_type = "person"
        subject_id = person_id
        necessity = "not_assessed"
        necessity_rationale = None
        priority = Priority.INFORMATIONAL
        if matched_gap:
            gap, necessity_rationale, dependency, gap_evidence_ids = matched_gap
            subject_type, subject_id = gap.subject_type, gap.subject_id
            assessed_evidence_ids = list(dict.fromkeys(assessed_evidence_ids + gap_evidence_ids))
            necessity = "necessary"
            priority = gap.priority
            contexts_to_add.append({
                "source_kind": "information_gap",
                "source_id": gap.id,
                "source_record_refs": source_refs,
                "necessity_assessment": {
                    "status": "necessary",
                    "field": gap.field,
                    "necessity_rationale": necessity_rationale,
                    "decision_dependency": dependency,
                    "evidence_ids": gap_evidence_ids,
                    "assessment_method": "deterministic_match_to_open_lcr_gap",
                },
            })
        existing = existing_by_text.get(normalized)
        if existing is None:
            existing = OpenQuestionModel(
                person_id=person_id,
                question=question_text,
                subject_type=subject_type,
                subject_id=subject_id,
                status="open",
                priority=priority,
                necessity=necessity,
                necessity_rationale=necessity_rationale,
                evidence_ids=assessed_evidence_ids or None,
                context=contexts_to_add,
                created_by_caregiver_id=caregiver_id,
            )
            manual_revision_objects.add(id(existing))
            try:
                db.add(existing)
                db.flush()
                _record_open_question_revision(
                    db, existing, before_state=None, change_kind="created",
                    actor=f"source_workflow:{source_kind}",
                )
            finally:
                manual_revision_objects.discard(id(existing))
            existing_by_text[normalized] = existing
        else:
            before_state = _open_question_state(existing)
            manual_revision_objects.add(id(existing))
            try:
                existing.evidence_ids = list(dict.fromkeys(
                    list(existing.evidence_ids or []) + assessed_evidence_ids
                )) or None
                if matched_gap and existing.necessity != "necessary":
                    existing.subject_type = subject_type
                    existing.subject_id = subject_id
                    existing.necessity = necessity
                    existing.necessity_rationale = necessity_rationale
                    existing.priority = priority
                contexts = list(existing.context or [])
                for new_context in contexts_to_add:
                    context_key = (new_context.get("source_kind"), new_context.get("source_id"))
                    if not any(
                        (item.get("source_kind"), item.get("source_id")) == context_key
                        for item in contexts if isinstance(item, dict)
                    ):
                        contexts.append(new_context)
                existing.context = contexts
                if before_state != _open_question_state(existing):
                    _record_open_question_revision(
                        db, existing, before_state=before_state,
                        change_kind="updated", actor=f"source_workflow:{source_kind}",
                    )
                db.flush()
            finally:
                manual_revision_objects.discard(id(existing))
        persisted.append(existing)

    # Reconcile the newly linked source against accepted LCR assertions and
    # explicit decision dependencies before the caller commits the source.
    # This never commits and never turns an unassessed extraction question
    # into caregiver work on wording or model confidence alone.
    reassess_open_question_lifecycle(db, person_id)
    db.flush()
    return persisted


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

    def _validate_person_evidence(self, person_id: str, evidence_ids) -> list:
        from app.models.care import Evidence

        ids = list(evidence_ids or [])
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate evidence IDs are not allowed.")
        if ids:
            count = self.db.query(Evidence.id).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(ids),
            ).count()
            if count != len(ids):
                raise ValueError("Every evidence reference must belong to the same care record.")
        return ids

    def create_information_gap(self, gap: InformationGapCreate) -> InformationGapModel:
        payload = self._scoped_payload(gap.model_dump())
        scoped_person_id = self.person_id or payload["person_id"]
        payload["person_id"] = scoped_person_id
        if payload.get("lifecycle_status", GapLifecycleStatus.OPEN) != GapLifecycleStatus.OPEN:
            raise ValueError("A new information gap must start open; transitions require an existing record.")
        payload["evidence_ids"] = self._validate_person_evidence(
            scoped_person_id, payload.get("evidence_ids")
        ) or None
        attention_assessments = [
            item for item in (payload.get("context") or [])
            if isinstance(item, dict) and item.get("caregiver_attention_required") is True
        ]
        for assessment in attention_assessments:
            rationale = assessment.get("necessity_rationale")
            dependency = assessment.get("decision_dependency")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("A necessary information-gap request requires a recorded rationale.")
            if not isinstance(dependency, str) or not dependency.strip():
                raise ValueError("A necessary information-gap request must identify the current care decision that depends on the missing information.")
            if not payload["evidence_ids"]:
                raise ValueError("A necessary information-gap request requires persisted source evidence.")
        if payload.get("resolution_date") or payload.get("resolution_notes") or payload.get("resolution_mechanism"):
            raise ValueError("A new information gap cannot carry a fabricated prior resolution.")
        db_gap = InformationGapModel(**payload)
        try:
            self.db.add(db_gap)
            self.db.flush()
            # Creating a structured, evidence-backed necessity assessment is
            # itself a source-workflow event. Materialize/reassess the single
            # shared question lifecycle in this transaction, not on a later
            # dashboard or manual refresh.
            from app.services.care_continuity import (
                next_best_questions,
                reassess_open_question_lifecycle,
            )
            next_best_questions(self.db, scoped_person_id, persist=True, commit=False)
            reassess_open_question_lifecycle(self.db, scoped_person_id)
            self.db.commit()
            self.db.refresh(db_gap)
            return db_gap
        except Exception:
            self.db.rollback()
            raise

    def update_information_gap(
        self, gap_id: str, update: InformationGapUpdate
    ) -> Optional[InformationGapModel]:
        db_gap = (
            self._target_query(InformationGapModel, gap_id).first()
        )
        if not db_gap:
            return None

        update_data = self._scoped_payload(update.model_dump(exclude_unset=True))
        scoped_person_id = self.person_id or db_gap.person_id
        if "evidence_ids" in update_data:
            update_data["evidence_ids"] = self._validate_person_evidence(
                scoped_person_id, update_data["evidence_ids"]
            ) or None

        context_for_assessment = update_data.get("context", db_gap.context) or []
        attention_assessments = [
            item for item in context_for_assessment
            if isinstance(item, dict) and item.get("caregiver_attention_required") is True
        ]
        evidence_for_assessment = update_data.get("evidence_ids", db_gap.evidence_ids) or []
        for assessment in attention_assessments:
            rationale = assessment.get("necessity_rationale")
            dependency = assessment.get("decision_dependency")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("A necessary information-gap request requires a recorded rationale.")
            if not isinstance(dependency, str) or not dependency.strip():
                raise ValueError("A necessary information-gap request must identify the current care decision that depends on the missing information.")
            if not evidence_for_assessment:
                raise ValueError("A necessary information-gap request requires persisted source evidence.")

        new_status = update_data.get("lifecycle_status", db_gap.lifecycle_status)
        if new_status == GapLifecycleStatus.RESOLVED:
            mechanism = update_data.get("resolution_mechanism", db_gap.resolution_mechanism)
            notes = update_data.get("resolution_notes", db_gap.resolution_notes)
            evidence_ids = update_data.get("evidence_ids", db_gap.evidence_ids) or []
            if mechanism is None:
                raise ValueError("Resolving an information gap requires an explicit resolution mechanism.")
            if not isinstance(notes, str) or not notes.strip():
                raise ValueError("Resolving an information gap requires a recorded rationale.")
            if mechanism in {"new_evidence", "source_confirmation"} and not evidence_ids:
                raise ValueError("This resolution mechanism requires linked supporting evidence.")
            update_data["resolution_date"] = datetime.now(timezone.utc)
        elif new_status == GapLifecycleStatus.REOPENED:
            # Reopening means the earlier resolution no longer settles the question.
            update_data["resolution_date"] = None
            update_data["resolution_mechanism"] = None
            update_data["resolution_notes"] = None
        elif new_status == GapLifecycleStatus.OPEN and db_gap.lifecycle_status != GapLifecycleStatus.OPEN:
            update_data["resolution_date"] = None
            update_data["resolution_mechanism"] = None
            update_data["resolution_notes"] = None

        for field, value in update_data.items():
            setattr(db_gap, field, value)

        try:
            # A necessity assessment added or withdrawn on an existing gap
            # must immediately create, suppress, or supersede its linked
            # caregiver request and append the lifecycle audit revision.
            from app.services.care_continuity import (
                next_best_questions,
                reassess_open_question_lifecycle,
            )
            next_best_questions(self.db, scoped_person_id, persist=True, commit=False)
            reassess_open_question_lifecycle(self.db, scoped_person_id)
            self.db.commit()
            self.db.refresh(db_gap)
            return db_gap
        except Exception:
            self.db.rollback()
            raise

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
        payload = self._scoped_payload(question.model_dump())
        scoped_person_id = self.person_id or payload["person_id"]
        payload["person_id"] = scoped_person_id
        if payload.get("status", "open") != "open":
            raise ValueError("A new clarification question must start open.")
        if payload.get("answer") or payload.get("answer_provenance"):
            raise ValueError("A new clarification question cannot contain a pre-recorded answer.")
        if payload.get("closed_reason"):
            raise ValueError("A new clarification question cannot contain a closure reason.")
        payload["evidence_ids"] = self._validate_person_evidence(
            scoped_person_id, payload.get("evidence_ids")
        ) or None
        claim_id = payload.get("claim_id")
        claim = None
        if claim_id:
            claim = self.db.query(CareAssertionModel).filter(
                CareAssertionModel.id == claim_id,
                CareAssertionModel.person_id == scoped_person_id,
            ).first()
            if claim is None:
                raise ValueError("A linked claim must belong to the same care record.")
            if claim.subject_type != payload["subject_type"] or claim.subject_id != payload["subject_id"]:
                raise ValueError("A clarification question must use the linked claim's subject scope.")
        if payload.get("necessity", "not_assessed") == "necessary":
            # A human-facing request must be grounded in persisted evidence,
            # not merely a database row that may itself have no source.
            linked_evidence = list(payload.get("evidence_ids") or [])
            if claim is not None:
                linked_evidence.extend(claim.evidence_ids or [])
                if claim.source_event_id:
                    source_event = self.db.query(CareEventModel).filter(
                        CareEventModel.id == claim.source_event_id,
                        CareEventModel.person_id == scoped_person_id,
                    ).first()
                    if source_event is not None:
                        linked_evidence.extend(source_event.evidence_ids or [])
            linked_evidence = self._validate_person_evidence(
                scoped_person_id, list(dict.fromkeys(linked_evidence))
            )
            if not linked_evidence:
                raise ValueError(
                    "A necessary caregiver question requires persisted source evidence linked to this care record."
                )
            payload["evidence_ids"] = linked_evidence
        allowed_answerer_roles = {
            "person_receiving_care", "reporting_caregiver", "other_caregiver",
            "professional", "not_known",
        }
        answerer_role = payload.get("intended_answerer_role")
        if answerer_role is not None and answerer_role not in allowed_answerer_roles:
            raise ValueError("Unsupported intended answerer role.")
        answerer_label = payload.get("intended_answerer_label")
        if answerer_label is not None:
            answerer_label = answerer_label.strip()
            payload["intended_answerer_label"] = answerer_label or None
        if answerer_role == "not_known" and answerer_label:
            raise ValueError("Do not attach a named answerer label when the intended answerer is explicitly unknown.")
        necessity = payload.get("necessity", "not_assessed")
        if necessity not in {"necessary", "optional", "not_assessed"}:
            raise ValueError("Necessity must be necessary, optional, or not_assessed.")
        necessity_rationale = payload.get("necessity_rationale")
        if necessity_rationale is not None:
            necessity_rationale = necessity_rationale.strip()
            payload["necessity_rationale"] = necessity_rationale or None
        if necessity in {"necessary", "optional"} and not necessity_rationale:
            raise ValueError("A necessity assessment requires a recorded rationale.")
        if necessity == "necessary" and not _has_decision_dependency(payload.get("context")):
            raise ValueError("A necessary caregiver question must identify the current care decision that depends on the answer.")
        question_text = (payload.get("question") or "").strip()
        if not question_text:
            raise ValueError("A clarification question must contain non-empty text.")
        payload["question"] = question_text
        # Suppress an exact normalized duplicate that is still open or unresolved for
        # the same person, subject, and claim. Answered/declined questions do not
        # block a new review; an unanswered question must be reopened or updated.
        question_key = re.sub(r"[\W_]+", " ", question_text.casefold()).strip()
        existing_open = (
            self.db.query(OpenQuestionModel)
            .filter(
                OpenQuestionModel.person_id == scoped_person_id,
                OpenQuestionModel.subject_type == payload["subject_type"],
                OpenQuestionModel.subject_id == payload["subject_id"],
                OpenQuestionModel.claim_id == claim_id,
                OpenQuestionModel.status.in_(["open", "unresolved"]),
            )
            .order_by(OpenQuestionModel.asked_at.desc(), OpenQuestionModel.id.asc())
            .limit(200)
            .all()
        )
        if any(
            re.sub(r"[\W_]+", " ", (row.question or "").casefold()).strip() == question_key
            for row in existing_open
        ):
            raise ValueError(
                "An equivalent unanswered clarification question already exists for this claim and subject; review its status and answer history before asking again."
            )
        db_question = OpenQuestionModel(**payload)
        manual_revision_objects = self.db.info.setdefault("manual_care_state_revision_objects", set())
        manual_revision_objects.add(id(db_question))
        try:
            self.db.add(db_question)
            self.db.flush()
            from app.services.care_continuity import _record_open_question_revision
            _record_open_question_revision(
                self.db, db_question, before_state=None, change_kind="created",
                actor="caregiver_question_creation",
                changed_by_caregiver_id=self.caregiver_id,
            )
            self.db.commit()
            self.db.refresh(db_question)
            return db_question
        except Exception:
            self.db.rollback()
            raise
        finally:
            manual_revision_objects.discard(id(db_question))

    def update_open_question(
        self, question_id: str, update: OpenQuestionUpdate
    ) -> Optional[OpenQuestionModel]:
        # Serialize answer transitions per question on PostgreSQL. Each answer
        # event receives a monotonic version even when timestamps share precision.
        db_question = (
            self._target_query(OpenQuestionModel, question_id).with_for_update().first()
        )
        if not db_question:
            return None

        manual_revision_objects = self.db.info.setdefault("manual_care_state_revision_objects", set())
        from app.services.care_continuity import _open_question_state, _record_open_question_revision
        before_state = _open_question_state(db_question)
        explicit_update_fields = set(getattr(update, "model_fields_set", set()))
        update_data = self._scoped_payload(update.model_dump(exclude_unset=True))
        # Keep caller-supplied answer evidence distinct from evidence inherited
        # to validate the question's necessity. Otherwise a necessary question's
        # original source can be mistaken for evidence of the caregiver's answer.
        explicit_answer_evidence_ids = (
            list(update_data.get("evidence_ids") or [])
            if "evidence_ids" in explicit_update_fields else []
        )
        if "evidence_ids" in update_data:
            update_data["evidence_ids"] = self._validate_person_evidence(
                db_question.person_id, update_data["evidence_ids"]
            ) or None
        new_necessity = update_data.get("necessity", db_question.necessity)
        new_necessity_rationale = update_data.get("necessity_rationale", db_question.necessity_rationale)
        if new_necessity not in {"necessary", "optional", "not_assessed"}:
            raise ValueError("Necessity must be necessary, optional, or not_assessed.")
        if new_necessity in {"necessary", "optional"} and not (
            isinstance(new_necessity_rationale, str) and new_necessity_rationale.strip()
        ):
            raise ValueError("A necessity assessment requires a recorded rationale.")
        if "context" in update_data:
            # Context updates are additive annotations, not destructive
            # replacement of the source and decision-dependency trail that
            # justified an existing necessary question.
            merged_context = list(db_question.context or [])
            for context_item in (update_data.get("context") or []):
                if context_item not in merged_context:
                    merged_context.append(context_item)
            update_data["context"] = merged_context
        context_for_necessity = update_data.get("context", db_question.context)
        if new_necessity == "necessary" and not _has_decision_dependency(context_for_necessity):
            raise ValueError("A necessary caregiver question must identify the current care decision that depends on the answer.")
        if new_necessity == "necessary":
            linked_evidence = list(update_data.get("evidence_ids", db_question.evidence_ids) or [])
            if db_question.claim_id:
                linked_claim = self.db.query(CareAssertionModel).filter(
                    CareAssertionModel.id == db_question.claim_id,
                    CareAssertionModel.person_id == db_question.person_id,
                ).first()
                if linked_claim is not None:
                    linked_evidence.extend(linked_claim.evidence_ids or [])
                    if linked_claim.source_event_id:
                        source_event = self.db.query(CareEventModel).filter(
                            CareEventModel.id == linked_claim.source_event_id,
                            CareEventModel.person_id == db_question.person_id,
                        ).first()
                        if source_event is not None:
                            linked_evidence.extend(source_event.evidence_ids or [])
            linked_evidence = self._validate_person_evidence(
                db_question.person_id, list(dict.fromkeys(linked_evidence))
            )
            if not linked_evidence:
                raise ValueError(
                    "A necessary caregiver question requires persisted source evidence linked to this care record."
                )
            update_data["evidence_ids"] = linked_evidence
        new_status = update_data.get("status", db_question.status)
        allowed_statuses = {"open", "answered", "declined", "closed", "unresolved"}
        if new_status not in allowed_statuses:
            raise ValueError(
                "Unsupported question status. Use open, answered, declined, closed, or unresolved."
            )

        answer = update_data.get("answer", db_question.answer)
        answer_provenance = update_data.get("answer_provenance", db_question.answer_provenance)
        closed_reason = update_data.get("closed_reason", db_question.closed_reason)
        answer_evidence_ids = []
        latest_answer_event = (
            self.db.query(OpenQuestionAnswerEventModel)
            .filter(
                OpenQuestionAnswerEventModel.question_id == db_question.id,
                OpenQuestionAnswerEventModel.person_id == db_question.person_id,
            )
            .order_by(OpenQuestionAnswerEventModel.answer_version.desc())
            .first()
        )
        answer_content_changed = (
            db_question.status != "answered"
            or ("answer" in explicit_update_fields and answer != db_question.answer)
            or ("answer_provenance" in explicit_update_fields and answer_provenance != db_question.answer_provenance)
            or (
                "evidence_ids" in explicit_update_fields
                and (
                    latest_answer_event is None
                    or explicit_answer_evidence_ids != list(latest_answer_event.evidence_ids or [])
                )
            )
        )
        if new_status == "answered" and answer_content_changed:
            supplied_evidence_ids = explicit_answer_evidence_ids
            if supplied_evidence_ids:
                answer_evidence_ids = self._validate_person_evidence(
                    db_question.person_id, supplied_evidence_ids
                )
            else:
                # A caregiver's answer is itself a source report. Save it as
                # append-only evidence in the same transaction so the caregiver
                # does not need to upload a separate document to answer a question.
                if not self.caregiver_id:
                    raise ValueError("Recording a clarification answer requires an authenticated caregiver identity.")
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("An answered question requires a non-empty answer.")
                if not isinstance(answer_provenance, str) or not answer_provenance.strip():
                    raise ValueError("An answered question requires answer provenance.")
                answer_evidence = EvidenceModel(
                    person_id=db_question.person_id,
                    type="note",
                    evidence_kind="caregiver_report",
                    epistemic_status="caregiver_reported",
                    source_text=f"Answer: {answer.strip()}\n\nProvided by: {answer_provenance.strip()}",
                    uploaded_by_caregiver_id=self.caregiver_id,
                    reported_by_caregiver_id=self.caregiver_id,
                    collected_by_caregiver_id=self.caregiver_id,
                    reported_by_label="Caregiver",
                    source_type="caregiver_report",
                    origin_channel="clarification_answer",
                    collection_method="caregiver_form",
                    time_provenance="server_recorded_at",
                    occurred_at=datetime.now(timezone.utc),
                )
                self.db.add(answer_evidence)
                self.db.flush()
                answer_evidence_ids = [answer_evidence.id]
            if not answer_evidence_ids:
                raise ValueError("A clarification answer must have a saved source report.")
            update_data["evidence_ids"] = list(dict.fromkeys([
                *(db_question.evidence_ids or []),
                *answer_evidence_ids,
            ]))
        elif new_status == "answered":
            answer_evidence_ids = list(
                (latest_answer_event.evidence_ids if latest_answer_event else None)
                or db_question.evidence_ids
                or []
            )
        if new_status == "answered":
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("An answered question requires a non-empty answer.")
            if not isinstance(answer_provenance, str) or not answer_provenance.strip():
                raise ValueError("An answered question requires answer provenance.")
        elif new_status == "declined":
            if not isinstance(closed_reason, str) or not closed_reason.strip():
                raise ValueError("A declined question requires a recorded reason.")
        elif new_status == "closed" and not (
            (isinstance(answer, str) and answer.strip())
            or (isinstance(closed_reason, str) and closed_reason.strip())
        ):
            raise ValueError("A question can only be closed with an answer or an explicit close reason.")

        now = datetime.now(timezone.utc)
        answer_text = answer.strip() if isinstance(answer, str) else answer
        answer_source = answer_provenance.strip() if isinstance(answer_provenance, str) else answer_provenance
        answer_was_recorded = new_status == "answered" and answer_content_changed and (
            db_question.status != "answered"
            or ("answer" in explicit_update_fields and answer_text != db_question.answer)
            or ("answer_provenance" in explicit_update_fields and answer_source != db_question.answer_provenance)
            or latest_answer_event is None
            or (
                "evidence_ids" in explicit_update_fields
                and list(latest_answer_event.evidence_ids or []) != answer_evidence_ids
            )
        )
        if answer_was_recorded:
            if not self.caregiver_id:
                raise ValueError("Recording a clarification answer requires an authenticated caregiver identity.")
            update_data["answer"] = answer_text
            update_data["answer_provenance"] = answer_source
            db_question.answered_by_caregiver_id = self.caregiver_id
            next_answer_version = (
                latest_answer_event.answer_version + 1 if latest_answer_event else 1
            )
            self.db.add(OpenQuestionAnswerEventModel(
                question_id=db_question.id,
                person_id=db_question.person_id,
                answer=answer_text,
                answer_provenance=answer_source,
                evidence_ids=answer_evidence_ids,
                answer_version=next_answer_version,
                answered_by_caregiver_id=self.caregiver_id,
            ))

        if new_status == "open" and db_question.status != "open":
            # Reopening does not erase the previous answer; the immutable answer
            # event remains available even if the question is reopened or corrected.
            db_question.closed_at = None
            db_question.closed_reason = None
        elif new_status in {"answered", "declined", "closed"}:
            db_question.closed_at = now
            if new_status == "answered" and answer_was_recorded:
                db_question.answered_at = now
            elif new_status == "declined":
                db_question.answered_at = None

        for field, value in update_data.items():
            setattr(db_question, field, value)

        try:
            if before_state != _open_question_state(db_question):
                manual_revision_objects.add(id(db_question))
                _record_open_question_revision(
                    self.db, db_question, before_state=before_state, change_kind="updated",
                    actor="caregiver_question_update",
                    changed_by_caregiver_id=self.caregiver_id,
                )
            self.db.commit()
            self.db.refresh(db_question)
            return db_question
        except Exception:
            self.db.rollback()
            raise
        finally:
            manual_revision_objects.discard(id(db_question))

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
        # This method backs the caregiver-facing questions endpoint. Unassessed
        # and optional candidates remain in the record for internal intelligence,
        # but must not be presented as caregiver work.
        query = self.db.query(OpenQuestionModel).filter(
            OpenQuestionModel.person_id == scoped_person_id,
            OpenQuestionModel.necessity == "necessary",
        )
        if subject_type:
            query = query.filter(OpenQuestionModel.subject_type == subject_type)
        if subject_id:
            query = query.filter(OpenQuestionModel.subject_id == subject_id)
        if status:
            query = query.filter(OpenQuestionModel.status == status)
        else:
            # The default caregiver projection is an active-work list, not an
            # archive. Closed/superseded questions remain available through
            # the explicit status filter and answer-history route.
            query = query.filter(OpenQuestionModel.status.in_(["open", "unresolved"]))
        from app.services.care_continuity import (
            has_valid_caregiver_necessity,
            prioritize_caregiver_questions,
        )
        rows = [
            row for row in query.all()
            if has_valid_caregiver_necessity(row, self.db, scoped_person_id)
        ]
        # Return the full unique queue for on-demand review; the caregiver
        # interface applies the shared critical-plus-two initial display cap.
        unique, _, _ = prioritize_caregiver_questions(rows, ordinary_limit=2)
        return unique

    def create_contradiction(self, contradiction: ContradictionCreate) -> ContradictionModel:
        from app.models.care import CareEvent, Evidence

        payload = self._scoped_payload(contradiction.model_dump())
        scoped_person_id = self.person_id or payload["person_id"]
        payload["person_id"] = scoped_person_id
        if payload.get("resolution_status", "unresolved") != "unresolved":
            raise ValueError("A new conflict must start unresolved; status changes require a separate audited transition")
        payload["resolution_status"] = "unresolved"
        payload["resolution_scope"] = None
        payload["unresolved_remainder"] = None
        payload["resolved_by_caregiver_id"] = None
        payload["matching_candidate_ids"] = []
        start = payload.get("effective_from")
        end = payload.get("effective_to")
        if start and end:
            start_utc = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
            end_utc = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
            if end_utc < start_utc:
                raise ValueError("Conflict effective_to cannot precede effective_from")

        event_id = payload.get("event_id")
        if event_id and not self.db.query(CareEvent.id).filter(
            CareEvent.id == event_id, CareEvent.person_id == scoped_person_id
        ).first():
            raise ValueError("Conflict event must belong to the same care record")

        evidence_ids = payload.get("evidence_ids") or []
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Duplicate evidence IDs are not allowed")
        if evidence_ids:
            evidence_count = self.db.query(Evidence.id).filter(
                Evidence.person_id == scoped_person_id,
                Evidence.id.in_(evidence_ids),
            ).count()
            if evidence_count != len(evidence_ids):
                raise ValueError("Every conflict evidence reference must belong to the same care record")

        query = self.db.query(ContradictionModel).filter(
            ContradictionModel.person_id == scoped_person_id,
            ContradictionModel.subject_type == payload["subject_type"],
            ContradictionModel.subject_id == payload["subject_id"],
            ContradictionModel.field == payload["field"],
            ContradictionModel.resolution_status.in_(("unresolved", "reopened", "qualified", "partially_resolved")),
        )
        candidates = []
        for existing in query.all():
            if event_id and existing.event_id and str(event_id) != str(existing.event_id):
                continue
            new_context = (payload.get("context_key") or "").strip().casefold()
            old_context = (existing.context_key or "").strip().casefold()
            if new_context and old_context and new_context != old_context:
                continue

            old_start, old_end = existing.effective_from, existing.effective_to
            new_start, new_end = start, end
            def utc(value):
                if value is None:
                    return None
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            old_start, old_end, new_start, new_end = map(utc, (old_start, old_end, new_start, new_end))
            # Exclude only when explicit bounds prove the intervals disjoint.
            if old_end and new_start and old_end < new_start:
                continue
            if new_end and old_start and new_end < old_start:
                continue
            candidates.append(str(existing.id))

        payload["matching_candidate_ids"] = sorted(set(candidates))
        db_contradiction = ContradictionModel(**payload)
        try:
            self.db.add(db_contradiction)
            self.db.commit()
            self.db.refresh(db_contradiction)
            return db_contradiction
        except Exception:
            self.db.rollback()
            raise

    def update_contradiction(
        self, contradiction_id: str, update: ContradictionUpdate
    ) -> Optional[ContradictionModel]:
        # Serialize competing conflict transitions on PostgreSQL so each immutable audit event records the actual preceding state. SQLite ignores FOR UPDATE, while PostgreSQL blocks concurrent writers until the latest row version is available.
        db_contradiction = self._target_query(ContradictionModel, contradiction_id).with_for_update().first()
        if not db_contradiction:
            return None

        update_data = self._scoped_payload(update.model_dump(exclude_unset=True))
        resolution_scope = (update_data.pop("resolution_scope", None) or "").strip() or None
        unresolved_remainder = (update_data.pop("unresolved_remainder", None) or "").strip() or None
        requested_status = update_data.get("resolution_status", db_contradiction.resolution_status)
        status_changed = requested_status != db_contradiction.resolution_status
        resolution_statuses = {"resolved", "partially_resolved", "qualified", "superseded", "reopened", "unresolved"}
        if requested_status not in resolution_statuses:
            raise ValueError("Unsupported conflict resolution status")
        rationale = (update_data.get("resolution_notes") or "").strip()
        evidence_ids = update_data.get("evidence_ids", db_contradiction.evidence_ids) or []
        if requested_status in {"resolved", "partially_resolved"} and not evidence_ids:
            raise ValueError("Resolving a conflict requires linked evidence from the same care record")
        if requested_status == "partially_resolved":
            if not resolution_scope:
                raise ValueError("A partial conflict resolution requires an explicit scope describing exactly what was resolved")
            if not unresolved_remainder:
                raise ValueError("A partial conflict resolution must preserve the unresolved remainder explicitly")
        elif resolution_scope or unresolved_remainder:
            raise ValueError("Resolution scope and unresolved remainder may only be set for a partial conflict resolution")
        audit_event_required = (
            status_changed
            or resolution_scope is not None
            or unresolved_remainder is not None
            or ("resolution_notes" in update_data and update_data.get("resolution_notes") != db_contradiction.resolution_notes)
            or ("evidence_ids" in update_data and evidence_ids != (db_contradiction.evidence_ids or []))
        )
        if audit_event_required and not rationale:
            raise ValueError("A non-empty rationale is required for conflict status, rationale, or evidence-link changes")
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
            if audit_event_required:
                event = ContradictionResolutionEventModel(
                    contradiction_id=db_contradiction.id,
                    person_id=db_contradiction.person_id,
                    actor_caregiver_id=self.caregiver_id,
                    previous_status=db_contradiction.resolution_status,
                    new_status=requested_status,
                    rationale=rationale,
                    resolution_scope=resolution_scope,
                    unresolved_remainder=unresolved_remainder,
                    evidence_ids=list(evidence_ids),
                )
                self.db.add(event)

            if status_changed:
                db_contradiction.resolution_scope = resolution_scope if requested_status == "partially_resolved" else None
                db_contradiction.unresolved_remainder = unresolved_remainder if requested_status == "partially_resolved" else None
            elif resolution_scope is not None or unresolved_remainder is not None:
                if resolution_scope is not None:
                    db_contradiction.resolution_scope = resolution_scope
                if unresolved_remainder is not None:
                    db_contradiction.unresolved_remainder = unresolved_remainder

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
        self, assessment: UncertaintyAssessmentCreate, *, commit: bool = True
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
            # Callers may include linked lifecycle records in the same transaction.
            if commit:
                self.db.commit()
                self.db.refresh(db_assessment)
            else:
                self.db.flush()
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
                OpenQuestionModel.necessity == "necessary",
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
                OpenQuestionModel.necessity == "necessary",
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
