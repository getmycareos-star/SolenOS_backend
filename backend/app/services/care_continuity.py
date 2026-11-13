from __future__ import annotations

from datetime import datetime, timezone, timedelta
import uuid
from typing import Any, Dict, List, Optional
import json

from fastapi import HTTPException
from sqlalchemy import MetaData, Table, desc, func, insert, inspect, select, text
from sqlalchemy.orm import Session

from app.models.care_continuity import CareActionOutcome, CareAssertion, CareStateRevision
from app.models.care import CareEvent, Evidence
from app.models.action import Action
from app.models.open_loop import OpenLoopRecord
from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion
from app.schemas.care_continuity import CareAssertionCreate, CareActionOutcomeCreate
from app.core.uncertainty_enums import GapLifecycleStatus, Priority, ResolutionMechanism


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _value_key(value: str) -> str:
    return " ".join(value.casefold().split())


def has_valid_caregiver_necessity(
    question: Any, db: Optional[Session] = None, person_id: Optional[str] = None
) -> bool:
    """Fail closed unless rationale, decision dependency and same-record evidence exist."""
    evidence_ids = list(dict.fromkeys(
        str(value) for value in (getattr(question, "evidence_ids", None) or []) if value
    ))
    rationale = getattr(question, "necessity_rationale", None)
    if not evidence_ids or not isinstance(rationale, str) or not rationale.strip():
        return False
    scoped_person_id = person_id or getattr(question, "person_id", None)
    if db is not None:
        if not scoped_person_id:
            return False
        found = {
            str(row[0]) for row in db.query(Evidence.id).filter(
                Evidence.person_id == scoped_person_id,
                Evidence.id.in_(evidence_ids),
            ).all()
        }
        if found != set(evidence_ids):
            return False
        claim_id = getattr(question, "claim_id", None)
        if claim_id:
            claim = db.query(CareAssertion).filter(
                CareAssertion.id == claim_id,
                CareAssertion.person_id == scoped_person_id,
            ).first()
            if claim is None:
                return False
            if (
                getattr(question, "subject_type", None)
                and claim.subject_type != question.subject_type
            ):
                return False
            if (
                getattr(question, "subject_id", None)
                and claim.subject_id != question.subject_id
            ):
                return False
            claim_evidence_ids = set(str(value) for value in (claim.evidence_ids or []) if value)
            if not claim_evidence_ids.intersection(evidence_ids):
                return False
    # The dependency must be tied to the same persisted source context that
    # grounds this question. Evidence attached elsewhere on the question must
    # not authorize an unrelated dependency in another context entry.
    for item in (getattr(question, "context", None) or []):
        if not isinstance(item, dict):
            continue
        nested = item.get("necessity_assessment")
        nested = nested if isinstance(nested, dict) else {}
        dependency_values = (
            item.get("decision_dependency"),
            nested.get("decision_dependency"),
        )
        has_dependency = any(
            isinstance(value, str) and value.strip()
            for value in dependency_values
        )
        source_kind = item.get("source_kind")
        source_id = item.get("source_id")
        source_ids = item.get("source_ids")
        has_source_reference = (
            isinstance(source_kind, str) and bool(source_kind.strip())
            and (
                (isinstance(source_id, str) and bool(source_id.strip()))
                or (
                    isinstance(source_ids, list)
                    and any(isinstance(value, str) and value.strip() for value in source_ids)
                )
            )
        )
        rationale_values = (
            item.get("necessity_rationale"),
            nested.get("necessity_rationale"),
        )
        has_rationale = any(
            isinstance(value, str) and value.strip()
            for value in rationale_values
        )
        assessment_evidence_ids = nested.get("evidence_ids")
        # The source-context assessment must explicitly name the evidence that
        # grounds this exact decision dependency. An omitted evidence list is
        # not proof of linkage, even when unrelated evidence is attached to
        # the question elsewhere.
        context_evidence_ids = {
            str(value) for value in assessment_evidence_ids
            if isinstance(value, str) and value.strip()
        } if isinstance(assessment_evidence_ids, list) else set()
        evidence_matches_context = bool(context_evidence_ids.intersection(evidence_ids))
        source_record_exists = True
        if db is not None:
            source_record_exists = False
            if source_kind == "information_gap":
                source_row = db.query(InformationGap).filter(
                    InformationGap.id == source_id,
                    InformationGap.person_id == scoped_person_id,
                    InformationGap.lifecycle_status.in_([
                        GapLifecycleStatus.OPEN,
                        GapLifecycleStatus.REOPENED,
                        GapLifecycleStatus.PARTIALLY_RESOLVED,
                    ]),
                ).first() if isinstance(source_id, str) else None
                source_record_exists = bool(
                    source_row
                    and source_row.subject_type == getattr(question, "subject_type", None)
                    and source_row.subject_id == getattr(question, "subject_id", None)
                )
            elif source_kind in {"daily_capture_extraction", "document_extraction"}:
                source_refs = item.get("source_record_refs")
                normalized_refs = [
                    source_ref for source_ref in source_refs
                    if isinstance(source_ref, dict)
                    and isinstance(source_ref.get("record_id"), str)
                    and source_ref.get("record_id").strip()
                ] if isinstance(source_refs, list) else []
                for source_ref in normalized_refs:
                    record_type = source_ref.get("record_type")
                    record_id = source_ref.get("record_id")
                    if record_id != source_id:
                        continue
                    if record_type == "evidence" and db.query(Evidence.id).filter(
                        Evidence.id == record_id, Evidence.person_id == scoped_person_id
                    ).first():
                        source_record_exists = True
                    elif record_type == "care_event" and db.query(CareEvent.id).filter(
                        CareEvent.id == record_id, CareEvent.person_id == scoped_person_id
                    ).first():
                        source_record_exists = True
            elif source_kind == "contradiction":
                source_row = db.query(Contradiction).filter(
                    Contradiction.id == source_id,
                    Contradiction.person_id == scoped_person_id,
                    Contradiction.resolution_status.in_(("unresolved", "reopened", "partially_resolved", "qualified")),
                ).first() if isinstance(source_id, str) else None
                source_record_exists = bool(
                    source_row
                    and source_row.subject_type == getattr(question, "subject_type", None)
                    and source_row.subject_id == getattr(question, "subject_id", None)
                )
            elif source_kind == "stale_or_unreviewed_assertion":
                source_row = db.query(CareAssertion).filter(
                    CareAssertion.id == source_id,
                    CareAssertion.person_id == scoped_person_id,
                    CareAssertion.assertion_status.in_(("reported", "review_required", "accepted", "caregiver_reviewed")),
                ).first() if isinstance(source_id, str) else None
                source_record_exists = bool(
                    source_row
                    and source_row.subject_type == getattr(question, "subject_type", None)
                    and source_row.subject_id == getattr(question, "subject_id", None)
                )
            elif source_kind == "registered_action":
                source_record_exists = bool(
                    isinstance(source_id, str) and db.query(Action.id).filter(
                        Action.id == source_id,
                        Action.person_id == scoped_person_id,
                        Action.status.in_(("needs_clarification", "pending_confirmation")),
                        Action.requires_confirmation.is_(True),
                        Action.expired_at.is_(None),
                        Action.cancelled_at.is_(None),
                        Action.superseded_by_action_id.is_(None),
                    ).first()
                )
            elif source_kind == "appointment_workspace_follow_up":
                source_record_exists = bool(
                    isinstance(source_id, str) and db.query(OpenLoopRecord.id).filter(
                        OpenLoopRecord.id == source_id,
                        OpenLoopRecord.person_id == scoped_person_id,
                        OpenLoopRecord.source_type == "appointment_workspace_follow_up",
                        OpenLoopRecord.status == "blocked",
                    ).first()
                )
        if (
            has_dependency and has_source_reference and has_rationale
            and evidence_matches_context and source_record_exists
        ):
            return True
    return False


