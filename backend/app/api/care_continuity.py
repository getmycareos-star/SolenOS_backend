"""Authenticated, person-scoped API for deep care-continuity records.

The API deliberately reuses existing evidence, events, changes, situations,
temporal and uncertainty tables. It does not create duplicate open-loop or
evidence stores. New records are always scoped from the bearer session, never
from a caller-supplied person_id.
"""
from datetime import datetime, date
from typing import Any
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import MetaData, Table, select, insert, desc
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.care_delta import _scoped_identity

router = APIRouter(prefix="/care-continuity", tags=["care-continuity"])

RESOURCES = {
    "facts": "care_facts",
    "fact-evidence": "care_fact_evidence",
    "relationships": "care_record_relationships",
    "delta-items": "care_delta_items",
    "integrity-findings": "care_integrity_findings",
    "integrity-resolutions": "care_integrity_resolutions",
    "decisions": "care_decisions",
    "decision-outcomes": "decision_outcomes",
    "reconciliations": "care_reconciliation_records",
    "follow-up-links": "care_follow_up_links",
    "review-decisions": "care_review_decisions",
    "provenance": "care_provenance_records",
    "timeline": "care_timeline_entries",
    "change-assessments": "care_change_assessments",
    "events": "care_events",
    "evidence": "evidence",
    "changes": "changes",
    "situations": "situations",
    "state-revisions": "care_state_revisions",
    "temporal-facts": "temporal_facts",
    "temporal-relations": "temporal_relations",
    "temporal-relationships": "temporal_relationships",
    "temporal-reference-frames": "temporal_reference_frames",
    "temporal-contradictions": "temporal_contradictions",
    "uncertainty-assessments": "uncertainty_assessments",
    "causal-hypotheses": "causal_hypotheses",
    "causal-evidence": "causal_evidence",
    "contradictions": "contradictions",
    "significance-assessments": "significance_assessments",
    "delta-runs": "care_delta_runs",
    "open-questions": "open_questions",
    "information-gaps": "information_gaps",
    "assertions": "care_assertions",
    "caregiver-contexts": "caregiver_contexts",
    "caregiver-perspectives": "caregiver_perspectives",
    "caregiver-observations": "caregiver_observations",
    "living-records": "living_care_records",
    "record-provenance": "record_provenance",
    "record-provenance-links": "record_provenance_links",
    "record-change-sets": "care_record_change_sets",
    "record-change-items": "care_record_change_items",
    "corrections": "care_corrections",
    "amendments": "record_amendments",
    "amendment-items": "record_amendment_items",
    "record-audit": "care_record_audit_log",
    "uncertainties": "care_uncertainties",
    "missing-information": "care_missing_information",
    "conflicts": "care_conflicts",
    "action-outcomes": "care_action_outcomes",
}

