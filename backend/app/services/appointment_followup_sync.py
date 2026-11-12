"""Sync explicit appointment follow-ups into the existing Open Loop record."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

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
        stable_key = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()[:24]
        source_id = f"{workspace_id}:{stable_key}"
        if source_id in current_source_ids:
            continue
        current_source_ids.add(source_id)
        owner = item.get("owner")
        owner_label = owner.strip()[:200] if isinstance(owner, str) and owner.strip() else None
        due_text = item.get("due") if isinstance(item.get("due"), str) else None
        due_at = _aware_due(due_text)
        source_status = item.get("status") if isinstance(item.get("status"), str) else "not_recorded"
        evidence = [{
            "record_type": "appointment_workspace", "record_id": workspace_id,
            "source_type": SOURCE_TYPE, "reported_owner": owner_label,
            "reported_due": due_text, "reported_status": source_status,
            "epistemic_status": "caregiver_reported",
            "interpretation_limit": "A reported follow-up is not proof that the action was completed or that the reported owner accepted responsibility.",
        }]
        fingerprint = hashlib.sha256(json.dumps({
            "title": title, "owner": owner_label, "due": due_text, "status": source_status,
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
                title=title, description=title, status="open", outcome_status="outcome_unknown",
                owner_role=None, owner_label=owner_label, source_type=SOURCE_TYPE, source_id=source_id,
                source_fingerprint=fingerprint, evidence=evidence, uncertainty="reported",
                deadline_at=due_at, deadline_source="explicit_user" if due_at else None,
                deadline_timezone=str(due_at.tzinfo) if due_at else None,
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
            loop.closure_result = "reopened_from_new_source_revision"
            loop.closure_actor = "solenos_reconciliation"
        loop.title = title
        loop.description = title
        loop.owner_label = owner_label
        loop.deadline_at = due_at
        loop.deadline_source = "explicit_user" if due_at else None
        loop.deadline_timezone = str(due_at.tzinfo) if due_at else None
        loop.source_fingerprint = fingerprint
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