def _open_question_state(question: OpenQuestion) -> Dict[str, Any]:
    return {
        "id": question.id,
        "question": question.question,
        "subject_type": question.subject_type,
        "subject_id": question.subject_id,
        "status": question.status,
        "necessity": question.necessity,
        "necessity_rationale": question.necessity_rationale,
        "intended_answerer_role": question.intended_answerer_role,
        "priority": str(getattr(question.priority, "value", question.priority)),
        "evidence_ids": list(question.evidence_ids or []),
        "context": list(question.context or []),
        "closed_at": question.closed_at.isoformat() if question.closed_at else None,
        "closed_reason": question.closed_reason,
        "answered_at": question.answered_at.isoformat() if question.answered_at else None,
        "answer": question.answer,
        "answer_provenance": question.answer_provenance,
    }


def _record_open_question_revision(
    db: Session,
    question: OpenQuestion,
    *,
    before_state: Optional[Dict[str, Any]],
    change_kind: str = "updated",
    actor: str = "solenos_reconciliation",
    changed_by_caregiver_id: Optional[str] = None,
) -> None:
    """Append an immutable care-state revision for every automated question transition.

    Serialize revision-number allocation per target on PostgreSQL. A read-max-then-
    insert without a lock races under concurrent answer/reconciliation requests and
    violates the unique target/revision constraint.
    """
    # Mark before any query: SQLAlchemy autoflush can otherwise invoke the
    # generic revision listener while allocating this explicit revision.
    db.info.setdefault("manual_care_state_revision_objects", set()).add(id(question))
    if db.get_bind().dialect.name == "postgresql":
        lock_key = f"care-state-revision:open_questions:{question.person_id}:{question.id}"
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
    latest = db.query(CareStateRevision.revision_number).filter(
        CareStateRevision.person_id == question.person_id,
        CareStateRevision.target_type == "open_questions",
        CareStateRevision.target_id == question.id,
    ).order_by(CareStateRevision.revision_number.desc()).first()
    revision_number = (latest[0] if latest else 0) + 1
    after_state = _open_question_state(question)
    after_state["lifecycle_actor"] = actor
    # The ORM revision listener also tracks open_questions. Mark this object
    # before the flush so this explicit, actor-attributed revision is the only
    # revision for the transition.
    db.add(CareStateRevision(
        id=str(uuid.uuid4()),
        person_id=question.person_id,
        target_type="open_questions",
        target_id=question.id,
        revision_number=revision_number,
        change_kind=change_kind,
        before_state=json.dumps(before_state, sort_keys=True, ensure_ascii=False, default=str) if before_state is not None else None,
        after_state=json.dumps(after_state, sort_keys=True, ensure_ascii=False, default=str),
        evidence_ids=list(question.evidence_ids or []),
        changed_by_caregiver_id=changed_by_caregiver_id,
    ))
    # Make the revision visible to any subsequent transition in this same
    # transaction, including sessions configured with autoflush=False.
    db.flush()