WRITABLE = {
    "facts": {"subject_type", "subject_id", "fact_type", "fact_value", "epistemic_status",
              "effective_at", "superseded_by_fact_id", "review_notes"},
    "fact-evidence": {"fact_id", "evidence_id", "evidence_role", "source_span"},
    "relationships": {"source_type", "source_id", "relationship_type", "target_type", "target_id", "provenance"},
    "delta-items": {"delta_run_id", "item_type", "target_type", "target_id", "before_state",
                    "after_state", "evidence_ids", "reconciliation_status", "review_notes"},
    "integrity-findings": {"finding_type", "severity", "status", "target_type", "target_id",
                           "description", "evidence_ids", "detected_by"},
    "integrity-resolutions": {"finding_id", "resolution_status", "resolution_notes", "evidence_ids"},
    "decisions": {"title", "decision_text", "decision_status", "decided_at", "context", "evidence_ids"},
    "decision-outcomes": {"decision_id", "outcome_status", "observed_at", "description",
                          "evidence_ids", "is_intended_outcome"},
    "reconciliations": {"target_type", "target_id", "reconciliation_status", "competing_claim_ids",
                        "evidence_ids", "decision_notes"},
    "follow-up-links": {"source_type", "source_id", "target_type", "target_id", "follow_up_kind", "status", "due_at", "completed_at", "notes", "evidence_ids"},
    "review-decisions": {"target_type", "target_id", "review_decision", "reason", "evidence_ids"},
    "provenance": {"target_type", "target_id", "source_type", "source_id", "evidence_id", "source_label", "source_span", "metadata"},
    "timeline": {"source_type", "source_id", "entry_type", "title", "summary", "occurred_at", "evidence_ids", "epistemic_status", "is_projection"},
    "change-assessments": {"change_id", "assessment_status", "significance", "evidence_status", "assessment_notes", "evidence_ids"},
    # Caregiver-reported context remains attributed perspective, not an LCR fact.
    # Identity fields are injected from the server-issued session below.
    "caregiver-contexts": {"context_type", "context_payload", "epistemic_status", "source_type", "source_id", "recorded_at", "effective_from", "effective_to", "notes"},
    "caregiver-perspectives": {"topic", "perspective_text", "epistemic_status", "evidence_ids", "source_type", "source_id", "recorded_at", "effective_from", "effective_to", "supersedes_id"},
    "caregiver-observations": {"observation_type", "description", "observed_value", "observed_at", "recorded_at", "epistemic_status", "evidence_ids", "source_type", "source_id"},
    "living-records": {"record_status", "schema_version"},
    "record-provenance": {"care_record_id", "source_type", "source_id", "author_label", "method", "recorded_at", "occurred_at", "effective_from", "effective_to", "reason", "metadata"},
    "record-provenance-links": {"provenance_id", "target_type", "target_id", "relationship"},
    "record-change-sets": {"care_record_id", "operation", "status", "reason", "provenance_id"},
    "record-change-items": {"change_set_id", "target_type", "target_id", "operation", "before_value", "after_value", "provenance_id"},
    "corrections": {"care_record_id", "target_type", "target_id", "original_value", "corrected_value", "correction_reason", "status", "provenance_id", "effective_at"},
    "amendments": {"care_record_id", "amendment_type", "reason", "status", "provenance_id"},
    "amendment-items": {"amendment_id", "target_type", "target_id", "field_name", "before_value", "after_value"},
    "record-audit": {"care_record_id", "actor_type", "actor_id", "action", "target_type", "target_id", "outcome", "reason", "metadata", "occurred_at", "request_id"},
    "uncertainties": {"care_record_id", "target_type", "target_id", "question", "uncertainty_type", "status", "source_ids", "resolved_at"},
    "missing-information": {"care_record_id", "topic", "description", "priority", "status", "resolved_at"},
    "conflicts": {"care_record_id", "conflict_type", "status", "claim_refs", "description", "resolution", "resolved_at"},
    "action-outcomes": {"action_id", "action_type", "outcome_status", "notes", "correction_text", "evidence_ids"},
}

def _table(db: Session, name: str) -> Table:
    try:
        return Table(name, MetaData(), autoload_with=db.get_bind())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Care-continuity schema is not ready: {name}") from exc

