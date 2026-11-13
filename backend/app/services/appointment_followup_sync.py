"""Sync explicit appointment follow-ups into the existing Open Loop record."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from sqlalchemy.orm import Session

from app.models.care import Evidence

from app.models.open_loop import OpenLoopRecord, OpenLoopEvent, OpenLoopOccurrence

SOURCE_TYPE = "appointment_workspace_follow_up"


def _aware_due(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _timezone_label(value, explicit_timezone=None):
    """Persist an IANA zone when supplied; otherwise retain the source UTC offset."""
    if explicit_timezone is not None:
        if not isinstance(explicit_timezone, str) or not explicit_timezone.strip():
            raise HTTPException(status_code=422, detail="Follow-up timezone must be a valid IANA timezone")
        try:
            ZoneInfo(explicit_timezone.strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=422, detail="Follow-up timezone must be a valid IANA timezone")
        return explicit_timezone.strip()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return str(parsed.tzinfo)


def _snapshot(loop):
    return {
        "id": loop.id, "person_id": loop.person_id, "caregiver_id": loop.caregiver_id,
        "loop_type": loop.loop_type, "title": loop.title, "description": loop.description,
        "status": loop.status, "owner_role": loop.owner_role, "owner_label": loop.owner_label,
        "source_type": loop.source_type, "source_id": loop.source_id,
        "deadline_at": loop.deadline_at.isoformat() if loop.deadline_at else None,
        "deadline_source": loop.deadline_source, "evidence": loop.evidence or [],
        "version": loop.version,
    }


def sync_appointment_followups(
    db: Session, *, person_id: str, caregiver_id: str, workspace_id: str, payload: dict
) -> dict[str, int]:
    """Maintain source-linked Open Loops for explicit follow-ups in a saved workspace.

    The caller owns the transaction. Removal cancels, but never completes, a loop.
    A later reappearance reopens only a loop cancelled by this synchronizer.
    """
    raw = payload.get("followUps", payload.get("follow_ups", []))
    if not isinstance(raw, list):
        raw = []
    current_source_ids: set[str] = set()
    created = updated = cancelled = 0
    now = datetime.now(timezone.utc)

    for item in raw:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        if not isinstance(action, str) or not action.strip():
            continue
        title = " ".join(action.split())[:240]
        raw_follow_up_id = item.get("id")
        if raw_follow_up_id is not None:
            if (
                not isinstance(raw_follow_up_id, str)
                or not raw_follow_up_id.strip()
                or len(raw_follow_up_id.strip()) > 200
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Appointment follow-up IDs must be non-empty strings of at most 200 characters",
                )
            # The client-generated item ID is stable across workspace revisions.
            # Hash it before embedding it in source_id; changing the action text
            # must update the same commitment, not cancel it and create another.
            identity_material = "follow-up-id:" + raw_follow_up_id.strip()
        else:
            # Preserve compatibility for older clients that did not send item IDs.
            identity_material = "legacy-title:" + title.casefold()
        stable_key = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:24]
        source_id = f"{workspace_id}:{stable_key}"
        if source_id in current_source_ids:
            continue
        current_source_ids.add(source_id)
        owner = item.get("owner_label", item.get("owner"))
        owner_label = owner.strip()[:200] if isinstance(owner, str) and owner.strip() else None
        raw_owner_role = item.get("owner_role")
        # SolenOS owns tracking and reassessment; an absent action owner stays
        # explicitly unassigned rather than being silently pushed onto the
        # caregiver who entered the appointment note.
        owner_role = (
            raw_owner_role.strip()[:100]
            if isinstance(raw_owner_role, str) and raw_owner_role.strip()
            else "unassigned"
        )
        due_text = item.get("due") if isinstance(item.get("due"), str) else None
        due_at = _aware_due(due_text)
        source_status = item.get("status") if isinstance(item.get("status"), str) else "not_recorded"
        reported_complete = source_status.strip().casefold() in {"completed", "complete", "done"}
        needs_clarification = source_status.strip().casefold() in {"needs_clarification", "blocked"}
        clarification_question = item.get("clarification_question", item.get("question"))
        necessity_rationale = item.get("necessity_rationale")
        decision_dependency = item.get("decision_dependency")
        raw_evidence_ids = item.get("evidence_ids") or []
        if isinstance(raw_evidence_ids, str):
            raw_evidence_ids = [raw_evidence_ids]
        if not isinstance(raw_evidence_ids, list):
            raw_evidence_ids = []
        clarification_evidence_ids = list(dict.fromkeys(
            value.strip() for value in raw_evidence_ids
            if isinstance(value, str) and value.strip()
        ))
        if needs_clarification:
            if not (
                isinstance(clarification_question, str)
                and clarification_question.strip().endswith("?")
                and isinstance(necessity_rationale, str) and necessity_rationale.strip()
                and isinstance(decision_dependency, str) and decision_dependency.strip()
                and clarification_evidence_ids
            ):
                raise HTTPException(
                    status_code=422,
                    detail="A blocked appointment follow-up needs a specific question, reason, decision dependency, and same-record evidence IDs",
                )
        if clarification_evidence_ids:
            found_evidence_ids = {
                str(row[0]) for row in db.query(Evidence.id).filter(
                    Evidence.person_id == person_id,
                    Evidence.id.in_(clarification_evidence_ids),
                ).all()
            }
            if found_evidence_ids != set(clarification_evidence_ids):
                raise HTTPException(
                    status_code=422,
                    detail="Appointment follow-up evidence must belong to this care record",
                )
        evidence = [{
            "record_type": "appointment_workspace", "record_id": workspace_id,
            "source_type": SOURCE_TYPE, "reported_owner": owner_label,
            "reported_owner_role": owner_role,
            "reported_due": due_text, "reported_status": source_status,
            "evidence_ids": clarification_evidence_ids,
            "clarification_assessment": ({
                "question": clarification_question.strip(),
                "necessity_rationale": necessity_rationale.strip(),
                "decision_dependency": decision_dependency.strip(),
                "intended_answerer_role": (
                    item.get("intended_answerer_role").strip()
                    if isinstance(item.get("intended_answerer_role"), str) and item.get("intended_answerer_role").strip()
                    else "not_known"
                ),
            } if needs_clarification else None),
            "epistemic_status": "caregiver_reported",
            "interpretation_limit": "A reported follow-up is not proof that the action was completed or that the reported owner accepted responsibility.",
        }]
        fingerprint = hashlib.sha256(json.dumps({
            "title": title, "owner": owner_label, "owner_role": owner_role, "due": due_text, "status": source_status,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        loop = db.query(OpenLoopRecord).filter(
            OpenLoopRecord.person_id == person_id,
            OpenLoopRecord.caregiver_id == caregiver_id,
            OpenLoopRecord.loop_type == "follow_up",
            OpenLoopRecord.source_type == SOURCE_TYPE,
            OpenLoopRecord.source_id == source_id,
        ).with_for_update().first()

        if loop is None:
            loop = OpenLoopRecord(
                person_id=person_id, caregiver_id=caregiver_id, loop_type="follow_up",
                title=title, description=title, status="blocked" if needs_clarification else "open",
                outcome_status="outcome_reported" if reported_complete else "outcome_unknown",
                outcome_evidence=evidence if reported_complete else [],
                verification_rule=(
                    "Completion was reported in the appointment workspace; keep it reported until supported by completion evidence."
                    if reported_complete else None
                ),
                owner_role=owner_role, owner_label=owner_label, source_type=SOURCE_TYPE, source_id=source_id,
                source_fingerprint=fingerprint, evidence=evidence, uncertainty="reported",
                deadline_at=due_at, deadline_source="explicit_user" if due_at else None,
                deadline_timezone=_timezone_label(due_text, item.get("timezone")),
                priority="normal",
                priority_reasons=["Explicit follow-up recorded in an appointment workspace"],
                version=1, created_at=now, updated_at=now,
            )
            db.add(loop)
            db.flush()
            db.add(OpenLoopOccurrence(
                loop_id=loop.id, person_id=person_id, caregiver_id=caregiver_id,
                source_type=SOURCE_TYPE, source_id=source_id,
                source_fingerprint=fingerprint, evidence=evidence,
            ))
            db.add(OpenLoopEvent(
                loop_id=loop.id, person_id=person_id, caregiver_id=caregiver_id,
                event_type="created_from_appointment_follow_up", before_state=None,
                after_state=_snapshot(loop), evidence=evidence, actor="solenos_reconciliation",
            ))
            created += 1
            continue

        before = _snapshot(loop)
        if loop.status in {"closed", "cancelled"}:
            if not str(loop.cancellation_reason or "").startswith("source_removed_from_appointment_workspace"):
                continue
            loop.status = "open"
            loop.closed_at = None
            loop.cancellation_reason = None
            loop.closure_result = None
            loop.closure_actor = None
            loop.closure_evidence = []
        loop.title = title
        loop.description = title
        if needs_clarification:
            loop.status = "blocked"
        elif loop.status == "blocked":
            loop.status = "open"
        loop.owner_role = owner_role
        loop.owner_label = owner_label
        loop.deadline_at = due_at
        loop.deadline_source = "explicit_user" if due_at else None
        loop.deadline_timezone = _timezone_label(due_text, item.get("timezone"))
        loop.source_fingerprint = fingerprint
        # A later workspace edit is not authority to erase an independently
        # verified outcome. Workspace completion remains a report only.
        if loop.outcome_status != "outcome_verified":
            loop.outcome_status = "outcome_reported" if reported_complete else "outcome_unknown"
            loop.outcome_evidence = (
                list(loop.outcome_evidence or []) + [item for item in evidence if item not in (loop.outcome_evidence or [])]
                if reported_complete else []
            )
            loop.verification_rule = (
                "Completion was reported in the appointment workspace; keep it reported until supported by completion evidence."
                if reported_complete else None
            )
        existing_evidence = list(loop.evidence or [])
        for evidence_item in evidence:
            if evidence_item not in existing_evidence:
                existing_evidence.append(evidence_item)
        loop.evidence = existing_evidence
        loop.updated_at = now
        loop.version = (loop.version or 0) + 1
        db.add(OpenLoopEvent(
            loop_id=loop.id, person_id=person_id, caregiver_id=caregiver_id,
            event_type="source_reconciled", before_state=before,
            after_state=_snapshot(loop), evidence=evidence, actor="solenos_reconciliation",
        ))
        updated += 1

    prior_loops = db.query(OpenLoopRecord).filter(
        OpenLoopRecord.person_id == person_id, OpenLoopRecord.caregiver_id == caregiver_id,
        OpenLoopRecord.loop_type == "follow_up", OpenLoopRecord.source_type == SOURCE_TYPE,
        OpenLoopRecord.source_id.like(f"{workspace_id}:%"),
        OpenLoopRecord.status.notin_(["closed", "cancelled"]),
    ).with_for_update().all()
    for loop in prior_loops:
        if loop.source_id in current_source_ids:
            continue
        before = _snapshot(loop)
        loop.status = "cancelled"
        loop.cancellation_reason = "source_removed_from_appointment_workspace: removed from a later saved revision; not marked completed"
        loop.closure_result = "cancelled_not_resolved"
        loop.closure_actor = "solenos_reconciliation"
        loop.closed_at = now
        loop.updated_at = now
        loop.version = (loop.version or 0) + 1
        db.add(OpenLoopEvent(
            loop_id=loop.id, person_id=person_id, caregiver_id=caregiver_id,
            event_type="source_follow_up_removed", before_state=before,
            after_state=_snapshot(loop),
            evidence=[{"record_type": "appointment_workspace", "record_id": workspace_id,
                       "reason": "Absent from latest saved workspace revision",
                       "outcome": "cancelled_not_resolved"}],
            actor="solenos_reconciliation",
        ))
        cancelled += 1
    db.flush()
    return {"created": created, "updated": updated, "cancelled_not_completed": cancelled}