def create_assertion(db: Session, payload: CareAssertionCreate, *, commit: bool = True) -> CareAssertion:
    """Store every report; create/update unresolved conflict metadata without selecting a winner."""
    if payload.epistemic_state == "verified":
        raise HTTPException(status_code=422, detail="Verified assertions require a persisted verification review; direct assertion creation cannot self-certify")
    if payload.epistemic_state in {"conflicting", "missing"}:
        raise HTTPException(status_code=422, detail="Conflicting and missing are derived record states and cannot be assigned to an individual assertion")
    assertion = CareAssertion(**payload.model_dump())
    # AI proposal lineage is an interpretation, never a caregiver report or verified fact.
    if payload.source_proposal_id:
        assertion.epistemic_state = "inferred"
    if payload.source_event_id:
        source_event = db.query(CareEvent).filter(
            CareEvent.id == payload.source_event_id,
            CareEvent.person_id == payload.person_id,
        ).first()
        if source_event is None:
            raise HTTPException(status_code=422, detail="source_event_id must belong to the same care record")
    if payload.evidence_ids:
        evidence_rows = db.query(Evidence.id).filter(
            Evidence.person_id == payload.person_id,
            Evidence.id.in_(payload.evidence_ids),
        ).all()
        found = {row[0] for row in evidence_rows}
        if found != set(payload.evidence_ids):
            raise HTTPException(status_code=422, detail="Every evidence ID must exist and belong to the same care record")
    assertion.assertion_status = "reported"
    db.add(assertion)
    db.flush()

    peers = (
        db.query(CareAssertion)
        .filter(
            CareAssertion.person_id == payload.person_id,
            CareAssertion.subject_type == payload.subject_type,
            CareAssertion.subject_id == payload.subject_id,
            CareAssertion.field == payload.field,
            CareAssertion.assertion_kind.in_((
                "current_state", "document_reviewed_observation", "time_bound_observation",
            )),
            CareAssertion.id != assertion.id,
            CareAssertion.assertion_status.in_(["reported", "review_required", "accepted", "caregiver_reviewed"]),
        )
        .order_by(CareAssertion.recorded_at.asc())
        .all()
    )
    conflicts = [peer for peer in peers if _value_key(peer.asserted_value) != _value_key(assertion.asserted_value)]
    if conflicts:
        evidence_ids = list(dict.fromkeys(
            [item for peer in conflicts for item in (peer.evidence_ids or [])]
            + list(payload.evidence_ids or [])
        ))
        descriptions = sorted(set([peer.asserted_value for peer in conflicts] + [assertion.asserted_value]))
        contradiction = (
            db.query(Contradiction)
            .filter(
                Contradiction.person_id == payload.person_id,
                Contradiction.subject_type == payload.subject_type,
                Contradiction.subject_id == payload.subject_id,
                Contradiction.field == payload.field,
                Contradiction.resolution_status.in_(("unresolved", "reopened", "qualified", "partially_resolved")),
            )
            .first()
        )
        conflict_text = "Competing source assertions require review; no assertion was selected as true. Reported values: " + " | ".join(descriptions)
        source_assertion_ids = list(dict.fromkeys([peer.id for peer in conflicts] + [assertion.id]))
        evidence_ids = list(dict.fromkeys(
            [item for peer in conflicts for item in (peer.evidence_ids or [])]
            + list(payload.evidence_ids or [])
        ))
        # A single timestamp does not prove that two assertions describe
        # different moments. Qualify only when at least two comparable source
        # time values are present and actually differ. A timestamp cannot be
        # compared safely with free-text time wording.
        observed_time_keys = []
        event_time_keys = []
        for candidate in [*conflicts, assertion]:
            if candidate.observed_at is not None:
                observed_at = candidate.observed_at
                if observed_at.tzinfo is None:
                    observed_at = observed_at.replace(tzinfo=timezone.utc)
                else:
                    observed_at = observed_at.astimezone(timezone.utc)
                observed_time_keys.append(observed_at.isoformat())
            elif candidate.event_time_text and candidate.event_time_text.strip():
                event_time_keys.append(" ".join(candidate.event_time_text.casefold().split()))
        has_distinct_time_context = (
            len(set(observed_time_keys)) > 1
            or len(set(event_time_keys)) > 1
        )
        proposed_conflict_status = "qualified" if has_distinct_time_context else "unresolved"
        if contradiction:
            contradiction.description = conflict_text
            contradiction.evidence_ids = evidence_ids
            contradiction.source_assertion_ids = list(dict.fromkeys((contradiction.source_assertion_ids or []) + source_assertion_ids))
            if contradiction.resolution_status != "unresolved":
                contradiction.resolution_status = proposed_conflict_status
        else:
            contradiction = Contradiction(
                person_id=payload.person_id,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                field=payload.field,
                description=conflict_text,
                evidence_ids=evidence_ids,
                source_assertion_ids=source_assertion_ids,
                resolution_status=proposed_conflict_status,
            )
            db.add(contradiction)
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(assertion)
    return assertion


def _latest_assertion_review_at(db: Session, assertion: CareAssertion) -> Optional[datetime]:
    """Read the append-only review timestamp without mutating source event time."""
    # Reflect through the Session's active connection, not the Engine. With
    # SQLite StaticPool an Engine-level reflection checkout can reuse the same
    # DBAPI connection and roll back the caller's uncommitted assertion writes.
    connection = db.connection()
    if not inspect(connection).has_table("care_assertion_revisions"):
        return None
    revisions = Table("care_assertion_revisions", MetaData(), autoload_with=connection)
    return db.execute(
        select(revisions.c.changed_at).where(
            revisions.c.person_id == assertion.person_id,
            revisions.c.assertion_id == assertion.id,
        ).order_by(desc(revisions.c.revision_number)).limit(1)
    ).scalar_one_or_none()


def _append_assertion_revision(
    db: Session,
    assertion: CareAssertion,
    previous_payload: dict,
    reason: str,
    changed_by_caregiver_id: Optional[str],
) -> Optional[datetime]:
    """Persist an immutable assertion review/version in the existing revision table."""
    connection = db.connection()
    if not inspect(connection).has_table("care_assertion_revisions"):
        return None
    revisions = Table("care_assertion_revisions", MetaData(), autoload_with=connection)
    latest = db.execute(select(func.max(revisions.c.revision_number)).where(
        revisions.c.person_id == assertion.person_id,
        revisions.c.assertion_id == assertion.id,
    )).scalar_one_or_none()
    changed_at = datetime.now(timezone.utc)
    current_payload = {
        "id": assertion.id,
        "person_id": assertion.person_id,
        "assertion_status": assertion.assertion_status,
        "review_after_days": assertion.review_after_days,
        "epistemic_state": assertion.epistemic_state,
        "asserted_value": assertion.asserted_value,
        "evidence_ids": list(assertion.evidence_ids or []),
    }
    db.execute(insert(revisions).values(
        id=str(uuid.uuid4()),
        person_id=assertion.person_id,
        assertion_id=assertion.id,
        revision_number=int(latest or 0) + 1,
        previous_payload=previous_payload,
        current_payload=current_payload,
        revision_reason=reason,
        changed_by_caregiver_id=changed_by_caregiver_id,
        changed_at=changed_at,
    ))
    return changed_at


