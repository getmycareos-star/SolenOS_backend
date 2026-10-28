"""Authenticated, person-scoped API for deep care-continuity records.

The API deliberately reuses existing evidence, events, changes, situations,
temporal and uncertainty tables. It does not create duplicate open-loop or
evidence stores. New records are always scoped from the bearer session, never
from a caller-supplied person_id.
"""
from datetime import datetime, date, timezone
from typing import Any
import json
import logging
import uuid
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import MetaData, Table, select, insert, update, desc, inspect, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.care_delta import _scoped_identity
from app.core.caregiver_enums import ClaimType, ObservationType, InformationVisibility
from app.services.lcr_context import build_lcr_context
from app.models.care import Evidence
from app.models.care_continuity import EvidenceReviewRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/care-continuity", tags=["care-continuity"])


REVIEW_TYPES = {
    "quality": {"adequate", "limited", "insufficient", "undetermined"},
    "reliability": {"supported", "contested", "unverified", "undetermined"},
    "source_confidence": {"verified", "partially_verified", "unverified", "unknown"},
    "freshness": {"current", "stale", "historical", "undetermined"},
    "completeness": {"complete", "partial", "missing", "undetermined"},
}


@router.post("/evidence/{evidence_id}/reviews", status_code=201)
def create_evidence_review(evidence_id: str, payload: dict[str, Any], request: Request, db: Session = Depends(get_db)):
    """Append a versioned, evidence-linked review without overwriting source evidence."""
    person_id, caregiver_id = _scoped_identity(request, db)
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")

    review_type = str(payload.get("review_type", "")).strip()
    outcome = str(payload.get("outcome", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    policy_version = str(payload.get("policy_version", "")).strip()
    criteria = payload.get("criteria")
    if review_type not in REVIEW_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported evidence review type")
    if outcome not in REVIEW_TYPES[review_type]:
        raise HTTPException(status_code=422, detail="Outcome is not valid for this review type")
    if not rationale:
        raise HTTPException(status_code=422, detail="Review rationale is required")
    if not policy_version or len(policy_version) > 64:
        raise HTTPException(status_code=422, detail="A bounded policy version is required")
    if not isinstance(criteria, list) or not criteria:
        raise HTTPException(status_code=422, detail="Structured review criteria are required")
    for item in criteria:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("criterion", "result", "rationale")
        ):
            raise HTTPException(
                status_code=422,
                detail="Each criterion must include criterion, result, and rationale text",
            )

    attribution = {
        "evidence_id": evidence.id,
        "person_id": evidence.person_id,
        "evidence_type": evidence.type,
        "source_type": evidence.source_type,
        "source_text_present": bool(evidence.source_text),
        "uploaded_at": evidence.uploaded_at.isoformat() if evidence.uploaded_at else None,
        "uploaded_by_caregiver_id": evidence.uploaded_by_caregiver_id,
        "time_provenance": evidence.time_provenance,
        "location": evidence.location,
        "source_span_start": evidence.source_span_start,
        "source_span_end": evidence.source_span_end,
    }
    row = EvidenceReviewRecord(
        evidence_id=evidence.id,
        person_id=person_id,
        review_type=review_type,
        outcome=outcome,
        rationale=rationale,
        criteria=criteria,
        policy_version=policy_version,
        source_attribution_snapshot=json.dumps(attribution, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        reviewed_by_caregiver_id=caregiver_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("Evidence review could not be persisted")
        raise HTTPException(status_code=503, detail="Evidence review could not be persisted") from exc
    return {
        "id": row.id,
        "evidence_id": row.evidence_id,
        "person_id": row.person_id,
        "review_type": row.review_type,
        "outcome": row.outcome,
        "rationale": row.rationale,
        "criteria": row.criteria,
        "policy_version": row.policy_version,
        "source_attribution": json.loads(row.source_attribution_snapshot),
        "reviewed_by_caregiver_id": row.reviewed_by_caregiver_id,
        "created_at": row.created_at,
    }


@router.get("/evidence/{evidence_id}/reviews")
def list_evidence_reviews(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    evidence = db.query(Evidence.id).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    rows = db.query(EvidenceReviewRecord).filter(
        EvidenceReviewRecord.evidence_id == evidence_id,
        EvidenceReviewRecord.person_id == person_id,
    ).order_by(EvidenceReviewRecord.created_at.asc(), EvidenceReviewRecord.id.asc()).all()
    return [
        {
            "id": row.id,
            "evidence_id": row.evidence_id,
            "person_id": row.person_id,
            "review_type": row.review_type,
            "outcome": row.outcome,
            "rationale": row.rationale,
            "criteria": row.criteria,
            "policy_version": row.policy_version,
            "source_attribution": json.loads(row.source_attribution_snapshot),
            "reviewed_by_caregiver_id": row.reviewed_by_caregiver_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]

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


@router.get("/context")
def read_lcr_context(
    request: Request,
    limit_per_source: int = Query(default=20, ge=1, le=50),
    query: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
):
    """Read one bounded, permission-scoped context projection shared by LCR-facing modules."""
    person_id, _ = _scoped_identity(request, db)
    return build_lcr_context(db, person_id, limit_per_source=limit_per_source, query=query)


@router.get("/current-care")
def current_care_projection(
    request: Request,
    limit_per_source: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Return a bounded caregiver-facing projection from existing scoped LCR sources.

    This is a read-only projection, not a new source of truth. It deliberately
    retains competing claims and labels source/status rather than inferring that
    the newest report is necessarily correct.
    """
    person_id, _ = _scoped_identity(request, db)
    context = build_lcr_context(db, person_id, limit_per_source=limit_per_source)
    sources = context.get("sources") or {}
    current_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(source_name: str, row: dict[str, Any], kind: str, priority: int, *, require_open: bool = False) -> None:
        source_id = str(row.get("id") or "")
        if not source_id or (source_name, source_id) in seen:
            return
        raw_status = row.get("status") or row.get("lifecycle_status") or row.get("reconciliation_status") or row.get("assertion_status") or row.get("decision_status") or row.get("epistemic_status")
        status = str(raw_status).strip().lower() if raw_status is not None else "status_unknown"
        closed_statuses = {"resolved", "completed", "closed", "reconciled", "withdrawn", "retracted", "superseded", "cancelled"}
        if require_open and status in closed_statuses:
            return
        if source_name == "facts" and row.get("superseded_by_fact_id"):
            return
        if source_name == "assertions" and status in {"retracted", "superseded"}:
            return

        title_fields = ("title", "topic", "question", "conflict_type", "fact_type", "field", "follow_up_kind", "target_type",
                        "observation_category", "decision_text", "description", "event_type")
        description_fields = ("description", "question", "notes", "fact_value", "asserted_value",
                              "perspective_text", "observed_value", "decision_text", "summary", "resolution", "decision_notes")
        title = next((str(row[k]).strip() for k in title_fields if isinstance(row.get(k), str) and row[k].strip()), kind)
        description = next((str(row[k]).strip() for k in description_fields if isinstance(row.get(k), str) and row[k].strip()), "")
        occurred_at = row.get("occurred_at") or row.get("observed_at") or row.get("effective_at") or row.get("decided_at")
        recorded_at = row.get("recorded_at") or row.get("created_at") or row.get("uploaded_at") or row.get("asked_at")
        evidence_ids = row.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            evidence_ids = row.get("source_ids") if isinstance(row.get("source_ids"), list) else []
        source_label = row.get("source_label") or row.get("author_label")
        origin_type = row.get("source_type") or "not_recorded"
        claim_refs = row.get("claim_refs") or row.get("competing_claim_ids")
        if isinstance(claim_refs, str):
            try:
                claim_refs = json.loads(claim_refs)
            except (TypeError, json.JSONDecodeError):
                claim_refs = []
        if not isinstance(claim_refs, list):
            claim_refs = []
        current_items.append({
            "id": source_id,
            "kind": kind,
            "priority": priority,
            "title": title[:240],
            "description": description[:1600],
            "status": status,
            "epistemic_status": row.get("epistemic_status") or row.get("epistemic_state") or row.get("assertion_status") or row.get("decision_status") or "not_specified",
            "source_collection": source_name,
            "source_type": str(origin_type)[:80],
            "source_label": str(source_label)[:160] if source_label else "Source label not recorded",
            "occurred_at": occurred_at,
            "due_at": row.get("due_at"),
            "recorded_at": recorded_at,
            "evidence_ids": evidence_ids[:50],
            "claim_refs": claim_refs[:20],
            "claim_count": len(claim_refs),
        })
        seen.add((source_name, source_id))

    # Highest priority first: unresolved disagreement, then explicit unknowns,
    # missing information and open questions. These are never auto-resolved.
    for row in (sources.get("reconciliations") or {}).get("items", []):
        add("reconciliations", row, "Reconciliation review", 0, require_open=True)
    for row in (sources.get("conflicts") or {}).get("items", []):
        add("conflicts", row, "Conflict", 0, require_open=True)
    for row in (sources.get("uncertainties") or {}).get("items", []):
        add("uncertainties", row, "Needs clarification", 1, require_open=True)
    for row in (sources.get("information_gaps") or {}).get("items", []):
        add("information_gaps", row, "Information missing", 2, require_open=True)
    for row in (sources.get("missing_information") or {}).get("items", []):
        add("missing_information", row, "Information missing", 2, require_open=True)
    for row in (sources.get("open_questions") or {}).get("items", []):
        add("open_questions", row, "Open question", 3, require_open=True)
    for row in (sources.get("follow_up_links") or {}).get("items", []):
        add("follow_up_links", row, "Follow-up", 4, require_open=True)
    for row in (sources.get("events") or {}).get("items", []):
        event_type = str(row.get("event_type") or "").strip().lower()
        needs_attention = row.get("attention_candidate") is True or row.get("follow_up_candidate") is True
        is_daily_capture = event_type in {"structured_daily_capture", "daily_care_entry"}
        if needs_attention:
            add("events", row, "Care event needing attention", 5, require_open=True)
        elif is_daily_capture:
            # Daily Care is a source event in the longitudinal view even when
            # no automated attention/follow-up flag was raised. It remains a
            # caregiver-recorded event, not a verified clinical fact.
            add("events", row, "Daily care entry", 6)
    for row in (sources.get("caregiver_observations") or {}).get("items", []):
        add("caregiver_observations", row, "Caregiver observation", 6)
    for row in (sources.get("facts") or {}).get("items", []):
        add("facts", row, "Recorded information", 7)
    for row in (sources.get("assertions") or {}).get("items", []):
        add("assertions", row, "Reported information", 8, require_open=True)
    for row in (sources.get("decisions") or {}).get("items", []):
        add("decisions", row, "Care decision", 9, require_open=True)

    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        return (int(item["priority"]), str(item.get("recorded_at") or item.get("occurred_at") or ""))

    current_items.sort(key=sort_key, reverse=False)
    # Preserve priority ordering; within each priority, newest recorded item first.
    ordered: list[dict[str, Any]] = []
    for priority in sorted({item["priority"] for item in current_items}):
        group = [item for item in current_items if item["priority"] == priority]
        group.sort(key=lambda item: str(item.get("recorded_at") or item.get("occurred_at") or ""), reverse=True)
        ordered.extend(group)

    return {
        "person_id": person_id,
        "as_of": context.get("as_of"),
        "projection_type": "bounded_evidence_derived_current_care",
        "read_only": True,
        "history_coverage": context.get("history_coverage", "bounded_subset"),
        "schema_status": context.get("schema_status", "partial"),
        "missing_required_sources": context.get("missing_required_sources", []),
        "missing_optional_sources": context.get("missing_optional_sources", []),
        "items": ordered[:12],
        "item_count": min(len(ordered), 12),
        "has_more": len(ordered) > 12,
        "source_coverage": {
            name: {"status": value.get("status"), "returned_count": value.get("returned_count", 0), "has_more": value.get("has_more", False)}
            for name, value in sources.items()
        },
        "notice": "This is a bounded record view, not a clinical conclusion. Conflicting claims remain separate; unavailable sources may make this view incomplete.",
    }



def _build_situation_understanding_assessment(db: Session, person_id: str) -> dict[str, Any]:
    """Compose an explainable assessment from persisted LCR sources; never invent care facts."""
    context = build_lcr_context(db, person_id, limit_per_source=50)
    sources = context.get("sources") or {}
    source_domains = {
        "events": "current_state_reconstruction",
        "facts": "current_care_reality",
        "assertions": "care_context_reconstruction",
        "caregiver_observations": "functional_status_understanding",
        "caregiver_contexts": "caregiver_capacity_understanding",
        "caregiver_perspectives": "care_needs_understanding",
        "appointments": "care_context_awareness",
        "decision_outcomes": "longitudinal_care_understanding",
        "decisions": "situation_evolution_tracking",
        "uncertainties": "situation_confidence_assessment",
        "uncertainty_assessments": "situation_confidence_assessment",
        "information_gaps": "situation_prioritisation",
        "missing_information": "situation_prioritisation",
        "conflicts": "whole_situation_reasoning",
        "reconciliations": "cross_domain_care_reasoning",
        "provenance": "care_context_reconstruction",
        "timeline": "historical_state_reconstruction",
        "follow_up_links": "situation_reassessment",
    }
    evidence_ids: set[str] = set()
    domain_sources: dict[str, dict[str, Any]] = {}
    all_items: list[dict[str, Any]] = []
    for source_name, source in sources.items():
        rows = source.get("items") or []
        for row in rows:
            all_items.append({"source": source_name, **row})
            ids = row.get("evidence_ids")
            if isinstance(ids, list):
                evidence_ids.update(str(value) for value in ids if value)
        domain = source_domains.get(source_name)
        if domain:
            domain_sources.setdefault(domain, {"source_names": [], "record_count": 0, "source_statuses": []})
            domain_sources[domain]["source_names"].append(source_name)
            domain_sources[domain]["record_count"] += len(rows)
            domain_sources[domain]["source_statuses"].append(source.get("status", "unknown"))
    for domain, value in domain_sources.items():
        value["source_names"] = sorted(set(value["source_names"]))
        value["source_statuses"] = sorted(set(value["source_statuses"]))
        value["evidence_state"] = "supported_by_persisted_records" if value["record_count"] else "no_records_returned"
    open_statuses = {"open", "active", "pending", "unresolved", "needs_review", "not_started"}
    attention_items = []
    uncertainty_items = []
    missing_items = []
    for item in all_items:
        status = str(item.get("status") or item.get("lifecycle_status") or item.get("assertion_status") or "").lower()
        source = item["source"]
        summary_item = {
            "id": str(item.get("id") or ""),
            "source": source,
            "title": str(item.get("title") or item.get("topic") or item.get("question") or item.get("fact_type") or item.get("field") or item.get("event_type") or source)[:240],
            "status": status or str(item.get("epistemic_state") or item.get("epistemic_status") or "not_recorded").lower(),
            "epistemic_status": item.get("epistemic_status") or item.get("epistemic_state") or item.get("assertion_status") or "not_specified",
            "occurred_at": item.get("occurred_at") or item.get("observed_at") or item.get("effective_at"),
            "recorded_at": item.get("recorded_at") or item.get("created_at") or item.get("uploaded_at"),
            "evidence_ids": item.get("evidence_ids") if isinstance(item.get("evidence_ids"), list) else [],
        }
        if source in {"conflicts", "reconciliations", "uncertainties", "uncertainty_assessments", "information_gaps", "missing_information", "open_questions"} and status not in {"resolved", "closed", "completed", "reconciled", "withdrawn", "retracted", "superseded", "cancelled", "known"}:
            (uncertainty_items if source in {"conflicts", "reconciliations", "uncertainties", "uncertainty_assessments"} else missing_items).append(summary_item)
        elif source == "events" and (item.get("attention_candidate") is True or item.get("follow_up_candidate") is True):
            attention_items.append(summary_item)
        elif source == "follow_up_links" and status in open_statuses:
            attention_items.append(summary_item)
    capability_specs = {
        "situation_understanding": ["events", "facts", "assertions", "caregiver_observations", "caregiver_contexts", "caregiver_perspectives", "decisions", "decision_outcomes", "timeline", "uncertainties", "uncertainty_assessments", "information_gaps", "conflicts"],
        "current_care_reality": ["events", "facts", "assertions", "caregiver_observations", "caregiver_contexts"],
        "care_context_reconstruction": ["assertions", "evidence", "provenance", "events", "facts"],
        "whole_situation_reasoning": ["events", "facts", "assertions", "caregiver_observations", "caregiver_contexts", "appointments", "conflicts", "reconciliations"],
        "longitudinal_care_understanding": ["timeline", "events", "facts", "decisions", "decision_outcomes"],
        "cross_domain_care_reasoning": ["facts", "caregiver_observations", "caregiver_contexts", "reconciliations"],
        "care_context_awareness": ["living_record", "appointments", "caregiver_contexts", "caregiver_perspectives"],
        "functional_status_understanding": ["caregiver_observations", "facts", "assertions", "events"],
        "care_needs_understanding": ["information_gaps", "missing_information", "open_questions", "facts", "caregiver_observations"],
        "caregiver_capacity_understanding": ["caregiver_contexts", "caregiver_perspectives", "follow_up_links"],
        "dependency_understanding": ["facts", "caregiver_contexts", "caregiver_observations", "follow_up_links"],
        "environmental_context_understanding": ["caregiver_contexts", "caregiver_observations", "facts", "events"],
        "baseline_establishment": ["facts", "assertions", "caregiver_observations", "events"],
        "current_state_reconstruction": ["events", "facts", "assertions", "caregiver_observations"],
        "historical_state_reconstruction": ["timeline", "events", "decisions", "decision_outcomes", "corrections"],
        "situation_summarisation": ["events", "facts", "assertions", "decisions", "decision_outcomes", "uncertainties", "uncertainty_assessments", "conflicts"],
        "situation_prioritisation": ["conflicts", "reconciliations", "uncertainties", "information_gaps", "missing_information", "open_questions", "follow_up_links", "events"],
        "situation_reassessment": ["follow_up_links", "appointments", "information_gaps", "uncertainties", "events"],
        "situation_evolution_tracking": ["events", "facts", "decisions", "decision_outcomes", "timeline"],
        "situation_confidence_assessment": ["evidence", "provenance", "uncertainties", "uncertainty_assessments", "conflicts", "reconciliations", "assertions"],
    }
    capabilities = {}
    for capability, source_names in capability_specs.items():
        records = sum(len((sources.get(source_name) or {}).get("items") or []) for source_name in source_names)
        unavailable = [source_name for source_name in source_names if (sources.get(source_name) or {}).get("status") == "unavailable_schema"]
        capabilities[capability] = {
            "source_names": source_names,
            "record_count": records,
            "evidence_state": "supported_by_persisted_records" if records else "insufficient_evidence",
            "unavailable_sources": unavailable,
        }
    required_missing = context.get("missing_required_sources") or []
    optional_missing = context.get("missing_optional_sources") or []
    return {
        "schema_version": "situation-understanding.v1",
        "person_id": str(person_id),
        "as_of": context.get("as_of"),
        "assessment_method": "deterministic_composition_of_persisted_lcr_sources",
        "read_only_source_of_truth": True,
        "history_coverage": context.get("history_coverage", "bounded_subset"),
        "schema_status": context.get("schema_status", "partial"),
        "missing_required_sources": required_missing,
        "missing_optional_sources": optional_missing,
        "coverage": {
            "sources_examined": len(sources),
            "sources_with_records": sum(1 for value in sources.values() if value.get("items")),
            "records_examined": len(all_items),
            "distinct_linked_evidence_ids": len(evidence_ids),
            "is_complete": not required_missing and not optional_missing and not any(value.get("has_more") for value in sources.values()),
        },
        "domains": domain_sources,
        "capabilities": capabilities,
        "current_care_reality": {
            "records": [item for item in all_items if item["source"] in {"facts", "events", "assertions", "caregiver_observations", "caregiver_contexts"}][:100],
            "notice": "Records are reported with their source status. A record or missing record is not, by itself, proof of clinical truth or absence.",
        },
        "longitudinal_context": {
            "historical_records": [item for item in all_items if item["source"] in {"timeline", "decision_outcomes", "decisions"}][:100],
            "bounded": True,
        },
        "priorities": attention_items[:50],
        "uncertainties_and_conflicts": uncertainty_items[:100],
        "missing_information": missing_items[:100],
        "confidence_assessment": {
            "overall": "not_calibrated",
            "basis": {
                "source_coverage": "partial" if required_missing or optional_missing else "available",
                "linked_evidence_ids": len(evidence_ids),
                "records_without_explicit_evidence_link": sum(1 for item in all_items if not item.get("evidence_ids")),
                "history_coverage": context.get("history_coverage", "bounded_subset"),
            },
            "limitations": ["No calibrated probability is asserted.", "Unavailable or bounded sources can omit relevant context.", "Reported claims and derived assessments are not automatically verified care facts."],
        },
        "notice": "This assessment composes existing persisted LCR sources. It does not diagnose, infer causation, or promote unreviewed claims into authoritative facts.",
    }


@router.get("/situation-understanding")
def get_situation_understanding(request: Request, db: Session = Depends(get_db)):
    """Return the latest persisted assessment for the session-scoped person."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    row = db.execute(
        select(table).where(
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).order_by(desc(table.c.created_at)).limit(1)
    ).mappings().first()
    if row is None:
        return {"person_id": person_id, "status": "not_assessed", "assessment": None}
    try:
        assessment = json.loads(row["delta_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Stored situation assessment is invalid") from exc
    return {"person_id": person_id, "status": "available", "assessment_id": row["id"], "created_at": row["created_at"], "assessment": assessment}


@router.get("/situation-understanding/history")
def situation_understanding_history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return immutable, person-scoped assessment snapshots newest first."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    rows = db.execute(
        select(table).where(
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).order_by(desc(table.c.created_at)).limit(limit)
    ).mappings().all()
    items = []
    for row in rows:
        try:
            assessment = json.loads(row["delta_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail="Stored situation assessment is invalid") from exc
        items.append({
            "assessment_id": row["id"],
            "created_at": _jsonable(row["created_at"]),
            "assessment": assessment,
        })
    return {"person_id": person_id, "items": items, "count": len(items)}


@router.get("/situation-understanding/history/{assessment_id}")
def get_situation_understanding_snapshot(
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Read one immutable snapshot only when it belongs to the session-scoped person."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    row = db.execute(
        select(table).where(
            table.c.id == assessment_id,
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).limit(1)
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Situation assessment snapshot was not found")
    try:
        assessment = json.loads(row["delta_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Stored situation assessment is invalid") from exc
    return {
        "person_id": person_id,
        "assessment_id": row["id"],
        "created_at": _jsonable(row["created_at"]),
        "assessment": assessment,
    }


@router.post("/situation-understanding/reassess")
def reassess_situation_understanding(request: Request, db: Session = Depends(get_db)):
    """Persist an evidence-derived snapshot; repeated identical assessments are deduplicated."""
    person_id, caregiver_id = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    assessment = _build_situation_understanding_assessment(db, person_id)
    latest = db.execute(
        select(table).where(
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).order_by(desc(table.c.created_at)).limit(1)
    ).mappings().first()
    if latest:
        try:
            previous_assessment = json.loads(latest.get("delta_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            previous_assessment = {}
        previous_comparison = dict(previous_assessment) if isinstance(previous_assessment, dict) else {}
        current_comparison = dict(assessment)
        # Compare semantic content, excluding the assessment clock.
        previous_comparison.pop("as_of", None)
        current_comparison.pop("as_of", None)
        previous_records = {
            (str(item.get("source", "")), str(item.get("id", ""))): item
            for item in (previous_assessment.get("current_care_reality", {}).get("records", []) if isinstance(previous_assessment, dict) else [])
            if item.get("id")
        }
        current_records = {
            (str(item.get("source", "")), str(item.get("id", ""))): item
            for item in assessment.get("current_care_reality", {}).get("records", [])
            if item.get("id")
        }
        changes = []
        for key, current in current_records.items():
            prior = previous_records.get(key)
            if prior is None:
                changes.append({"change_type": "newly_included", "source": key[0], "record_id": key[1], "current": current, "previous": None})
            elif json.dumps(prior, sort_keys=True, default=str) != json.dumps(current, sort_keys=True, default=str):
                changes.append({"change_type": "record_updated", "source": key[0], "record_id": key[1], "current": current, "previous": prior})
        for key, prior in previous_records.items():
            if key not in current_records:
                changes.append({"change_type": "no_longer_in_current_projection", "source": key[0], "record_id": key[1], "current": None, "previous": prior})
        assessment["evolution_since_previous_assessment"] = {
            "comparison_available": True,
            "previous_assessment_id": latest["id"],
            "change_count": len(changes),
            "changes": changes[:200],
            "interpretation_limit": "Projection differences only. Removal from a bounded projection does not establish deletion or clinical improvement/deterioration.",
        }
        # Avoid a new snapshot when source content and coverage are semantically unchanged.
        previous_comparison.pop("evolution_since_previous_assessment", None)
        current_comparison.pop("evolution_since_previous_assessment", None)
        if previous_comparison == current_comparison and not changes:
            return {"person_id": person_id, "status": "unchanged", "assessment_id": latest["id"], "assessment": previous_assessment}
    else:
        assessment["evolution_since_previous_assessment"] = {
            "comparison_available": False,
            "previous_assessment_id": None,
            "change_count": 0,
            "changes": [],
            "interpretation_limit": "No earlier persisted assessment exists; no historical baseline is assumed.",
        }
    serialized = json.dumps(assessment, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    evidence_ids = sorted({
        str(evidence_id)
        for domain in assessment.get("current_care_reality", {}).get("records", [])
        for evidence_id in domain.get("evidence_ids", [])
        if evidence_id
    })
    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "person_id": person_id,
        "source": "situation_understanding_assessment",
        "event_date": now,
        "source_event_id": None,
        # This endpoint uses a reflected SQLAlchemy Table, which sees the
        # JSONList TypeDecorator's TEXT storage as plain TEXT. Serialize explicitly
        # rather than binding a Python list to a TEXT column.
        "evidence_ids": json.dumps(evidence_ids, separators=(",", ":")),
        "delta_json": serialized,
        "model_name": "deterministic-lcr-composition-v1",
        "created_by_caregiver_id": caregiver_id,
        "created_at": now,
    }
    try:
        db.execute(insert(table).values(**row))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Situation assessment persistence failed for session-scoped person %s", person_id)
        raise HTTPException(status_code=503, detail="Situation assessment could not be persisted") from exc
    return {"person_id": person_id, "status": "created", "assessment_id": row["id"], "assessment": assessment}


@router.get("/summary")
def continuity_summary(request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    result = {"person_id": person_id, "sources": "existing evidence/events/changes/situations are reused",
              "resources": {}}
    # Reflect the inventory in bulk, then count in SQL. The old implementation
    # reflected every table separately and fetched every matching ID into Python,
    # causing the workspace's first care-continuity request to time out on larger
    # inventories. Keep the same keys and session-derived person scope.
    extra_resources = (
        ("open_questions", "open_questions"), ("information_gaps", "information_gaps"),
        ("changes", "changes"), ("situations", "situations"), ("events", "care_events"),
        ("evidence", "evidence"), ("temporal_contradictions", "temporal_contradictions"),
        ("uncertainty_assessments", "uncertainty_assessments"),
        ("significance_assessments", "significance_assessments"), ("delta_runs", "care_delta_runs"),
    )
    resource_to_table = dict(RESOURCES)
    resource_to_table.update(dict(extra_resources))
    table_names = list(dict.fromkeys(resource_to_table.values()))
    bind = db.get_bind()
    available_tables = set(inspect(bind).get_table_names())
    missing_tables = [name for name in table_names if name not in available_tables]
    if missing_tables:
        raise HTTPException(
            status_code=503,
            detail=f"Care-continuity schema is not ready: {missing_tables[0]}",
        )
    metadata = MetaData()
    metadata.reflect(bind=bind, only=table_names)
    for table_name in table_names:
        table = metadata.tables[table_name]
        if "person_id" not in table.c:
            raise HTTPException(
                status_code=503,
                detail=f"Care-continuity schema is not ready: {table_name}",
            )
    count_expressions = [
        select(func.count()).select_from(metadata.tables[table_name])
        .where(metadata.tables[table_name].c.person_id == person_id)
        .scalar_subquery().label(table_name)
        for table_name in table_names
    ]
    counts = dict(db.execute(select(*count_expressions)).mappings().one())
    for resource, table_name in resource_to_table.items():
        result["resources"][resource] = counts[table_name]
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