def _jsonable(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value

def _validate_links(db: Session, person_id: str, resource: str, payload: dict):
    # Validate cross-table links against the same care recipient to prevent
    # accidentally attaching another person's source material.
    checks = {
        "fact-evidence": [("care_facts", "fact_id"), ("evidence", "evidence_id")],
        "delta-items": [("care_delta_runs", "delta_run_id")],
        "integrity-resolutions": [("care_integrity_findings", "finding_id")],
        "decision-outcomes": [("care_decisions", "decision_id")],
    }
    if resource == "provenance" and payload.get("evidence_id"):
        checks.setdefault(resource, []).append(("evidence", "evidence_id"))
    if resource == "change-assessments":
        checks.setdefault(resource, []).append(("changes", "change_id"))
    for table_name, field in checks.get(resource, []):
        value = payload.get(field)
        if not value:
            raise HTTPException(status_code=422, detail=f"{field} is required")
        table = _table(db, table_name)
        row = db.execute(select(table.c.id).where(table.c.id == value, table.c.person_id == person_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field} was not found in this care record")

    # Every explicit evidence reference must resolve within this care record.
    evidence_ids = payload.get("evidence_ids") or []
    if not isinstance(evidence_ids, list):
        raise HTTPException(status_code=422, detail="evidence_ids must be a JSON array")
    if evidence_ids:
        evidence = _table(db, "evidence")
        found = db.execute(
            select(evidence.c.id).where(
                evidence.c.person_id == person_id, evidence.c.id.in_(evidence_ids)
            )
        ).scalars().all()
        if set(map(str, found)) != set(map(str, evidence_ids)):
            raise HTTPException(status_code=404, detail="One or more evidence references are outside this care record")

    typed_tables = {
        "care_fact": "care_facts", "fact": "care_facts",
        "care_decision": "care_decisions", "decision": "care_decisions",
        "care_event": "care_events", "event": "care_events",
        "evidence": "evidence", "change": "changes", "situation": "situations",
        "open_question": "open_questions", "information_gap": "information_gaps",
        "delta_item": "care_delta_items", "integrity_finding": "care_integrity_findings",
    }
    links = []
    if resource in ("follow-up-links", "relationships"):
        links.extend([
            (payload.get("source_type"), payload.get("source_id"), "source_id"),
            (payload.get("target_type"), payload.get("target_id"), "target_id"),
        ])
    elif resource in ("review-decisions", "provenance"):
        links.append((payload.get("target_type"), payload.get("target_id"), "target_id"))
        if resource == "provenance" and payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource == "timeline":
        links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations"):
        if payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    for type_name, target_id, field_name in links:
        table_name = typed_tables.get(str(type_name or "").lower())
        if table_name is None or not target_id:
            continue
        linked = _table(db, table_name)
        row = db.execute(
            select(linked.c.id).where(linked.c.id == target_id, linked.c.person_id == person_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field_name} was not found in this care record")

@router.get("/summary")
def continuity_summary(request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    result = {"person_id": person_id, "sources": "existing evidence/events/changes/situations are reused",
              "resources": {}}
    for resource, table_name in RESOURCES.items():
        table = _table(db, table_name)
        result["resources"][resource] = db.execute(
            select(table.c.id).where(table.c.person_id == person_id)
        ).fetchall().__len__()
    # Open questions and information gaps are the existing equivalents of open loops.
    for key, table_name in (("open_questions", "open_questions"), ("information_gaps", "information_gaps"),
                            ("changes", "changes"), ("situations", "situations"),
                            ("events", "care_events"), ("evidence", "evidence"),
                            ("temporal_contradictions", "temporal_contradictions"),
                            ("uncertainty_assessments", "uncertainty_assessments"),
                            ("significance_assessments", "significance_assessments"),
                            ("delta_runs", "care_delta_runs")):
        table = _table(db, table_name)
        result["resources"][key] = db.execute(
            select(table.c.id).where(table.c.person_id == person_id)
        ).fetchall().__len__()
    return result

@router.get("/{resource}")
def list_resource(resource: str, request: Request, limit: int = 50, db: Session = Depends(get_db)):
    if resource not in RESOURCES:
        raise HTTPException(status_code=404, detail="Unknown care-continuity resource")
    person_id, _ = _scoped_identity(request, db)
    table = _table(db, RESOURCES[resource])
    query = select(table).where(table.c.person_id == person_id)
    if "created_at" in table.c:
        query = query.order_by(desc(table.c.created_at))
    rows = db.execute(query.limit(max(1, min(limit, 100)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]

@router.post("/{resource}", status_code=201)
def create_resource(resource: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    if resource not in WRITABLE:
        raise HTTPException(status_code=404, detail="Unknown or read-only care-continuity resource")
    person_id, caregiver_id = _scoped_identity(request, db)
    allowed = WRITABLE[resource]
    data = {key: value for key, value in payload.items() if key in allowed}
    if not data:
        raise HTTPException(status_code=422, detail="No supported care-continuity fields supplied")
    required = {
        "facts": ("subject_type", "fact_type", "fact_value"),
        "fact-evidence": ("fact_id", "evidence_id"),
        "relationships": ("source_type", "source_id", "relationship_type", "target_type", "target_id"),
        "delta-items": ("delta_run_id", "item_type"),
        "integrity-findings": ("finding_type", "description"),
        "integrity-resolutions": ("finding_id", "resolution_status"),
        "decisions": ("title", "decision_text"),
        "decision-outcomes": ("decision_id", "outcome_status"),
        "reconciliations": ("target_type", "target_id", "reconciliation_status"),
        "follow-up-links": ("source_type", "source_id", "target_type", "target_id"),
        "review-decisions": ("target_type", "target_id", "review_decision"),
        "provenance": ("target_type", "target_id", "source_type"),
        "timeline": ("source_type", "source_id", "entry_type", "title"),
        "change-assessments": ("change_id",),
        "caregiver-contexts": ("context_type", "context_payload"),
        "caregiver-perspectives": ("topic", "perspective_text"),
        "caregiver-observations": ("observation_type", "description"),
        "living-records": (),
        "record-provenance": ("source_type",),
        "record-provenance-links": ("provenance_id", "target_type", "target_id"),
        "record-change-sets": ("operation",),
        "record-change-items": ("change_set_id", "target_type", "target_id", "operation"),
        "corrections": ("target_type", "target_id", "corrected_value", "correction_reason"),
        "amendments": ("amendment_type", "reason"),
        "amendment-items": ("amendment_id", "target_type", "target_id"),
        "record-audit": ("actor_type", "action"),
        "uncertainties": ("question", "uncertainty_type"),
        "missing-information": ("topic", "description"),
        "conflicts": ("conflict_type", "description"),
        "action-outcomes": ("action_id", "action_type", "outcome_status"),
    }[resource]
    missing = [key for key in required if data.get(key) is None or data.get(key) == ""]
    if missing:
        raise HTTPException(status_code=422, detail={"missing_fields": missing})
    _validate_links(db, person_id, resource, data)
    table = _table(db, RESOURCES[resource])
    data["person_id"] = person_id
    # Migration-created continuity tables use UUID-shaped string primary keys
    # without database-side generators; issue IDs server-side for every insert.
    if "id" in table.c and "id" not in data:
        data["id"] = str(uuid.uuid4())
    if "created_by_caregiver_id" in table.c:
        data["created_by_caregiver_id"] = caregiver_id
    # Never trust client-supplied caregiver attribution.
    if "caregiver_id" in table.c:
        data["caregiver_id"] = caregiver_id
    if resource == "record-provenance" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "record-change-sets" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "corrections" and "requested_by_caregiver_id" in table.c:
        data["requested_by_caregiver_id"] = caregiver_id
    if resource == "amendments" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "record-audit":
        data["actor_type"] = "caregiver"
        data["actor_id"] = caregiver_id
    if "recorded_by_caregiver_id" in table.c:
        data["recorded_by_caregiver_id"] = caregiver_id
    if "reviewed_by_caregiver_id" in table.c and resource == "review-decisions":
        data["reviewed_by_caregiver_id"] = caregiver_id
    if "assessed_by_caregiver_id" in table.c and resource == "change-assessments":
        data["assessed_by_caregiver_id"] = caregiver_id
    if "reviewed_by_caregiver_id" in table.c and resource == "delta-items" and data.get("reconciliation_status") != "pending_review":
        data["reviewed_by_caregiver_id"] = caregiver_id
        data["reviewed_at"] = datetime.now().astimezone()
    if "resolved_by_caregiver_id" in table.c and resource == "integrity-resolutions":
        data["resolved_by_caregiver_id"] = caregiver_id
    if "reviewed_by_caregiver_id" in table.c and resource == "reconciliations":
        data["reviewed_by_caregiver_id"] = caregiver_id
        data["reviewed_at"] = datetime.now().astimezone()
    if "evidence_ids" in data and isinstance(data["evidence_ids"], str):
        raise HTTPException(status_code=422, detail="evidence_ids must be a JSON array")
    try:
        result = db.execute(insert(table).values(**data).returning(table))
        row = result.mappings().one()
        db.commit()
        return _jsonable(dict(row))
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail="Care-continuity record could not be saved; check linked IDs and field types") from exc