def set_assertion_status(db: Session, assertion_id: str, person_id: str, status: str, updated_by_caregiver_id: Optional[str] = None, review_after_days: Optional[int] = None) -> CareAssertion:
    allowed = {"reported", "review_required", "accepted", "rejected", "superseded"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported assertion status")
    assertion = db.query(CareAssertion).filter(
        CareAssertion.id == assertion_id, CareAssertion.person_id == person_id
    ).with_for_update().first()
    if assertion is None:
        raise HTTPException(status_code=404, detail="Assertion not found")
    if status == "accepted" and assertion.assertion_kind == "current_state" and review_after_days is None and assertion.review_after_days is None:
        raise HTTPException(status_code=422, detail="Set a review window before marking a current-state assertion as accepted")
    previous_payload = {
        "id": assertion.id,
        "person_id": assertion.person_id,
        "assertion_status": assertion.assertion_status,
        "review_after_days": assertion.review_after_days,
        "epistemic_state": assertion.epistemic_state,
        "asserted_value": assertion.asserted_value,
        "evidence_ids": list(assertion.evidence_ids or []),
    }
    previous_review_state = (assertion.assertion_status, assertion.review_after_days)
    if review_after_days is not None:
        assertion.review_after_days = review_after_days
    assertion.assertion_status = status
    assertion.updated_by_caregiver_id = updated_by_caregiver_id
    db.flush()
    if previous_review_state != (assertion.assertion_status, assertion.review_after_days):
        _append_assertion_revision(
            db,
            assertion,
            previous_payload,
            "Caregiver explicitly reviewed the source-attributed assertion",
            updated_by_caregiver_id,
        )
    if assertion.recorded_at is None:
        db.refresh(assertion, attribute_names=["recorded_at"])
    # Assertion review is a source-workflow event. Reconcile the affected care
    # record in the same transaction so accepted evidence can settle exact
    # information gaps and obsolete requests are lifecycle-managed immediately.
    from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence
    reconcile_person_change_intelligence(
        db, person_id, protected_assertion_ids=[assertion.id]
    )
    db.commit()
    db.refresh(assertion)
    return assertion


def list_revisions(db: Session, person_id: str, target_type: Optional[str], target_id: Optional[str]) -> List[CareStateRevision]:
    query = db.query(CareStateRevision).filter(CareStateRevision.person_id == person_id)
    if target_type:
        query = query.filter(CareStateRevision.target_type == target_type)
    if target_id:
        query = query.filter(CareStateRevision.target_id == target_id)
    return query.order_by(CareStateRevision.changed_at.desc(), CareStateRevision.revision_number.desc()).limit(500).all()


def revision_response(revision: CareStateRevision) -> Dict[str, Any]:
    return {
        "id": revision.id,
        "person_id": revision.person_id,
        "target_type": revision.target_type,
        "target_id": revision.target_id,
        "revision_number": revision.revision_number,
        "change_kind": revision.change_kind,
        "before_state": json.loads(revision.before_state) if revision.before_state else None,
        "after_state": json.loads(revision.after_state) if revision.after_state else None,
        "evidence_ids": revision.evidence_ids or [],
        "changed_by_caregiver_id": revision.changed_by_caregiver_id,
        "changed_at": revision.changed_at,
    }


def get_freshness(
    assertion: CareAssertion,
    now: Optional[datetime] = None,
    *,
    unresolved_conflict: bool = False,
    reviewed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = _utc(now or datetime.now(timezone.utc))
    # A caregiver's explicit review starts a new review window. Keep the
    # original recorded_at/observed_at untouched as source provenance.
    review_reference = reviewed_at if assertion.assertion_status in {"accepted", "caregiver_reviewed"} else None
    reference = _utc(review_reference) or _utc(assertion.observed_at) or _utc(assertion.recorded_at)
    age_days = max(0, (now - reference).days) if reference else None
    if assertion.assertion_kind == "time_bound_observation":
        status = "time_bound_observation"
        explanation = "This is a time-bound observation, not a claim about the current state."
    elif assertion.assertion_status in {"rejected", "superseded"}:
        status = "historical"
        explanation = "This assertion was explicitly rejected or superseded; it must not be presented as current."
    elif unresolved_conflict:
        status = "needs_confirmation"
        explanation = "A competing report for this same field remains unresolved. This report must not be presented as the established current state, even if a caregiver reviewed it."
    elif assertion.review_after_days is None or age_days is None or age_days > assertion.review_after_days:
        status = "needs_confirmation"
        explanation = "The review window is missing/elapsed or the observation time is unavailable; confirm before treating this report as current."
    elif assertion.assertion_status not in {"accepted", "caregiver_reviewed"}:
        status = "needs_confirmation"
        explanation = "This is a reported assertion, not an accepted current fact."
    else:
        status = "within_review_window"
        explanation = "Accepted assertion is within its configured review window; this does not independently prove it remains true."
    return {
        "assertion_id": assertion.id,
        "field": assertion.field,
        "status": status,
        "age_days": age_days,
        "review_after_days": assertion.review_after_days,
        "assertion_status": assertion.assertion_status,
        "explanation": explanation,
    }


def _priority_weight(value: Any) -> int:
    value = getattr(value, "value", value)
    return {"critical": 50, "high": 40, "medium": 30, "low": 20, "informational": 10}.get(str(value), 10)


def _question_has_new_grounding(question: OpenQuestion, candidate: Dict[str, Any]) -> bool:
    """A closed system question reopens only for a new persisted source or evidence."""
    prior_source_ids = set()
    for context in (question.context or []):
        if not isinstance(context, dict):
            continue
        source_ids = context.get("source_ids") or [context.get("source_id")]
        prior_source_ids.update(str(value) for value in source_ids if value)
    candidate_source_ids = {
        str(value) for value in (candidate.get("source_ids") or [candidate.get("source_id")])
        if value
    }
    prior_evidence_ids = {str(value) for value in (question.evidence_ids or []) if value}
    candidate_evidence_ids = {str(value) for value in (candidate.get("evidence_ids") or []) if value}
    return bool(
        candidate_source_ids - prior_source_ids
        or candidate_evidence_ids - prior_evidence_ids
    )


def next_best_questions(
    db: Session,
    person_id: str,
    persist: bool = True,
    *,
    commit: bool = True,
) -> List[Dict[str, Any]]:
    """Return internal clarification candidates; only explicitly necessary questions become caregiver work."""
    candidates: List[Dict[str, Any]] = []
    contradictions = db.query(Contradiction).filter(
        Contradiction.person_id == person_id,
        Contradiction.resolution_status.in_(("unresolved", "reopened", "partially_resolved", "qualified")),
    ).all()
    for item in contradictions:
        is_medication_conflict = (
            str(item.subject_type or "").strip().casefold() in {"medication", "medications"}
            or "medication" in str(item.field or "").casefold()
        )
        if is_medication_conflict:
            # A competing current medication instruction is a concrete decision
            # dependency. Keep the prescription unresolved and direct confirmation
            # to a qualified professional; never generate a start/stop/dose action.
            candidates.append({
                "question": "Can the prescribing clinician or pharmacist confirm the current instruction for this medication?",
                "subject_type": item.subject_type,
                "subject_id": item.subject_id,
                "priority": "high",
                "necessity": "necessary",
                "necessity_rationale": (
                    "Different current medication reports remain in the care record. "
                    "SolenOS cannot establish whether the instruction changed; a prescribing "
                    "clinician or pharmacist must confirm it before the family relies on the reported change."
                ),
                "decision_dependency": (
                    "The current medication instruction remains unconfirmed. Do not treat the "
                    "reported start, stop, or dose change as established until a qualified professional confirms it."
                ),
                "reason": "A source-linked medication contradiction remains active; no report was selected as correct.",
                "evidence_ids": item.evidence_ids or [],
                "source_kind": "contradiction",
                "source_id": item.id,
                "source_ids": item.source_assertion_ids or [],
                "intended_answerer_role": "professional",
                "_weight": 50,
            })
        elif item.resolution_status == "unresolved":
            candidates.append({
                "question": f"Two different reports conflict about {item.field}. What, if anything, confirms the current situation? Neither report has been selected as correct.",
                "subject_type": item.subject_type, "subject_id": item.subject_id,
                "priority": "high", "necessity": "not_assessed", "necessity_rationale": "A contradiction is retained for internal reconciliation; its presence alone does not establish that caregiver input is necessary.", "reason": "An unresolved contradiction prevents a reliable current-state conclusion.",
                "evidence_ids": item.evidence_ids or [], "source_kind": "contradiction", "source_id": item.id, "source_ids": item.source_assertion_ids or [], "_weight": 40,
            })

    assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.assertion_status.in_(["reported", "review_required", "accepted", "caregiver_reviewed"]),
    ).all()
    uncertainty_records = db.query(Contradiction).filter(
        Contradiction.person_id == person_id,
        Contradiction.resolution_status.in_(["unresolved", "reopened", "partially_resolved", "qualified"]),
    ).all()
    unresolved_keys = {
        (item.subject_type, item.subject_id, item.field)
        for item in uncertainty_records
    }
    now = datetime.now(timezone.utc)
    for item in assertions:
        has_conflict = (
            item.assertion_kind == "current_state"
            and (item.subject_type, item.subject_id, item.field) in unresolved_keys
        )
        # The contradiction itself already generates the higher-priority,
        # source-linked question. Do not emit a duplicate stale-report question.
        if has_conflict:
            continue
        freshness = get_freshness(item, now, reviewed_at=_latest_assertion_review_at(db, item))
        if freshness["status"] == "needs_confirmation":
            candidates.append({
                "question": f"Can you confirm whether the reported information about {item.field} is still accurate? The record does not establish that it is current.",
                "subject_type": item.subject_type, "subject_id": item.subject_id,
                "priority": "medium", "necessity": "not_assessed", "necessity_rationale": "Age or an unreviewed status alone does not establish that caregiver input is necessary; monitor internally until a current care decision depends on it.", "reason": freshness["explanation"],
                "evidence_ids": item.evidence_ids or [], "source_kind": "stale_or_unreviewed_assertion",
                "source_id": item.id, "_weight": 30,
            })

    # Registered actions that are explicitly blocked for clarification are
    # a deterministic source of necessary questions. This does not infer
    # clinical urgency from model confidence or priority: the action itself
    # must require confirmation, remain in needs_clarification, and cite
    # persisted evidence from this same care record.
    blocked_actions = db.query(Action).filter(
        Action.person_id == person_id,
        Action.status.in_(("needs_clarification", "pending_confirmation")),
        Action.requires_confirmation.is_(True),
        Action.expired_at.is_(None),
        Action.cancelled_at.is_(None),
        Action.superseded_by_action_id.is_(None),
    ).order_by(Action.created_at.asc(), Action.id.asc()).all()
    action_evidence_ids = list(dict.fromkeys(
        str(value)
        for action in blocked_actions
        for value in ([action.source_evidence_id] + list(action.source_evidence_ids or []))
        if value
    ))
    valid_action_evidence_ids = {
        str(row[0]) for row in db.query(Evidence.id).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_(action_evidence_ids),
        ).all()
    } if action_evidence_ids else set()
    for action in blocked_actions:
        source_ids = list(dict.fromkeys(
            str(value) for value in ([action.source_evidence_id] + list(action.source_evidence_ids or []))
            if value
        ))
        if not source_ids or not set(source_ids).issubset(valid_action_evidence_ids):
            continue
        is_medication = bool(action.is_medication_action)
        subject = (
            str(action.subject_label or action.subject_id or "the recorded medication")
            if is_medication else
            str(action.subject_label or action.subject_id or "the recorded care action")
        )
        action_text = str(action.normalized_action or action.original_text or action.action_object or "").strip()
        if is_medication:
            question_text = f"Can the prescribing clinician confirm the current instruction for {subject}?"
            answerer_role = "professional"
            rationale = (
                "This medication action is explicitly marked as needing clarification and requiring confirmation. "
                "The instruction must not be acted on until its source is confirmed."
            )
            dependency = (
                f"Medication action {action.id} is blocked pending confirmation; the care plan cannot rely on "
                "this instruction until a responsible prescribing professional confirms it."
            )
        else:
            question_text = f"Who can confirm the recorded care action for {subject} before it proceeds?"
            answerer_role = "not_known"
            rationale = (
                "This registered care action is explicitly marked as needing clarification and requiring confirmation. "
                "The action must not proceed until the responsible source confirms it."
            )
            dependency = (
                f"Care action {action.id} is blocked pending confirmation; the next care step depends on "
                "a responsible source confirming this recorded instruction."
            )
        candidates.append({
            "question": question_text,
            "subject_type": str(action.subject_type or "patient"),
            "subject_id": str(action.subject_id or person_id),
            "priority": "informational",
            "necessity": "necessary",
            "necessity_rationale": rationale,
            "decision_dependency": dependency,
            "reason": (
                f"Persisted action status is needs_clarification; requires_confirmation is true. "
                f"Source action: {action_text[:500]}"
            ),
            "evidence_ids": source_ids,
            "source_kind": "registered_action",
            "source_id": str(action.id),
            "source_ids": [str(action.id)],
            "intended_answerer_role": answerer_role,
            "_weight": 20,
        })

    # Appointment follow-ups become caregiver questions only when the
    # saved workspace explicitly marks the item blocked and supplies a
    # specific question, decision dependency, rationale, and same-record
    # evidence. A due date or missing owner alone never creates a question.
    blocked_appointment_followups = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == person_id,
        OpenLoopRecord.source_type == "appointment_workspace_follow_up",
        OpenLoopRecord.status == "blocked",
    ).order_by(OpenLoopRecord.created_at.asc(), OpenLoopRecord.id.asc()).all()
    for loop in blocked_appointment_followups:
        assessment = next((
            item.get("clarification_assessment")
            for item in (loop.evidence or [])
            if isinstance(item, dict)
            and isinstance(item.get("clarification_assessment"), dict)
        ), None)
        if not isinstance(assessment, dict):
            continue
        question_text = assessment.get("question")
        rationale = assessment.get("necessity_rationale")
        dependency = assessment.get("decision_dependency")
        evidence_ids = list(dict.fromkeys(
            str(value) for value in (assessment.get("evidence_ids") or []) if value
        ))
        # Evidence IDs are persisted alongside the structured assessment in
        # the loop's source event; validate them against this person's record.
        if not evidence_ids:
            evidence_ids = list(dict.fromkeys(
                str(value)
                for item in (loop.evidence or []) if isinstance(item, dict)
                for value in (item.get("evidence_ids") or [])
                if value
            ))
        valid_ids = {
            str(row[0]) for row in db.query(Evidence.id).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(evidence_ids),
            ).all()
        } if evidence_ids else set()
        if not (
            isinstance(question_text, str) and question_text.strip().endswith("?")
            and isinstance(rationale, str) and rationale.strip()
            and isinstance(dependency, str) and dependency.strip()
            and evidence_ids and valid_ids == set(evidence_ids)
        ):
            continue
        candidates.append({
            "question": question_text.strip()[:300],
            "subject_type": "appointment_follow_up",
            "subject_id": str(loop.id),
            "priority": "informational",
            "necessity": "necessary",
            "necessity_rationale": rationale.strip(),
            "decision_dependency": dependency.strip(),
            "reason": "The saved appointment follow-up is explicitly blocked pending the source-backed clarification.",
            "evidence_ids": evidence_ids,
            "source_kind": "appointment_workspace_follow_up",
            "source_id": str(loop.id),
            "source_ids": [str(loop.id)],
            "intended_answerer_role": assessment.get("intended_answerer_role") or "not_known",
            "_weight": 25,
        })

    gaps = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_([GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED, GapLifecycleStatus.PARTIALLY_RESOLVED]),
    ).all()
    all_gap_evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for gap in gaps
        for evidence_id in (gap.evidence_ids or [])
        if evidence_id
    ))
    valid_gap_evidence_ids = set()
    if all_gap_evidence_ids:
        valid_gap_evidence_ids = {
            str(row[0]) for row in db.query(Evidence.id).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(all_gap_evidence_ids),
            ).all()
        }
    for item in gaps:
        priority = getattr(item.priority, "value", item.priority)
        # A gap becomes caregiver work only when a deterministic upstream
        # workflow explicitly records that a current care decision depends on
        # the missing information. Priority alone is never a clinical-urgency
        # or necessity signal.
        explicit_assessment = next((
            entry for entry in (item.context or [])
            if isinstance(entry, dict)
            and entry.get("caregiver_attention_required") is True
            and isinstance(entry.get("necessity_rationale"), str)
            and entry.get("necessity_rationale", "").strip()
            and isinstance(entry.get("decision_dependency"), str)
            and entry.get("decision_dependency", "").strip()
            and bool(item.evidence_ids)
            and {
                str(evidence_id) for evidence_id in (item.evidence_ids or []) if evidence_id
            }.issubset(valid_gap_evidence_ids)
        ), None)
        necessity = "necessary" if explicit_assessment else "not_assessed"
        necessity_rationale = (
            explicit_assessment["necessity_rationale"].strip()
            if explicit_assessment else
            "An information gap remains part of the care record. Missing information alone does not establish that asking the caregiver is necessary."
        )
        decision_dependency = (
            explicit_assessment["decision_dependency"].strip()
            if explicit_assessment else None
        )
        candidates.append({
            "question": f"What is currently known about {item.field}?",
            "subject_type": item.subject_type, "subject_id": item.subject_id,
            "priority": priority if priority in {"critical", "high", "medium", "low", "informational"} else "informational",
            "necessity": necessity, "necessity_rationale": necessity_rationale,
            "decision_dependency": decision_dependency,
            "reason": item.description or f"The record identifies {item.field} as missing or incomplete.",
            "evidence_ids": item.evidence_ids or [], "source_kind": "information_gap", "source_id": item.id,
            "_weight": _priority_weight(priority),
        })

    # Collapse identical questions from repeated stale assertions while retaining every source.
    deduplicated: Dict[tuple, Dict[str, Any]] = {}
    for item in candidates:
        key = (item["question"], item["subject_type"], item["subject_id"])
        if key not in deduplicated:
            item["source_ids"] = item.get("source_ids") or [item["source_id"]]
            deduplicated[key] = item
            continue
        prior = deduplicated[key]
        prior["evidence_ids"] = list(dict.fromkeys(prior["evidence_ids"] + item["evidence_ids"]))
        prior["source_ids"] = list(dict.fromkeys(prior["source_ids"] + (item.get("source_ids") or [item["source_id"]])))
        if item.get("necessity") == "necessary" and prior.get("necessity") != "necessary":
            prior["necessity"] = "necessary"
            prior["necessity_rationale"] = item.get("necessity_rationale")
            prior["decision_dependency"] = item.get("decision_dependency")
        if item["_weight"] > prior["_weight"]:
            prior["priority"] = item["priority"]
            prior["_weight"] = item["_weight"]
            prior["reason"] = item["reason"]
            prior["source_kind"] = item["source_kind"]
            prior["source_id"] = item["source_id"]
    candidates = list(deduplicated.values())
    candidates.sort(key=lambda item: (-item["_weight"], item["question"].casefold(), item["source_id"]))
    output = []
    for item in candidates[:50]:
        clean = {key: value for key, value in item.items() if key != "_weight"}
        persisted_id = None
        # Candidate generation is internal intelligence, not a caregiver request.
        # Persist only after a separate, explicit necessity assessment has marked it necessary.
        if persist and clean.get("necessity") == "necessary":
            matching_rows = db.query(OpenQuestion).filter(
                OpenQuestion.person_id == person_id,
                OpenQuestion.subject_type == clean["subject_type"],
                OpenQuestion.subject_id == clean["subject_id"],
                OpenQuestion.question == clean["question"],
            ).order_by(OpenQuestion.asked_at.desc()).limit(50).with_for_update().all()
            existing = next(
                (row for row in matching_rows if row.status in {"open", "unresolved"}),
                None,
            )
            existing_before = _open_question_state(existing) if existing is not None else None
            prior_terminal_question = None
            if existing is None:
                automatically_closed = next(
                    (
                        row for row in matching_rows
                        if row.status == "closed"
                        and str(row.closed_reason or "").startswith("automatically_")
                    ),
                    None,
                )
                if automatically_closed is not None and _question_has_new_grounding(automatically_closed, clean):
                    existing = automatically_closed
                    existing_before = _open_question_state(existing)
                    # Reopen only after a new persisted source/evidence arrives;
                    # a repeated assessment of unchanged information is not new.
                    existing.status = "open"
                    existing.closed_at = None
                    existing.closed_reason = None
                elif automatically_closed is not None:
                    # Retain the closed historical row; do not manufacture a
                    # second copy or reopen it from unchanged evidence.
                    persisted_id = automatically_closed.id
                    clean["persisted_question_id"] = persisted_id
                    output.append(clean)
                    continue
                else:
                    prior_terminal_question = next(
                        (
                            row for row in matching_rows
                            if row.status in {"answered", "declined", "closed"}
                        ),
                        None,
                    )
                    if prior_terminal_question is not None and not _question_has_new_grounding(prior_terminal_question, clean):
                        # A repeated reconciliation must not ask again after a
                        # caregiver answered, declined, or manually closed it.
                        persisted_id = prior_terminal_question.id
                        clean["persisted_question_id"] = persisted_id
                        output.append(clean)
                        continue
            source_context = {
                "source_kind": clean["source_kind"],
                "source_id": clean["source_id"],
                "source_ids": clean.get("source_ids") or [clean["source_id"]],
                "reason": clean["reason"],
                "necessity_assessment": {
                    "necessity": clean.get("necessity", "not_assessed"),
                    "necessity_rationale": clean.get("necessity_rationale"),
                    "decision_dependency": clean.get("decision_dependency"),
                    "evidence_ids": list(clean.get("evidence_ids") or []),
                },
            }
            if prior_terminal_question is not None:
                source_context["prior_terminal_question_id"] = prior_terminal_question.id
                source_context["reassessment_trigger"] = "new_persisted_source_or_evidence_after_prior_answer_or_decline"
            if existing is None:
                question = OpenQuestion(
                    person_id=person_id,
                    question=clean["question"],
                    subject_type=clean["subject_type"],
                    subject_id=clean["subject_id"],
                    status="open",
                    priority=Priority(clean["priority"]),
                    necessity=clean["necessity"],
                    necessity_rationale=clean["necessity_rationale"],
                    evidence_ids=clean["evidence_ids"],
                    intended_answerer_role=clean.get("intended_answerer_role"),
                    context=[source_context],
                )
                db.add(question)
                db.flush()
                _record_open_question_revision(
                    db, question, before_state=None, change_kind="created"
                )
                persisted_id = question.id
            else:
                # Refresh an existing unanswered question with newly linked source records.
                # Keep unresolved questions unresolved; never rewrite their status.
                if existing_before is None:
                    existing_before = _open_question_state(existing)
                existing.evidence_ids = list(dict.fromkeys(
                    list(existing.evidence_ids or []) + list(clean["evidence_ids"] or [])
                ))
                contexts = list(existing.context or [])
                context_key = (source_context["source_kind"], source_context["source_id"])
                matching = next(
                    (entry for entry in contexts
                     if (entry.get("source_kind"), entry.get("source_id")) == context_key),
                    None,
                )
                if matching is None:
                    contexts.append(source_context)
                else:
                    matching["source_ids"] = list(dict.fromkeys(
                        list(matching.get("source_ids") or [])
                        + list(source_context["source_ids"])
                    ))
                    matching["reason"] = source_context["reason"]
                    matching["necessity_assessment"] = source_context["necessity_assessment"]
                existing.context = contexts
                existing.priority = Priority(clean["priority"]) if _priority_weight(clean["priority"]) > _priority_weight(existing.priority) else existing.priority
                existing.necessity = clean["necessity"]
                existing.necessity_rationale = clean["necessity_rationale"]
                if clean.get("intended_answerer_role"):
                    existing.intended_answerer_role = clean["intended_answerer_role"]
                if existing_before != _open_question_state(existing):
                    _record_open_question_revision(
                        db, existing, before_state=existing_before, change_kind="updated"
                    )
                persisted_id = existing.id
        clean["persisted_question_id"] = persisted_id
        output.append(clean)
    if persist:
        if commit:
            db.commit()
        else:
            db.flush()
    return output



