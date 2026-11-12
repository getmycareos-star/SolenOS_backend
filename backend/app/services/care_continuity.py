from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List, Optional
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.care_continuity import CareActionOutcome, CareAssertion, CareStateRevision
from app.models.care import CareEvent, Evidence
from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion
from app.schemas.care_continuity import CareAssertionCreate, CareActionOutcomeCreate
from app.core.uncertainty_enums import GapLifecycleStatus, Priority


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _value_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _open_question_state(question: OpenQuestion) -> Dict[str, Any]:
    return {
        "id": question.id,
        "question": question.question,
        "subject_type": question.subject_type,
        "subject_id": question.subject_id,
        "status": question.status,
        "necessity": question.necessity,
        "necessity_rationale": question.necessity_rationale,
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
    """Append an immutable care-state revision for every automated question transition."""
    latest = db.query(CareStateRevision.revision_number).filter(
        CareStateRevision.person_id == question.person_id,
        CareStateRevision.target_type == "open_questions",
        CareStateRevision.target_id == question.id,
    ).order_by(CareStateRevision.revision_number.desc()).first()
    revision_number = (latest[0] if latest else 0) + 1
    after_state = _open_question_state(question)
    after_state["lifecycle_actor"] = actor
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
            CareAssertion.assertion_kind == "current_state",
            CareAssertion.id != assertion.id,
            CareAssertion.assertion_status.in_(["reported", "review_required", "accepted", "caregiver_reviewed"]),
        )
        .order_by(CareAssertion.recorded_at.asc())
        .all()
    )
    if assertion.assertion_kind != "current_state":
        peers = []
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
                Contradiction.resolution_status == "unresolved",
            )
            .first()
        )
        conflict_text = "Competing source assertions require review; no assertion was selected as true. Reported values: " + " | ".join(descriptions)
        source_assertion_ids = list(dict.fromkeys([peer.id for peer in conflicts] + [assertion.id]))
        if contradiction:
            contradiction.description = conflict_text
            contradiction.evidence_ids = evidence_ids
            contradiction.source_assertion_ids = list(dict.fromkeys((contradiction.source_assertion_ids or []) + source_assertion_ids))
        else:
            contradiction = Contradiction(
                person_id=payload.person_id,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                field=payload.field,
                description=conflict_text,
                evidence_ids=evidence_ids,
                source_assertion_ids=source_assertion_ids,
                resolution_status="unresolved",
            )
            db.add(contradiction)
    if commit:
        db.commit()
    else:
        db.flush()
    db.refresh(assertion)
    return assertion


def set_assertion_status(db: Session, assertion_id: str, person_id: str, status: str, updated_by_caregiver_id: Optional[str] = None, review_after_days: Optional[int] = None) -> CareAssertion:
    allowed = {"reported", "review_required", "accepted", "rejected", "superseded"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported assertion status")
    assertion = db.query(CareAssertion).filter(
        CareAssertion.id == assertion_id, CareAssertion.person_id == person_id
    ).first()
    if assertion is None:
        raise HTTPException(status_code=404, detail="Assertion not found")
    if status == "accepted" and assertion.assertion_kind == "current_state" and review_after_days is None and assertion.review_after_days is None:
        raise HTTPException(status_code=422, detail="Set a review window before marking a current-state assertion as accepted")
    if review_after_days is not None:
        assertion.review_after_days = review_after_days
    assertion.assertion_status = status
    assertion.updated_by_caregiver_id = updated_by_caregiver_id
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
        Contradiction.resolution_status == "unresolved",
    ).all()
    for item in contradictions:
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
        Contradiction.resolution_status.in_(["unresolved", "qualified"]),
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
        freshness = get_freshness(item, now)
        if freshness["status"] == "needs_confirmation":
            candidates.append({
                "question": f"Can you confirm whether the reported information about {item.field} is still accurate? The record does not establish that it is current.",
                "subject_type": item.subject_type, "subject_id": item.subject_id,
                "priority": "medium", "necessity": "not_assessed", "necessity_rationale": "Age or an unreviewed status alone does not establish that caregiver input is necessary; monitor internally until a current care decision depends on it.", "reason": freshness["explanation"],
                "evidence_ids": item.evidence_ids or [], "source_kind": "stale_or_unreviewed_assertion",
                "source_id": item.id, "_weight": 30,
            })

    gaps = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_([GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED, GapLifecycleStatus.PARTIALLY_RESOLVED]),
    ).all()
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
            ).order_by(OpenQuestion.asked_at.desc()).limit(50).all()
            existing = next(
                (row for row in matching_rows if row.status in {"open", "unresolved"}),
                None,
            )
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
            source_context = {
                "source_kind": clean["source_kind"],
                "source_id": clean["source_id"],
                "source_ids": clean.get("source_ids") or [clean["source_id"]],
                "reason": clean["reason"],
                "necessity_assessment": {
                    "necessity": clean.get("necessity", "not_assessed"),
                    "necessity_rationale": clean.get("necessity_rationale"),
                    "decision_dependency": clean.get("decision_dependency"),
                },
            }
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
                    context=[source_context],
                )
                db.add(question)
                db.flush()
                persisted_id = question.id
            else:
                # Refresh an existing unanswered question with newly linked source records.
                # Keep unresolved questions unresolved; never rewrite their status.
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
                persisted_id = existing.id
        clean["persisted_question_id"] = persisted_id
        output.append(clean)
    if persist:
        if commit:
            db.commit()
        else:
            db.flush()
    return output



def reassess_open_question_lifecycle(db: Session, person_id: str) -> Dict[str, int]:
    """Reassess source-linked questions without treating silence as an answer.

    OpenQuestion changes are captured by the existing append-only CareStateRevision
    listener. Automatic transitions therefore remain auditable in the same
    transaction as source reconciliation; this function never commits.
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
    ).order_by(OpenQuestion.asked_at.asc(), OpenQuestion.id.asc()).all()

    now = datetime.now(timezone.utc)
    counts = {"kept": 0, "suppressed": 0, "superseded": 0, "reopened": 0, "untouched": 0}
    managed_sources = {"information_gap", "contradiction", "stale_or_unreviewed_assertion"}

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
            question.necessity = "necessary"
            question.necessity_rationale = necessary["necessity_rationale"].strip()
            question.evidence_ids = list(dict.fromkeys(
                list(question.evidence_ids or []) + list(necessary.get("evidence_ids") or [])
            )) or None
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
            continue

        # Only questions created from these system-managed candidate sources are
        # auto-transitioned. User-authored clarification requests remain intact.
        if question.status not in {"open", "unresolved"}:
            counts["untouched"] += 1
            continue

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
