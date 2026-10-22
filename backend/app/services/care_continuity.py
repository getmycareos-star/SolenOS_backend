from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.care_continuity import CareActionOutcome, CareAssertion, CareStateRevision
from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion
from app.schemas.care_continuity import CareAssertionCreate, CareActionOutcomeCreate
from app.core.uncertainty_enums import GapLifecycleStatus, Priority


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _value_key(value: str) -> str:
    return " ".join(value.casefold().split())


def create_assertion(db: Session, payload: CareAssertionCreate, *, commit: bool = True) -> CareAssertion:
    """Store every report; create/update unresolved conflict metadata without selecting a winner."""
    assertion = CareAssertion(**payload.model_dump())
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
            CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
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


def set_assertion_status(db: Session, assertion_id: str, person_id: str, status: str) -> CareAssertion:
    allowed = {"reported", "review_required", "accepted", "rejected", "superseded"}
    if status not in allowed:
        raise HTTPException(status_code=422, detail="Unsupported assertion status")
    assertion = db.query(CareAssertion).filter(
        CareAssertion.id == assertion_id, CareAssertion.person_id == person_id
    ).first()
    if assertion is None:
        raise HTTPException(status_code=404, detail="Assertion not found")
    assertion.assertion_status = status
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


def get_freshness(assertion: CareAssertion, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = _utc(now or datetime.now(timezone.utc))
    reference = _utc(assertion.observed_at) or _utc(assertion.recorded_at)
    age_days = max(0, (now - reference).days) if reference else None
    if assertion.assertion_kind == "time_bound_observation":
        status = "time_bound_observation"
        explanation = "This is a time-bound observation, not a claim about the current state."
    elif assertion.assertion_status in {"rejected", "superseded"}:
        status = "historical"
        explanation = "This assertion was explicitly rejected or superseded; it must not be presented as current."
    elif assertion.review_after_days is None or age_days is None or age_days > assertion.review_after_days:
        status = "needs_confirmation"
        explanation = "The review window is missing/elapsed or the observation time is unavailable; confirm before treating this report as current."
    elif assertion.assertion_status != "accepted":
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


def next_best_questions(db: Session, person_id: str, persist: bool = True) -> List[Dict[str, Any]]:
    """Rank questions from explicit contradictions, stale reports, and open gaps only."""
    candidates: List[Dict[str, Any]] = []
    contradictions = db.query(Contradiction).filter(
        Contradiction.person_id == person_id,
        Contradiction.resolution_status == "unresolved",
    ).all()
    for item in contradictions:
        candidates.append({
            "question": f"Two different reports conflict about {item.field}. What, if anything, confirms the current situation? Neither report has been selected as correct.",
            "subject_type": item.subject_type, "subject_id": item.subject_id,
            "priority": "high", "reason": "An unresolved contradiction prevents a reliable current-state conclusion.",
            "evidence_ids": item.evidence_ids or [], "source_kind": "contradiction", "source_id": item.id, "source_ids": item.source_assertion_ids or [], "_weight": 40,
        })

    assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
    ).all()
    now = datetime.now(timezone.utc)
    for item in assertions:
        freshness = get_freshness(item, now)
        if freshness["status"] == "needs_confirmation":
            candidates.append({
                "question": f"Can you confirm whether the reported information about {item.field} is still accurate? The record does not establish that it is current.",
                "subject_type": item.subject_type, "subject_id": item.subject_id,
                "priority": "medium", "reason": freshness["explanation"],
                "evidence_ids": item.evidence_ids or [], "source_kind": "stale_or_unreviewed_assertion",
                "source_id": item.id, "_weight": 30,
            })

    gaps = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_([GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED, GapLifecycleStatus.PARTIALLY_RESOLVED]),
    ).all()
    for item in gaps:
        priority = getattr(item.priority, "value", item.priority)
        candidates.append({
            "question": f"What is currently known about {item.field}?",
            "subject_type": item.subject_type, "subject_id": item.subject_id,
            "priority": priority if priority in {"critical", "high", "medium", "low", "informational"} else "informational",
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
        if persist:
            existing = db.query(OpenQuestion).filter(
                OpenQuestion.person_id == person_id,
                OpenQuestion.subject_type == clean["subject_type"],
                OpenQuestion.subject_id == clean["subject_id"],
                OpenQuestion.question == clean["question"],
                OpenQuestion.status == "open",
            ).first()
            if existing is None:
                question = OpenQuestion(
                    person_id=person_id,
                    question=clean["question"],
                    subject_type=clean["subject_type"],
                    subject_id=clean["subject_id"],
                    status="open",
                    priority=Priority(clean["priority"]),
                    evidence_ids=clean["evidence_ids"],
                    context=[{"source_kind": clean["source_kind"], "source_id": clean["source_id"], "reason": clean["reason"]}],
                )
                db.add(question)
                db.flush()
                persisted_id = question.id
            else:
                persisted_id = existing.id
        clean["persisted_question_id"] = persisted_id
        output.append(clean)
    if persist:
        db.commit()
    return output


def create_action_outcome(db: Session, payload: CareActionOutcomeCreate) -> CareActionOutcome:
    outcome = CareActionOutcome(**payload.model_dump())
    db.add(outcome)
    db.commit()
    db.refresh(outcome)
    return outcome