def reconcile_information_gaps_from_accepted_assertions(
    db: Session, person_id: str
) -> Dict[str, int]:
    """Resolve exact-field gaps covered by fresh, accepted, source-backed assertions.

    Current-state, document-reviewed and time-bound observations retain their
    distinct epistemic meaning. A time-bound observation can settle only an
    exact event-specific gap while its explicit review window remains current.
    No assertion is upgraded to independently verified.
    """
    active_statuses = (
        GapLifecycleStatus.OPEN,
        GapLifecycleStatus.REOPENED,
        GapLifecycleStatus.PARTIALLY_RESOLVED,
    )
    gaps = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_(active_statuses),
    ).with_for_update().all()
    if not gaps:
        return {"scanned": 0, "resolved": 0, "skipped_conflict": 0}

    assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.assertion_kind.in_(("current_state", "document_reviewed_observation", "time_bound_observation")),
        CareAssertion.assertion_status.in_(("accepted", "caregiver_reviewed")),
        CareAssertion.epistemic_state.in_(("reported", "observed", "verified")),
        CareAssertion.review_after_days.is_not(None),
        CareAssertion.review_after_days > 0,
    ).order_by(CareAssertion.recorded_at.desc()).all()

    now = datetime.now(timezone.utc)
    evidence_by_assertion = {}
    for assertion in assertions:
        # An accepted but expired assertion is historical context, not enough
        # to close a currently open information gap.
        freshness_status = get_freshness(assertion, now)["status"]
        if assertion.assertion_kind == "time_bound_observation":
            observed_at = _utc(assertion.observed_at)
            if (
                observed_at is None
                or not assertion.review_after_days
                or assertion.review_after_days <= 0
                or now > observed_at + timedelta(days=assertion.review_after_days)
            ):
                continue
        elif freshness_status != "within_review_window":
            continue
        ids = list(dict.fromkeys(str(value) for value in (assertion.evidence_ids or []) if value))
        if not ids:
            continue
        found = {
            str(row[0]) for row in db.query(Evidence.id).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(ids),
            ).all()
        }
        if found == set(ids):
            evidence_by_assertion[assertion.id] = ids

    assertion_groups = {}
    for assertion in assertions:
        if assertion.id not in evidence_by_assertion:
            continue
        key = (assertion.subject_type, assertion.subject_id, assertion.field)
        assertion_groups.setdefault(key, []).append(assertion)

    unresolved_conflicts = {
        (row.subject_type, row.subject_id, row.field)
        for row in db.query(Contradiction).filter(
            Contradiction.person_id == person_id,
            Contradiction.resolution_status.in_(("unresolved", "reopened", "qualified", "partially_resolved")),
        ).all()
    }

    resolved = skipped_conflict = 0
    for gap in gaps:
        key = (gap.subject_type, gap.subject_id, gap.field)
        candidates = assertion_groups.get(key, [])
        if not candidates:
            continue
        if key in unresolved_conflicts or len({_value_key(row.asserted_value) for row in candidates}) > 1:
            skipped_conflict += 1
            continue

        source_ids = [row.id for row in candidates]
        evidence_ids = list(dict.fromkeys(
            evidence_id
            for row in candidates
            for evidence_id in evidence_by_assertion[row.id]
        ))
        before_context = list(gap.context or [])
        resolution_entry = {
            "actor": "solenos_reconciliation",
            "change": "information_gap_resolved_from_accepted_current_state_assertion",
            "assertion_ids": source_ids,
            "evidence_ids": evidence_ids,
            "field_scope": gap.field,
            "epistemic_limit": "The accepted assertion remains source-attributed and reported; this transition records field coverage, not independent clinical verification.",
            "resolved_at": now.isoformat(),
        }
        if resolution_entry not in before_context:
            before_context.append(resolution_entry)
        gap.context = before_context
        gap.lifecycle_status = GapLifecycleStatus.RESOLVED
        gap.resolution_mechanism = ResolutionMechanism.NEW_EVIDENCE
        gap.resolution_date = now
        gap.resolution_notes = (
            "The missing field now has an accepted, source-backed current-state assertion. "
            "The assertion remains source-attributed and is not independently clinically verified."
        )
        # The transition is system-generated; do not attribute it to the
        # caregiver who last edited the gap.
        gap.updated_by_caregiver_id = None
        gap.evidence_ids = list(dict.fromkeys(
            list(gap.evidence_ids or []) + evidence_ids
        ))
        resolved += 1

    db.flush()
    return {"scanned": len(gaps), "resolved": resolved, "skipped_conflict": skipped_conflict}


