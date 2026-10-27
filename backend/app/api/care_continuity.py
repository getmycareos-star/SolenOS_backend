"""Authenticated, person-scoped API for deep care-continuity records.

The API deliberately reuses existing evidence, events, changes, situations,
temporal and uncertainty tables. It does not create duplicate open-loop or
evidence stores. New records are always scoped from the bearer session, never
from a caller-supplied person_id.
"""
from datetime import datetime, date, timezone
from typing import Any
import json
import uuid
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import MetaData, Table, select, insert, update, desc, inspect
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.care_delta import _scoped_identity
from app.core.caregiver_enums import ClaimType, ObservationType, InformationVisibility

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
    "record-identifiers": "care_record_identifiers",
    "record-sections": "care_record_sections",
    "record-links": "care_record_links",
    "record-status-history": "care_record_status_history",
    "fact-revisions": "care_fact_revisions",
    "assertion-revisions": "care_assertion_revisions",
    "decision-revisions": "care_decision_revisions",
    "event-revisions": "care_event_revisions",
    "observation-revisions": "care_observation_revisions",
    "change-revisions": "care_change_revisions",
    "situation-revisions": "care_situation_revisions",
    "evidence-revisions": "care_evidence_revisions",
    "record-retractions": "care_record_retractions",
    "record-restorations": "care_record_restorations",
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
    "caregiver-observations": {"observation_category", "description", "observed_value", "observed_at", "recorded_at", "epistemic_status", "evidence_ids", "source_type", "source_id"},
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
    "record-identifiers": {"care_record_id", "identifier_type", "identifier_fingerprint", "masked_value", "source_type", "source_id", "status", "valid_from", "valid_to"},
    "record-sections": {"care_record_id", "section_key", "section_payload"},
    "record-links": {"source_record_id", "target_record_id", "relationship"},
    "record-retractions": {"care_record_id", "target_type", "target_id", "reason", "provenance_id"},
    "record-restorations": {"care_record_id", "retraction_id", "reason", "provenance_id"},
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
        "care_assertion": "care_assertions", "assertion": "care_assertions",
        "care_state_revision": "care_state_revisions", "state_revision": "care_state_revisions",
        "caregiver_context": "caregiver_contexts", "caregiver_perspective": "caregiver_perspectives",
        "caregiver_observation": "caregiver_observations",
        "record_change_set": "care_record_change_sets", "care_record_change_set": "care_record_change_sets",
        "record_change_item": "care_record_change_items", "care_record_change_item": "care_record_change_items",
        "care_correction": "care_corrections", "correction": "care_corrections",
        "record_amendment": "record_amendments", "amendment": "record_amendments",
        "care_uncertainty": "care_uncertainties", "uncertainty": "care_uncertainties",
        "care_conflict": "care_conflicts", "conflict": "care_conflicts",
        "care_action_outcome": "care_action_outcomes", "action_outcome": "care_action_outcomes",
        "living_care_record": "living_care_records", "living_record": "living_care_records",
        "care_record_retraction": "care_record_retractions", "record_retraction": "care_record_retractions",
    }
    links = []
    if resource in ("follow-up-links", "relationships"):
        links.extend([
            (payload.get("source_type"), payload.get("source_id"), "source_id"),
            (payload.get("target_type"), payload.get("target_id"), "target_id"),
        ])
    elif resource in ("review-decisions", "provenance", "record-provenance"):
        if resource in ("review-decisions", "provenance"):
            links.append((payload.get("target_type"), payload.get("target_id"), "target_id"))
        if resource in ("provenance", "record-provenance") and payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource == "timeline":
        links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations"):
        if payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource in ("record-provenance-links", "record-change-items", "corrections", "amendment-items", "record-audit", "uncertainties", "conflicts", "record-retractions"):
        if payload.get("target_id"):
            links.append((payload.get("target_type"), payload.get("target_id"), "target_id"))
    elif resource == "record-identifiers":
        if payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    strict_link_resources = {"record-provenance", "record-provenance-links", "record-change-items", "corrections", "amendment-items", "record-audit", "uncertainties", "conflicts", "record-retractions", "record-identifiers"}
    for type_name, target_id, field_name in links:
        if not target_id:
            continue
        table_name = typed_tables.get(str(type_name or "").lower())
        if table_name is None:
            # Polymorphic IDs are security boundaries. Silently skipping an
            # unknown type lets a caller attach an unverified cross-record ID.
            # Reject unknown linked types for every resource, not just the
            # subset that was previously marked strict.
            raise HTTPException(status_code=422, detail=f"Unsupported {field_name} type for this record")
        linked = _table(db, table_name)
        if "person_id" not in linked.c:
            raise HTTPException(status_code=422, detail=f"{field_name} type is not person-scoped")
        row = db.execute(
            select(linked.c.id).where(linked.c.id == target_id, linked.c.person_id == person_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field_name} was not found in this care record")

    # Validate direct foreign-key-like references against the active person's
    # rows before inserts. These are logical links where the DB schema cannot
    # express a polymorphic foreign key.
    scoped_references = {
        "record-change-sets": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "record-change-items": (("care_record_change_sets", "change_set_id"), ("record_provenance", "provenance_id")),
        "record-provenance-links": (("record_provenance", "provenance_id"),),
        "corrections": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "amendments": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "amendment-items": (("record_amendments", "amendment_id"),),
        "record-audit": (("living_care_records", "care_record_id"),),
        "uncertainties": (("living_care_records", "care_record_id"),),
        "missing-information": (("living_care_records", "care_record_id"),),
        "conflicts": (("living_care_records", "care_record_id"),),
        "record-identifiers": (("living_care_records", "care_record_id"),),
        "record-sections": (("living_care_records", "care_record_id"),),
        "record-links": (("living_care_records", "source_record_id"), ("living_care_records", "target_record_id")),
        "record-retractions": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "record-restorations": (("living_care_records", "care_record_id"), ("care_record_retractions", "retraction_id"), ("record_provenance", "provenance_id")),
    }
    for table_name, field in scoped_references.get(resource, ()):
        value = payload.get(field)
        if value is None:
            continue
        linked = _table(db, table_name)
        row = db.execute(
            select(linked.c.id).where(linked.c.id == value, linked.c.person_id == person_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field} was not found in this care record")

def _append_revision_for_change_item(db: Session, person_id: str, caregiver_id: str, data: dict):
    target_type = str(data.get("target_type") or "").lower()
    revision_specs = {
        "care_fact": ("care_fact_revisions", "care_fact_id"),
        "fact": ("care_fact_revisions", "care_fact_id"),
        "care_assertion": ("care_assertion_revisions", "assertion_id"),
        "assertion": ("care_assertion_revisions", "assertion_id"),
        "care_decision": ("care_decision_revisions", "decision_id"),
        "decision": ("care_decision_revisions", "decision_id"),
        "care_event": ("care_event_revisions", "event_id"),
        "event": ("care_event_revisions", "event_id"),
        "caregiver_observation": ("care_observation_revisions", "observation_id"),
        "care_observation": ("care_observation_revisions", "observation_id"),
        "observation": ("care_observation_revisions", "observation_id"),
        "care_change": ("care_change_revisions", "change_id"),
        "change": ("care_change_revisions", "change_id"),
        "care_situation": ("care_situation_revisions", "situation_id"),
        "situation": ("care_situation_revisions", "situation_id"),
        "care_evidence": ("care_evidence_revisions", "evidence_id"),
        "evidence": ("care_evidence_revisions", "evidence_id"),
    }
    spec = revision_specs.get(target_type)
    if spec is None:
        raise HTTPException(status_code=422, detail="This target type does not have an LCR revision schema")
    revision_table_name, target_column = spec
    revision_table = _table(db, revision_table_name)
    target_id = data.get("target_id")
    latest = db.execute(
        select(revision_table.c.revision_number)
        .where(revision_table.c[target_column] == target_id, revision_table.c.person_id == person_id)
        .order_by(desc(revision_table.c.revision_number))
        .limit(1)
    ).scalar_one_or_none()
    change_sets = _table(db, "care_record_change_sets")
    change_set = db.execute(
        select(change_sets.c.reason, change_sets.c.provenance_id)
        .where(change_sets.c.id == data.get("change_set_id"), change_sets.c.person_id == person_id)
    ).mappings().first()
    db.execute(insert(revision_table).values(
        id=str(uuid.uuid4()),
        person_id=person_id,
        **{target_column: target_id},
        revision_number=int(latest or 0) + 1,
        previous_payload=data.get("before_value"),
        current_payload=data.get("after_value"),
        revision_reason=(change_set.get("reason") if change_set else None) or data.get("operation"),
        provenance_id=data.get("provenance_id") or (change_set.get("provenance_id") if change_set else None),
        changed_by_caregiver_id=caregiver_id,
        changed_at=datetime.now(timezone.utc),
    ))


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

@router.get("/record-versions")
def list_record_versions(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    versions = _table(db, "care_record_versions")
    records = _table(db, "living_care_records")
    query = (
        select(versions, records.c.person_id.label("person_id"))
        .join(records, versions.c.care_record_id == records.c.id)
        .where(records.c.person_id == person_id)
        .order_by(desc(versions.c.created_at))
    )
    rows = db.execute(query.limit(max(1, min(limit, 100)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]


@router.get("/record-snapshots")
def list_record_snapshots(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    snapshots = _table(db, "care_record_snapshots")
    records = _table(db, "living_care_records")
    query = (
        select(snapshots, records.c.person_id.label("person_id"))
        .join(records, snapshots.c.care_record_id == records.c.id)
        .where(records.c.person_id == person_id)
        .order_by(desc(snapshots.c.as_of))
    )
    rows = db.execute(query.limit(max(1, min(limit, 100)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]


@router.get("/record-revisions")
def list_record_revisions(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    revision_specs = (
        ("care_fact_revisions", "care_fact_id"),
        ("care_assertion_revisions", "assertion_id"),
        ("care_decision_revisions", "decision_id"),
        ("care_event_revisions", "event_id"),
        ("care_observation_revisions", "observation_id"),
        ("care_change_revisions", "change_id"),
        ("care_situation_revisions", "situation_id"),
        ("care_evidence_revisions", "evidence_id"),
    )
    inspector = inspect(db.get_bind())
    collected = []
    for table_name, target_column in revision_specs:
        if not inspector.has_table(table_name):
            continue
        table = _table(db, table_name)
        rows = db.execute(
            select(table).where(table.c.person_id == person_id)
            .order_by(desc(table.c.changed_at))
            .limit(max(1, min(limit, 100)))
        ).mappings().all()
        for row in rows:
            item = dict(row)
            target_id = item.pop(target_column, None)
            item["target_id"] = target_id
            item["revision_type"] = table_name
            changed_at = item.get("changed_at")
            if isinstance(changed_at, datetime):
                if changed_at.tzinfo is None:
                    changed_at = changed_at.replace(tzinfo=timezone.utc)
                sort_key = changed_at.timestamp()
            else:
                sort_key = 0
            collected.append((sort_key, item))
    collected.sort(key=lambda pair: pair[0], reverse=True)
    return [_jsonable(item) for _, item in collected[:max(1, min(limit, 100))]]


@router.post("/record-versions", status_code=201)
def create_record_version(payload: dict, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scoped_identity(request, db)
    care_record_id = payload.get("care_record_id")
    if not care_record_id:
        raise HTTPException(status_code=422, detail="care_record_id is required")
    records = _table(db, "living_care_records")
    record = db.execute(
        select(records).where(records.c.id == care_record_id, records.c.person_id == person_id)
    ).mappings().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Living Care Record was not found in this session")

    # Snapshot only canonical persisted rows belonging to the session's person.
    snapshot_tables = (
        "care_facts", "care_assertions", "care_events", "evidence", "changes", "situations",
        "care_state_revisions", "care_delta_runs", "care_delta_items", "care_decisions",
        "decision_outcomes", "care_timeline_entries", "care_follow_up_links",
        "care_review_decisions", "care_provenance_records", "care_uncertainties",
        "care_missing_information", "care_conflicts", "care_action_outcomes",
        "record_provenance", "record_provenance_links", "care_record_change_sets",
        "care_record_change_items", "care_corrections", "record_amendments",
        "record_amendment_items", "care_record_audit_log",
        "care_record_identifiers", "care_record_sections", "care_record_links", "care_record_status_history",
        "care_fact_revisions", "care_assertion_revisions", "care_decision_revisions", "care_event_revisions",
        "care_observation_revisions", "care_change_revisions", "care_situation_revisions", "care_evidence_revisions",
        "care_record_retractions", "care_record_restorations",
        "caregiver_contexts", "caregiver_perspectives", "caregiver_observations",
    )
    tables_payload = {}
    inspector = inspect(db.get_bind())
    for table_name in snapshot_tables:
        if not inspector.has_table(table_name):
            continue
        table = _table(db, table_name)
        if "person_id" not in table.c:
            continue
        query = select(table).where(table.c.person_id == person_id)
        if "created_at" in table.c:
            query = query.order_by(desc(table.c.created_at))
        rows = db.execute(query.limit(1000)).mappings().all()
        tables_payload[table_name] = [_jsonable(dict(row)) for row in rows]

    captured_at = datetime.now(timezone.utc)
    snapshot_payload = {"person_id": person_id, "captured_at": captured_at.isoformat(), "tables": tables_payload}
    encoded = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    versions = _table(db, "care_record_versions")
    snapshots = _table(db, "care_record_snapshots")
    latest = db.execute(
        select(versions.c.version_number)
        .where(versions.c.care_record_id == care_record_id)
        .order_by(desc(versions.c.version_number))
        .limit(1)
    ).scalar_one_or_none()
    version_id = str(uuid.uuid4())
    snapshot_id = str(uuid.uuid4())
    version_number = int(latest or 0) + 1
    reason = str(payload.get("reason") or "care-continuity snapshot")[:2000]
    try:
        db.execute(insert(versions).values(
            id=version_id, care_record_id=care_record_id, version_number=version_number,
            record_digest=digest, snapshot_payload=snapshot_payload,
            created_by_caregiver_id=caregiver_id, reason=reason,
        ))
        db.execute(insert(snapshots).values(
            id=snapshot_id, care_record_id=care_record_id, version_id=version_id,
            as_of=captured_at, snapshot_payload=snapshot_payload, record_digest=digest,
            created_by_caregiver_id=caregiver_id,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record version could not be committed; refresh and retry") from exc
    return {"id": version_id, "care_record_id": care_record_id, "version_number": version_number,
            "snapshot_id": snapshot_id, "record_digest": digest, "captured_at": captured_at.isoformat(),
            "tables_captured": sorted(tables_payload.keys())}


@router.get("/{resource}")
def list_resource(resource: str, request: Request, limit: int = 50, db: Session = Depends(get_db)):
    if resource not in RESOURCES:
        raise HTTPException(status_code=404, detail="Unknown care-continuity resource")
    person_id, _ = _scoped_identity(request, db)
    table = _table(db, RESOURCES[resource])
    query = select(table).where(table.c.person_id == person_id)
    ordering = []
    # Revision sequence is the authoritative order for revisions of a given
    # care item. Timestamps can tie or lose ordering precision across database
    # backends, so use the monotonic revision number before timestamp fallback.
    if "revision_number" in table.c:
        ordering.append(desc(table.c.revision_number))
    if "created_at" in table.c:
        ordering.append(desc(table.c.created_at))
    elif "changed_at" in table.c:
        ordering.append(desc(table.c.changed_at))
    elif "updated_at" in table.c:
        ordering.append(desc(table.c.updated_at))
    if ordering:
        query = query.order_by(*ordering)
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
        "caregiver-observations": ("observation_category", "description"),
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
        "record-identifiers": ("care_record_id", "identifier_type", "identifier_fingerprint"),
        "record-sections": ("care_record_id", "section_key", "section_payload"),
        "record-links": ("source_record_id", "target_record_id", "relationship"),
        "record-retractions": ("care_record_id", "target_type", "target_id", "reason"),
        "record-restorations": ("care_record_id", "retraction_id", "reason"),
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
    # The legacy caregiver_id points to caregiver_profiles, not session caregivers.
    # New session attribution uses a separate server-bound column.
    if "caregiver_id" in table.c and resource not in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations"):
        data["caregiver_id"] = caregiver_id
    if resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations") and "reported_by_caregiver_id" in table.c:
        data["reported_by_caregiver_id"] = caregiver_id
    if resource == "caregiver-contexts" and "effective_start" in table.c and not data.get("effective_start"):
        data["effective_start"] = data.get("effective_from") or datetime.now(timezone.utc)
    if resource == "caregiver-perspectives":
        if "claim_type" in table.c:
            data["claim_type"] = ClaimType.INTERPRETATION.name
        if "claim_content" in table.c:
            data["claim_content"] = data.get("perspective_text")
        if "information_visibility" in table.c:
            data["information_visibility"] = InformationVisibility.PRIVATE.name
        if "is_promoted_to_shared" in table.c:
            data["is_promoted_to_shared"] = False
    if resource == "caregiver-observations":
        if "observation_content" in table.c:
            data["observation_content"] = data.get("description")
        if "observation_type" in table.c:
            data["observation_type"] = ObservationType.DIRECT.name
        if "information_visibility" in table.c:
            data["information_visibility"] = InformationVisibility.PRIVATE.name
        if "is_promoted_to_shared" in table.c:
            data["is_promoted_to_shared"] = False
        if "event_time" in table.c and data.get("observed_at"):
            data["event_time"] = data["observed_at"]
    if resource == "record-provenance" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "record-provenance" and "method" in table.c and not data.get("method"):
        data["method"] = "manual"
    if resource == "record-change-sets" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "corrections" and "requested_by_caregiver_id" in table.c:
        data["requested_by_caregiver_id"] = caregiver_id
    if resource == "amendments" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "record-audit":
        data["actor_type"] = "caregiver"
        data["actor_id"] = caregiver_id
        if "occurred_at" in table.c:
            data["occurred_at"] = datetime.now(timezone.utc)
    # recorded_at is server receipt time, never a client-controlled timestamp.
    if resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations", "record-provenance") and "recorded_at" in table.c:
        data["recorded_at"] = datetime.now(timezone.utc)
    if resource == "record-change-sets" and not data.get("status"):
        data["status"] = "committed"
    if resource == "record-identifiers":
        fingerprint = str(data.get("identifier_fingerprint") or "")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{128})", fingerprint):
            raise HTTPException(status_code=422, detail="identifier_fingerprint must be a SHA-256 or SHA-512 hex digest")
        if not data.get("status"):
            data["status"] = "active"
    if resource == "record-links" and data.get("source_record_id") == data.get("target_record_id"):
        raise HTTPException(status_code=422, detail="A Living Care Record cannot link to itself")
    if resource == "record-sections":
        try:
            existing = db.execute(
                select(table).where(
                    table.c.person_id == person_id,
                    table.c.care_record_id == data["care_record_id"],
                    table.c.section_key == data["section_key"],
                )
            ).mappings().first()
            now = datetime.now(timezone.utc)
            if existing:
                db.execute(update(table).where(
                    table.c.id == existing["id"], table.c.person_id == person_id
                ).values(
                    section_payload=data["section_payload"],
                    version_number=int(existing.get("version_number") or 1) + 1,
                    updated_at=now,
                    updated_by_caregiver_id=caregiver_id,
                ))
                updated = db.execute(select(table).where(table.c.id == existing["id"], table.c.person_id == person_id)).mappings().one()
                db.commit()
                return _jsonable(dict(updated))
            data["version_number"] = 1
            data["updated_at"] = now
            data["updated_by_caregiver_id"] = caregiver_id
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Care record section could not be saved; refresh and retry") from exc
    if resource == "record-retractions":
        data["status"] = "retracted"
        data["retracted_by_caregiver_id"] = caregiver_id
        data["retracted_at"] = datetime.now(timezone.utc)
    if resource == "record-restorations":
        retractions = _table(db, "care_record_retractions")
        prior = db.execute(
            select(retractions.c.status).where(
                retractions.c.id == data["retraction_id"], retractions.c.person_id == person_id
            )
        ).scalar_one_or_none()
        if prior != "retracted":
            raise HTTPException(status_code=409, detail="Only an active retraction can be restored")
        data["restored_by_caregiver_id"] = caregiver_id
        data["restored_at"] = datetime.now(timezone.utc)
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
        if resource == "record-change-items":
            _append_revision_for_change_item(db, person_id, caregiver_id, data)
        if resource == "living-records" and inspect(db.get_bind()).has_table("care_record_status_history"):
            status_history = _table(db, "care_record_status_history")
            db.execute(insert(status_history).values(
                id=str(uuid.uuid4()), person_id=person_id, care_record_id=row["id"],
                previous_status=None, new_status=row.get("record_status") or "active",
                reason="Living Care Record created", changed_by_caregiver_id=caregiver_id,
                changed_at=datetime.now(timezone.utc),
            ))
        if resource == "record-restorations":
            retractions = _table(db, "care_record_retractions")
            db.execute(update(retractions).where(
                retractions.c.id == data["retraction_id"], retractions.c.person_id == person_id
            ).values(status="restored"))
        db.commit()
        return _jsonable(dict(row))
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Care-continuity record could not be saved ({type(exc).__name__}); check linked IDs and field types") from exc