def reassess_open_question_lifecycle(db: Session, person_id: str) -> Dict[str, int]:
    """Reassess source-linked questions without treating silence as an answer.

    Every automatic transition is written explicitly to the append-only
    CareStateRevision table in the same transaction; this function never commits.
    """
    candidates = next_best_questions(db, person_id, persist=False, commit=False)
    candidate_by_source = {}
    for candidate in candidates:
        for source_id in (candidate.get("source_ids") or [candidate.get("source_id")]):
            if source_id:
                candidate_by_source[(candidate.get("source_kind"), str(source_id))] = candidate

    questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id,
        OpenQuestion.status.in_(["open", "unresolved", "closed"]),
    ).order_by(OpenQuestion.asked_at.asc(), OpenQuestion.id.asc()).with_for_update().all()

    now = datetime.now(timezone.utc)
    counts = {"kept": 0, "suppressed": 0, "superseded": 0, "reopened": 0, "untouched": 0}
    managed_sources = {
        "information_gap", "contradiction", "stale_or_unreviewed_assertion",
        "daily_capture_extraction", "document_extraction", "registered_action",
        "appointment_workspace_follow_up",
    }

    for question in questions:
        contexts = [item for item in (question.context or []) if isinstance(item, dict)]
        linked = [
            (str(context.get("source_kind") or ""), str(source_id))
            for context in contexts
            for source_id in (context.get("source_ids") or [context.get("source_id")])
            if source_id and str(context.get("source_kind") or "") in managed_sources
        ]
        if not linked:
            counts["untouched"] += 1
            continue

        matching = [candidate_by_source[key] for key in linked if key in candidate_by_source]
        necessary = next((
            item for item in matching
            if item.get("necessity") == "necessary"
            and isinstance(item.get("necessity_rationale"), str)
            and item["necessity_rationale"].strip()
        ), None)

        if necessary:
            before_state = _open_question_state(question)
            if question.status == "closed" and str(question.closed_reason or "").startswith("automatically_"):
                if not _question_has_new_grounding(question, necessary):
                    counts["untouched"] += 1
                    continue
                question.status = "open"
                question.closed_at = None
                question.closed_reason = None
                counts["reopened"] += 1
            else:
                counts["kept"] += 1
            question.necessity = "necessary"
            question.necessity_rationale = necessary["necessity_rationale"].strip()
            question.evidence_ids = list(dict.fromkeys(
                list(question.evidence_ids or []) + list(necessary.get("evidence_ids") or [])
            )) or None
            if before_state != _open_question_state(question):
                _record_open_question_revision(
                    db, question, before_state=before_state, change_kind="updated"
                )
            continue

        # Daily Capture and document extraction questions participate in
        # the same lifecycle. They remain internal and unassessed until a
        # deterministic necessity assessment exists. A source-backed accepted
        # LCR assertion may supersede them; unanswered questions stay open.
        extraction_linked = any(
            source_kind in {"daily_capture_extraction", "document_extraction"}
            for source_kind, _ in linked
        )
        if extraction_linked:
            from app.services.uncertainty import _question_is_answered_by_current_lcr
            answered_by_lcr = _question_is_answered_by_current_lcr(
                db, person_id, question.question or ""
            )
            if not answered_by_lcr or question.status not in {"open", "unresolved"}:
                counts["untouched"] += 1
                continue
            before_state = _open_question_state(question)
            question.status = "closed"
            question.closed_at = now
            question.necessity = "not_assessed"
            question.closed_reason = (
                "automatically_superseded: an accepted, same-record LCR assertion "
                "answers the extracted clarification and no unresolved field conflict remains"
            )
            _record_open_question_revision(
                db, question, before_state=before_state, change_kind="updated"
            )
            counts["superseded"] += 1
            continue
        # Only questions created from these system-managed candidate sources are
        # auto-transitioned. User-authored clarification requests remain intact.
        if question.status not in {"open", "unresolved"}:
            counts["untouched"] += 1
            continue

        before_state = _open_question_state(question)
        has_active_candidate = bool(matching)
        question.status = "closed"
        question.closed_at = now
        if has_active_candidate:
            question.necessity = "not_assessed"
            question.necessity_rationale = (
                "Automatically suppressed: current evidence does not establish that caregiver input is necessary."
            )
            question.closed_reason = "automatically_suppressed: current evidence does not establish that caregiver input is necessary"
            counts["suppressed"] += 1
        else:
            question.closed_reason = "automatically_superseded: the linked source is no longer an active clarification candidate"
            counts["superseded"] += 1
        _record_open_question_revision(
            db, question, before_state=before_state, change_kind="updated"
        )

    db.flush()
    return counts


def create_action_outcome(db: Session, payload: CareActionOutcomeCreate) -> CareActionOutcome:
    if payload.evidence_ids:
        evidence_rows = db.query(Evidence.id).filter(
            Evidence.person_id == payload.person_id,
            Evidence.id.in_(payload.evidence_ids),
        ).all()
        if {row[0] for row in evidence_rows} != set(payload.evidence_ids):
            raise HTTPException(status_code=422, detail="Every outcome evidence ID must belong to this care record")
    if payload.action_type == "next_best_question":
        question = db.query(OpenQuestion).filter(
            OpenQuestion.id == payload.action_id,
            OpenQuestion.person_id == payload.person_id,
        ).first()
        if question is None:
            raise HTTPException(status_code=422, detail="The referenced next-best question does not belong to this care record")
    outcome = CareActionOutcome(**payload.model_dump())
    db.add(outcome)
    db.commit()
    db.refresh(outcome)
    return outcome
