"""Person-scoped appointment workflow persistence.

The workspace stores caregiver-entered notes and follow-up metadata separately from
canonical LCR facts. It never promotes a note to a verified clinical fact.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.time_location import Appointment
from app.models.care import CareEvent, Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import EvidenceSourceRelationship, EvidenceFreshnessAssessment, EvidenceFreshnessPolicy, CareAssertion
from app.models.ai_output_governance import AIOutputLCRProposal, AIOutputLCRDecision
from app.models.appointment_workspace import AppointmentWorkspaceRecord, AppointmentWorkspaceRevision
from app.schemas.appointment_workspace import AppointmentWorkspaceCreate, AppointmentWorkspaceUpdate

router = APIRouter(prefix="/appointment-workspaces", tags=["appointment-workspace"])

_ALLOWED_STATUS_TRANSITIONS = {
    "preparation": {"preparation", "during_visit", "outcome_pending", "follow_up_pending"},
    "during_visit": {"during_visit", "outcome_pending", "follow_up_pending"},
    "outcome_pending": {"outcome_pending", "follow_up_pending", "ready_for_review"},
    "follow_up_pending": {"follow_up_pending", "ready_for_review", "closed_with_outstanding_items"},
    "ready_for_review": {"ready_for_review", "follow_up_pending", "closed_with_outstanding_items"},
    "closed_with_outstanding_items": {"closed_with_outstanding_items", "follow_up_pending"},
}


def _request_fingerprint(value: dict) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scope(request: Request, db: Session) -> tuple[str, str]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _iso(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)

def _evidence_ids_from_row(row) -> list[str]:
    """Read only explicit evidence references; malformed values remain unlinked."""
    values = row.get("evidence_ids") if hasattr(row, "get") else None
    if values is None and hasattr(row, "get"):
        values = row.get("source_evidence_id")
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except (TypeError, ValueError):
            values = [values] if values.strip() else []
    if isinstance(values, (list, tuple, set)):
        return list(dict.fromkeys(str(value) for value in values if isinstance(value, str) and value.strip()))
    return [str(values)] if isinstance(values, str) and values.strip() else []


def _record_out(record: AppointmentWorkspaceRecord) -> dict:
    return {
        "id": record.id,
        "appointment_id": record.appointment_id,
        "status": record.status,
        "version_number": record.version_number,
        "payload": record.payload,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("")
def list_appointment_workspaces(request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    rows = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.person_id == person_id
    ).order_by(AppointmentWorkspaceRecord.updated_at.desc()).limit(50).all()
    return {"items": [_record_out(row) for row in rows], "limit": 50}


@router.post("", status_code=201)
def create_appointment_workspace(payload: AppointmentWorkspaceCreate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if len(json.dumps(payload.payload, ensure_ascii=False, default=str)) > 100_000:
        raise HTTPException(status_code=413, detail="Appointment workspace payload exceeds 100 KB")
    if payload.appointment_id:
        appointment = db.query(Appointment).filter(
            Appointment.id == payload.appointment_id,
            Appointment.person_id == person_id,
        ).first()
        if appointment is None:
            raise HTTPException(status_code=404, detail="Appointment not found for this care record")
    request_fingerprint = _request_fingerprint({
        "appointment_id": payload.appointment_id,
        "status": payload.status,
        "payload": payload.payload,
        "change_reason": payload.change_reason,
    })
    if payload.idempotency_key:
        existing = db.query(AppointmentWorkspaceRecord).filter(
            AppointmentWorkspaceRecord.person_id == person_id,
            AppointmentWorkspaceRecord.created_by_caregiver_id == caregiver_id,
            AppointmentWorkspaceRecord.idempotency_key == payload.idempotency_key,
        ).first()
        if existing is not None:
            if existing.idempotency_request_sha256 == request_fingerprint:
                return _record_out(existing)
            raise HTTPException(status_code=409, detail="This save key was already used for different appointment information")

    row = AppointmentWorkspaceRecord(
        id=str(uuid.uuid4()),
        person_id=person_id,
        appointment_id=payload.appointment_id,
        created_by_caregiver_id=caregiver_id,
        updated_by_caregiver_id=caregiver_id,
        status=payload.status,
        version_number=1,
        payload=payload.payload,
        last_revision_reason=payload.change_reason,
        idempotency_key=payload.idempotency_key,
        idempotency_request_sha256=request_fingerprint if payload.idempotency_key else None,
    )
    db.add(row)
    db.add(AppointmentWorkspaceRevision(
        id=str(uuid.uuid4()), record_id=row.id, person_id=person_id,
        revision_number=1, payload=payload.payload,
        change_reason=payload.change_reason, changed_by_caregiver_id=caregiver_id,
        idempotency_key=payload.idempotency_key,
        idempotency_request_sha256=request_fingerprint if payload.idempotency_key else None,
    ))
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        if payload.idempotency_key:
            existing = db.query(AppointmentWorkspaceRecord).filter(
                AppointmentWorkspaceRecord.person_id == person_id,
                AppointmentWorkspaceRecord.created_by_caregiver_id == caregiver_id,
                AppointmentWorkspaceRecord.idempotency_key == payload.idempotency_key,
            ).first()
            if existing is not None and existing.idempotency_request_sha256 == request_fingerprint:
                return _record_out(existing)
            if existing is not None:
                raise HTTPException(status_code=409, detail="This save key was already used for different appointment information") from exc
        raise HTTPException(status_code=503, detail="Appointment workspace could not be persisted") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Appointment workspace could not be persisted") from exc
    return _record_out(row)



@router.get("/preparation-brief")
def get_appointment_preparation_brief(request: Request, window_days: int = 90, db: Session = Depends(get_db)):
    """Return a bounded, source-linked LCR preparation view without inferring clinical meaning."""
    if window_days < 1 or window_days > 365:
        raise HTTPException(status_code=422, detail="window_days must be between 1 and 365")
    person_id, _ = _scope(request, db)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    try:
        fact_columns = {column["name"] for column in inspect(db.get_bind()).get_columns("care_facts")}
        fact_select = [
            name for name in (
                "id", "fact_type", "fact_value", "epistemic_status", "effective_at",
                "created_at", "updated_at", "evidence_ids", "source_evidence_id",
            ) if name in fact_columns
        ]
        if not {"id", "fact_type", "fact_value"}.issubset(fact_columns):
            raise RuntimeError("care_facts is missing required preparation fields")
        fact_rows = db.execute(text(
            "SELECT " + ", ".join(fact_select) + " FROM care_facts "
            "WHERE person_id = :person_id "
            "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 40"
        ), {"person_id": person_id}).mappings().all()
    except Exception:
        db.rollback()
        fact_rows = None

    events = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.occurred_at >= cutoff,
    ).order_by(CareEvent.occurred_at.desc()).limit(30).all()
    appointments = db.query(Appointment).filter(
        Appointment.person_id == person_id,
    ).order_by(Appointment.scheduled_at.desc()).limit(15).all()

    # Document text is included only after it has been extracted from a
    # persisted, person-scoped upload. Raw upload metadata alone is not treated
    # as readable clinical content.
    documents = []
    document_source_status = "empty"
    try:
        extraction_rows = (
            db.query(CaptureAnalysis)
            .filter(CaptureAnalysis.person_id == person_id)
            .order_by(CaptureAnalysis.created_at.desc())
            .limit(50)
            .all()
        )
        for extraction in extraction_rows:
            metadata = extraction.candidates if isinstance(extraction.candidates, dict) else {}
            if metadata.get("kind") != "document_extraction_v1":
                continue
            source_evidence = None
            if extraction.source_evidence_id:
                source_evidence = db.query(Evidence).filter(
                    Evidence.id == extraction.source_evidence_id,
                    Evidence.person_id == person_id,
                    Evidence.type == "document",
                ).first()
            if source_evidence is None:
                continue
            documents.append({
                "source_table": "capture_analyses",
                "source_id": str(extraction.id),
                "source_evidence_id": str(source_evidence.id),
                "filename": metadata.get("filename"),
                "extraction_method": metadata.get("extraction_method"),
                "extraction_status": extraction.extraction_status,
                "extracted_text": (extraction.source_text or "")[:4000],
                "source_text_sha256": hashlib.sha256(
                    (extraction.source_text or "").encode("utf-8")
                ).hexdigest(),
                "recorded_at": _iso(extraction.created_at),
                "uploaded_at": _iso(source_evidence.ingested_at),
                "uploaded_by_caregiver_id": source_evidence.uploaded_by_caregiver_id,
                "epistemic_status": source_evidence.epistemic_status or "unknown",
                "interpretation_limit": "Extracted document text is source material, not an accepted LCR fact or clinical verification.",
            })
        document_source_status = "available" if documents else "empty"
    except Exception:
        db.rollback()
        documents = []
        document_source_status = "failed"

    facts = []
    if fact_rows is not None:
        for row in fact_rows:
            facts.append({
                "source_table": "care_facts",
                "source_id": str(row["id"]),
                "fact_type": row["fact_type"],
                "value": row["fact_value"],
                "epistemic_status": (row["epistemic_status"] or "unknown") if "epistemic_status" in row else "unknown",
                "effective_at": _iso(row["effective_at"]) if "effective_at" in row else None,
                "recorded_at": _iso(row["created_at"]) if "created_at" in row else None,
                "updated_at": _iso(row["updated_at"]) if "updated_at" in row else None,
                "evidence_ids": _evidence_ids_from_row(row),
            })

    event_items = [{
        "source_table": "care_events",
        "source_id": str(item.id),
        "event_type": item.event_type,
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
        "recorded_at": item.recorded_at.isoformat() if item.recorded_at else None,
        "evidence_ids": item.evidence_ids or [],
    } for item in events]
    appointment_items = [{
        "source_table": "appointments",
        "source_id": str(item.id),
        "title": item.title,
        "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
        "provider": item.provider,
        "healthcare_org": item.healthcare_org,
        "location": item.location,
        "status": item.status,
        "preparation_notes": item.preparation_notes,
        "evidence_ids": item.evidence_ids or [],
    } for item in appointments]

    # Attach provenance only for evidence IDs explicitly linked to this person's
    # preparation records. Author/uploader/reporter and event time stay separate.
    referenced_evidence_ids = set()
    for row in facts:
        referenced_evidence_ids.update(row.get("evidence_ids", []))
    for row in event_items + appointment_items:
        referenced_evidence_ids.update(
            str(value) for value in row.get("evidence_ids", []) if isinstance(value, str)
        )
    evidence_sources = {}
    evidence_rows = []
    if referenced_evidence_ids:
        evidence_rows = db.query(Evidence).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_(sorted(referenced_evidence_ids)),
        ).all()
        evidence_sources = {
            row.id: {
                "evidence_id": row.id,
                "type": row.type,
                "evidence_kind": row.evidence_kind,
                "epistemic_status": row.epistemic_status or "unknown",
                "source_type": row.source_type,
                "source_text": (row.source_text or "")[:1200],
                "original_author": row.original_author,
                "reported_by_label": row.reported_by_label,
                "uploaded_by_caregiver_id": row.uploaded_by_caregiver_id,
                "reported_by_caregiver_id": row.reported_by_caregiver_id,
                "collected_by_caregiver_id": row.collected_by_caregiver_id,
                "occurred_at": _iso(row.occurred_at),
                "occurred_at_timezone": row.occurred_at_timezone,
                "collected_at": _iso(row.collected_at),
                "ingested_at": _iso(row.ingested_at),
                "original_source_reference": row.original_source_reference,
                "parent_evidence_id": row.parent_evidence_id,
                "source_text_sha256": row.source_text_sha256,
            }
            for row in evidence_rows
        }
    freshness_assessments = []
    freshness_by_evidence: dict[str, list[dict]] = {}
    freshness_schema_available = (
        inspect(db.get_bind()).has_table("evidence_freshness_assessments")
        and inspect(db.get_bind()).has_table("evidence_freshness_policies")
    )
    if referenced_evidence_ids and freshness_schema_available:
        freshness_rows = (
            db.query(EvidenceFreshnessAssessment, EvidenceFreshnessPolicy)
            .join(EvidenceFreshnessPolicy, EvidenceFreshnessPolicy.id == EvidenceFreshnessAssessment.policy_id)
            .filter(
                EvidenceFreshnessAssessment.person_id == person_id,
                EvidenceFreshnessAssessment.evidence_id.in_(sorted(referenced_evidence_ids)),
            )
            .order_by(EvidenceFreshnessAssessment.assessed_at.desc())
            .limit(100)
            .all()
        )
        seen_policy_evidence_pairs = set()
        for assessment, policy in freshness_rows:
            pair = (str(assessment.evidence_id), str(assessment.policy_id))
            if pair in seen_policy_evidence_pairs:
                continue
            seen_policy_evidence_pairs.add(pair)
            item = {
                "assessment_id": str(assessment.id),
                "evidence_id": str(assessment.evidence_id),
                "policy_id": str(policy.id),
                "domain_key": policy.domain_key,
                "policy_version": assessment.policy_version,
                "freshness_status": assessment.freshness_status,
                "source_time": _iso(assessment.source_time),
                "source_time_kind": assessment.source_time_kind,
                "assessed_at": _iso(assessment.assessed_at),
                "age_days": assessment.age_days,
                "rationale": assessment.rationale,
                "interpretation_limit": "This is a policy application, not a truth judgment or clinical currentness guarantee.",
            }
            freshness_assessments.append(item)
            freshness_by_evidence.setdefault(str(assessment.evidence_id), []).append(item)

    for evidence_id, source in evidence_sources.items():
        source["freshness_assessments"] = freshness_by_evidence.get(str(evidence_id), [])

    for row in facts + event_items + appointment_items:
        row["freshness_assessments"] = [
            assessment
            for evidence_id in row.get("evidence_ids", [])
            for assessment in freshness_by_evidence.get(str(evidence_id), [])
        ]
        row["evidence_sources"] = [
            evidence_sources[evidence_id]
            for evidence_id in row.get("evidence_ids", [])
            if evidence_id in evidence_sources
        ]
        row["evidence_reference_status"] = (
            "linked_sources_available" if row["evidence_sources"]
            else "referenced_sources_unavailable" if row.get("evidence_ids")
            else "no_evidence_linked"
        )

    from app.services.evidence_lineage import load_evidence_lineage_scope, summarize_evidence_lineage
    lineage_scope = load_evidence_lineage_scope(db, person_id, referenced_evidence_ids)
    evidence_lineage = summarize_evidence_lineage(
        lineage_scope["evidence_rows"],
        lineage_scope["relationships"],
        referenced_evidence_ids,
    )
    evidence_lineage["scope_truncated"] = bool(lineage_scope["truncated"])
    evidence_lineage["lineage_nodes_examined"] = len(lineage_scope["evidence_rows"])
    evidence_lineage["relationship_records_examined"] = len(lineage_scope["relationships"])
    if lineage_scope["truncated"]:
        evidence_lineage["interpretation_limit"] += (
            " Provenance retrieval reached a safety bound; unreturned lineage is unknown, not absent."
        )

    return {
        "person_scope_verified": True,
        "snapshot_at": now.isoformat(),
        "window_days": window_days,
        "read_only": True,
        "updates_lcr": False,
        "facts": facts,
        "events": event_items,
        "appointments": appointment_items,
        "documents": documents,
        "evidence_lineage": evidence_lineage,
        "freshness_assessments": freshness_assessments,
        "freshness_assessment_scope": {
            "status": (
                "unavailable_schema" if not freshness_schema_available
                else "latest_per_policy_for_linked_evidence" if referenced_evidence_ids
                else "not_applicable_no_linked_evidence"
            ),
            "bounded": True,
            "limit": 100,
            "history_complete": False,
            "interpretation_limit": "Full assessment history is available through the evidence-freshness history endpoint; this preparation view shows only the latest result per policy and linked evidence.",
        },
        "source_coverage": {
            # Keep this contract stable for the caregiver UI and downstream
            # consumers: empty means queried successfully with no matching rows;
            # unavailable means this source is not integrated or its schema is
            # absent; failed means the query itself could not be completed.
            "care_facts": (
                "failed" if fact_rows is None
                else "available" if facts else "empty"
            ),
            "care_events": "available" if event_items else "empty",
            "appointments": "available" if appointment_items else "empty",
            "evidence_freshness_assessments": (
                "unavailable" if not freshness_schema_available
                else "available" if freshness_assessments else "empty"
            ),
            "medications_and_allergies": "unavailable",
            "documents": document_source_status,
            "community": "not_authorized",
        },
        "limitations": [
            "This is a source-linked preparation view, not a clinical assessment.",
            "Caregiver reports remain reports; no diagnosis, cause, medication instruction, or clinician finding is inferred.",
            "Missing or unavailable sources are not evidence that the information does not exist.",
        ],
    }


@router.get("/{record_id}/outcome-draft")
def get_appointment_outcome_draft(record_id: str, request: Request, db: Session = Depends(get_db)):
    """Organize persisted appointment notes into a person-scoped, read-only outcome."""
    person_id, _ = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")

    payload = row.payload if isinstance(row.payload, dict) else {}
    fields = {
        "caregiver_report": (("caregiverReport", "caregiver_report"), "What the caregiver reported"),
        "professional_information": (("clinicianFindings", "clinician_findings"), "What the caregiver recorded about the professional's words"),
        "decisions": (("decisions",), "Decisions recorded by the caregiver"),
        "instructions": (("instructions",), "Instructions recorded by the caregiver"),
        "unanswered_questions": (("unanswered", "unanswered_questions"), "Questions still unanswered"),
        "medication_information": (("medicationReconciliation", "medication_reconciliation"), "Medication information and questions"),
        "pending_results": (("pendingResults", "pending_results"), "Results still expected"),
        "family_summary": (("familySummary", "family_summary"), "Information for the family"),
    }
    sections = {key: [] for key in fields}
    missing_information = []
    for key, (field_names, label) in fields.items():
        value = next((payload.get(field) for field in field_names if isinstance(payload.get(field), str) and payload.get(field).strip()), None)
        if isinstance(value, str) and value.strip():
            sections[key].append({
                "label": label, "text": value.strip(),
                "source_type": "caregiver_recorded",
                "source_record_id": row.id, "source_version": row.version_number,
                "epistemic_status": "caregiver_reported",
                "independently_verified": False,
            })
        else:
            missing_information.append(label)

    follow_ups = []
    raw_follow_ups = payload.get("followUps", payload.get("follow_ups"))
    if isinstance(raw_follow_ups, list):
        for item in raw_follow_ups:
            if not isinstance(item, dict):
                continue
            action = item.get("action")
            if isinstance(action, str) and action.strip():
                follow_ups.append({
                    "text": action.strip(),
                    "owner": item.get("owner") if isinstance(item.get("owner"), str) and item.get("owner").strip() else None,
                    "due": item.get("due") if isinstance(item.get("due"), str) and item.get("due").strip() else None,
                    "status": item.get("status") if isinstance(item.get("status"), str) and item.get("status").strip() else "not_recorded",
                    "source_type": "caregiver_recorded",
                    "source_record_id": row.id,
                    "epistemic_status": "caregiver_reported",
                })
    sections["follow_ups"] = follow_ups
    has_information = any(sections[key] for key in fields) or bool(follow_ups)
    return {
        "record_id": row.id, "appointment_id": row.appointment_id,
        "record_version": row.version_number, "workspace_status": row.status,
        "attendance_status": payload.get("attendanceStatus") or "not_recorded",
        "actual_visit_date": payload.get("actualVisitDate") or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ready_for_review" if has_information else "no_outcome_information",
        "read_only": True, "updates_lcr": False,
        "sections": sections, "missing_information": missing_information,
        "limitations": [
            "This view organizes only information already recorded in this appointment workspace.",
            "Statements about a professional remain caregiver-reported unless an original professional record is separately reviewed.",
            "Generating or viewing this draft does not change the Living Care Record.",
        ],
    }



class AppointmentOutcomeReview(BaseModel):
    decision: str
    corrected_text: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    correction_context: Optional[str] = Field(default=None, max_length=4000)


def _appointment_proposal_json(proposal: AIOutputLCRProposal) -> dict:
    return {
        "id": proposal.id,
        "status": proposal.status,
        "proposal_type": proposal.proposal_type,
        "proposed_change": proposal.proposed_change_json or {},
        "candidate_index": proposal.candidate_index,
        "proposal_version": proposal.proposal_version,
        "created_at": _iso(proposal.created_at),
    }


def _appointment_proposal_id(record_id: str, revision: int, candidate_index: int, version: int) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"solenos:appointment-workspace:{record_id}:revision:{revision}:candidate:{candidate_index}:version:{version}",
    ))


def _appointment_source_text(revision_payload: dict, pointer: dict) -> Optional[str]:
    field_name = pointer.get("field_name")
    if not isinstance(field_name, str):
        return None
    value = revision_payload.get(field_name)
    if isinstance(value, str):
        return value.strip() or None
    return None


def _parse_appointment_event_time(value: Optional[str]):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@router.post("/{record_id}/outcome-proposals")
def create_appointment_outcome_proposals(record_id: str, request: Request, db: Session = Depends(get_db)):
    """Create idempotent, review-required LCR candidates from an immutable workspace revision."""
    person_id, caregiver_id = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).with_for_update().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    revision = db.query(AppointmentWorkspaceRevision).filter(
        AppointmentWorkspaceRevision.record_id == row.id,
        AppointmentWorkspaceRevision.person_id == person_id,
        AppointmentWorkspaceRevision.revision_number == row.version_number,
    ).first()
    if revision is None:
        raise HTTPException(status_code=409, detail="The current appointment revision is unavailable for review")

    payload = revision.payload if isinstance(revision.payload, dict) else {}
    field_candidates = (
        (("caregiverReport", "caregiver_report"), "caregiver_report", "Caregiver report"),
        (("clinicianFindings", "clinician_findings"), "professional_information", "Professional information recorded by caregiver"),
        (("decisions",), "decision", "Decision recorded by caregiver"),
        (("instructions",), "instruction", "Instruction recorded by caregiver"),
        (("medicationReconciliation", "medication_reconciliation"), "medication_information", "Medication information recorded by caregiver"),
        (("pendingResults", "pending_results"), "pending_result", "Pending result recorded by caregiver"),
    )
    candidates = []
    index = 0
    for field_names, assertion_field, label in field_candidates:
        field_name = next((name for name in field_names if isinstance(payload.get(name), str) and payload.get(name).strip()), None)
        if field_name is None:
            continue
        value = payload[field_name]
        candidates.append({
            "candidate_index": index,
            "field": assertion_field,
            "label": label,
            "source_pointer": {"field_name": field_name},
            "source_quote": value.strip(),
        })
        index += 1

    created = []
    for candidate in candidates:
        proposal_id = _appointment_proposal_id(row.id, row.version_number, candidate["candidate_index"], 1)
        proposal = db.query(AIOutputLCRProposal).filter(
            AIOutputLCRProposal.id == proposal_id,
            AIOutputLCRProposal.person_id == person_id,
            AIOutputLCRProposal.caregiver_id == caregiver_id,
        ).first()
        if proposal is None:
            proposal = AIOutputLCRProposal(
                id=proposal_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                proposal_type="appointment_workspace_outcome",
                proposed_change_json={
                    "asserted_value": candidate["source_quote"],
                    "source_quote": candidate["source_quote"],
                    "source_record_id": row.id,
                    "source_record_version": row.version_number,
                    "source_pointer": candidate["source_pointer"],
                    "field": candidate["field"],
                    "label": candidate["label"],
                    "event_time_text": payload.get("actualVisitDate") if isinstance(payload.get("actualVisitDate"), str) else payload.get("actual_visit_date") if isinstance(payload.get("actual_visit_date"), str) else None,
                    "epistemic_status": "caregiver_reported",
                    "independently_verified": False,
                },
                source_event_ids_json=[],
                source_evidence_ids_json=[],
                rationale="Extracted from a persisted appointment workspace revision. The exact caregiver report is admitted to the LCR as unreviewed; review controls its status, not whether the source report is retained.",
                uncertainty_json={"semantic_grounding": "exact_workspace_field", "clinical_verification": "not_performed"},
                source_conversation_id=None,
                source_message_id=None,
                candidate_index=candidate["candidate_index"],
                proposal_version=1,
                status="pending_authorization",
            )
            db.add(proposal)
            db.flush()

        # The caregiver's exact saved outcome enters the LCR immediately as
        # an unreviewed report, linked to the immutable appointment revision.
        # This is not a clinician-verified assertion.
        provisional = db.query(CareAssertion).filter(
            CareAssertion.source_proposal_id == proposal.id,
            CareAssertion.person_id == person_id,
        ).first()
        if provisional is None and proposal.status == "pending_authorization":
            change = proposal.proposed_change_json or {}
            quote = str(change.get("source_quote") or "").strip()
            if quote:
                provisional = CareAssertion(
                    id=str(uuid.uuid4()), person_id=person_id,
                    subject_type="person", subject_id=person_id,
                    field=str(change.get("field") or "appointment_observation"),
                    assertion_kind="time_bound_observation",
                    asserted_value=str(change.get("asserted_value") or quote),
                    source_label="Caregiver-reported appointment outcome (unreviewed)",
                    source_text=quote,
                    source_proposal_id=proposal.id,
                    source_conversation_id=None, source_message_id=None,
                    event_time_text=change.get("event_time_text"),
                    evidence_ids=[],
                    assertion_status="caregiver_reported_unreviewed",
                    observed_at=None,
                    created_by_caregiver_id=caregiver_id,
                    updated_by_caregiver_id=caregiver_id,
                )
                db.add(provisional)
                db.flush()
        created.append(_appointment_proposal_json(proposal))

    db.commit()
    return {
        "record_id": row.id,
        "source_record_version": row.version_number,
        "items": created,
        "count": len(created),
        "written_to_lcr": False,
        "review_required": True,
        "limitations": [
            "Candidates quote only persisted caregiver-entered workspace fields.",
            "Professional statements remain caregiver-reported unless an original professional source is separately reviewed.",
            "Acceptance creates a caregiver-reviewed assertion, not a clinically verified fact.",
            "Follow-up actions and unanswered questions are not converted into care assertions.",
        ],
    }


@router.get("/{record_id}/outcome-proposals")
def list_appointment_outcome_proposals(record_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord.id).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    scoped_proposals = db.query(AIOutputLCRProposal).filter(
        AIOutputLCRProposal.person_id == person_id,
        AIOutputLCRProposal.caregiver_id == caregiver_id,
        AIOutputLCRProposal.proposal_type == "appointment_workspace_outcome",
    ).order_by(AIOutputLCRProposal.created_at.desc()).limit(500).all()
    proposals = [
        item for item in scoped_proposals
        if (item.proposed_change_json or {}).get("source_record_id") == record_id
    ][:100]
    proposals.sort(key=lambda item: (item.candidate_index, item.proposal_version))
    return {"items": [_appointment_proposal_json(item) for item in proposals], "append_only_decisions": True}


@router.post("/{record_id}/outcome-proposals/{proposal_id}/review")
def review_appointment_outcome_proposal(
    record_id: str,
    proposal_id: str,
    payload: AppointmentOutcomeReview,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    decision = (payload.decision or "").strip().lower()
    if decision not in {"accept", "correct", "reject"}:
        raise HTTPException(status_code=422, detail="Decision must be accept, correct, or reject")

    record = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    proposal = db.query(AIOutputLCRProposal).filter(
        AIOutputLCRProposal.id == proposal_id,
        AIOutputLCRProposal.person_id == person_id,
        AIOutputLCRProposal.caregiver_id == caregiver_id,
        AIOutputLCRProposal.proposal_type == "appointment_workspace_outcome",
    ).with_for_update().first()
    if proposal is None or (proposal.proposed_change_json or {}).get("source_record_id") != record_id:
        raise HTTPException(status_code=404, detail="Appointment outcome proposal not found")

    original = dict(proposal.proposed_change_json or {})
    revision_number = original.get("source_record_version")
    revision = db.query(AppointmentWorkspaceRevision).filter(
        AppointmentWorkspaceRevision.record_id == record_id,
        AppointmentWorkspaceRevision.person_id == person_id,
        AppointmentWorkspaceRevision.revision_number == revision_number,
    ).first()
    if revision is None:
        raise HTTPException(status_code=409, detail="The original appointment workspace revision is unavailable")
    source_quote = original.get("source_quote")
    actual_source = _appointment_source_text(
        revision.payload if isinstance(revision.payload, dict) else {},
        original.get("source_pointer") if isinstance(original.get("source_pointer"), dict) else {},
    )
    if not isinstance(source_quote, str) or not actual_source or source_quote != actual_source:
        raise HTTPException(status_code=409, detail="The proposal no longer matches its persisted appointment source revision")

    terminal = {"accept": "accepted", "correct": "corrected", "reject": "rejected"}[decision]
    if proposal.status != "pending_authorization":
        if proposal.status != terminal:
            raise HTTPException(status_code=409, detail="This appointment outcome proposal already has a different review decision")
        existing_assertion = db.query(CareAssertion).filter(
            CareAssertion.source_proposal_id == proposal.id,
            CareAssertion.person_id == person_id,
        ).first()
        corrected_proposal = None
        if decision == "correct":
            next_id = _appointment_proposal_id(record_id, int(revision_number), int(proposal.candidate_index), int(proposal.proposal_version) + 1)
            corrected_proposal = db.query(AIOutputLCRProposal).filter(AIOutputLCRProposal.id == next_id).first()
            if corrected_proposal is None:
                raise HTTPException(status_code=409, detail="The saved correction proposal is unavailable")
            corrected_change = corrected_proposal.proposed_change_json or {}
            if (
                corrected_change.get("asserted_value") != (payload.corrected_text or "").strip()
                or corrected_change.get("correction_context") != (payload.correction_context or "").strip()
            ):
                raise HTTPException(status_code=409, detail="This proposal already has a different correction decision")
        return {
            "proposal": _appointment_proposal_json(proposal),
            "corrected_proposal": _appointment_proposal_json(corrected_proposal) if corrected_proposal else None,
            "decision": decision,
            "assertion_id": existing_assertion.id if existing_assertion else None,
            "written_to_lcr": bool(existing_assertion),
            "idempotent_replay": True,
        }

    now = datetime.now(timezone.utc)
    assertion_id = None
    corrected_proposal = None
    if decision == "reject":
        proposal.status = "rejected"
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="rejected",
            rationale="Caregiver rejected this appointment outcome candidate; no LCR assertion was created.",
            created_at=now,
        ))
    elif decision == "correct":
        corrected_text = (payload.corrected_text or "").strip()
        correction_context = (payload.correction_context or "").strip()
        if not corrected_text or not correction_context or corrected_text not in correction_context:
            raise HTTPException(status_code=422, detail="Correction text must be explicitly included in the caregiver's correction context")
        next_version = int(proposal.proposal_version or 1) + 1
        next_id = _appointment_proposal_id(record_id, int(revision_number), int(proposal.candidate_index), next_version)
        corrected_proposal = db.query(AIOutputLCRProposal).filter(
            AIOutputLCRProposal.id == next_id,
            AIOutputLCRProposal.person_id == person_id,
            AIOutputLCRProposal.caregiver_id == caregiver_id,
        ).first()
        if corrected_proposal is None:
            corrected_proposal = AIOutputLCRProposal(
                id=next_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                proposal_type="appointment_workspace_outcome",
                proposed_change_json={
                    **original,
                    "asserted_value": corrected_text,
                    "correction_context": correction_context,
                    "supersedes_proposal_id": proposal.id,
                    "epistemic_status": "caregiver_reported",
                    "independently_verified": False,
                },
                source_event_ids_json=list(proposal.source_event_ids_json or []),
                source_evidence_ids_json=list(proposal.source_evidence_ids_json or []),
                rationale="Caregiver correction preserved as a separate pending proposal; the original candidate and source revision remain unchanged.",
                uncertainty_json={**(proposal.uncertainty_json or {}), "correction_grounding": "explicit_caregiver_context", "clinical_verification": "not_performed"},
                source_conversation_id=None,
                source_message_id=None,
                candidate_index=proposal.candidate_index,
                proposal_version=next_version,
                status="pending_authorization",
            )
            db.add(corrected_proposal)
        proposal.status = "corrected"
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="corrected",
            rationale=corrected_text,
            clarification_request=correction_context,
            created_at=now,
        ))
    else:
        accepted_value = (original.get("asserted_value") or "").strip()
        correction_context = (original.get("correction_context") or "").strip()
        if not accepted_value or (
            accepted_value != source_quote
            and (not correction_context or accepted_value not in correction_context)
        ):
            raise HTTPException(status_code=422, detail="Accepted text is not grounded in the persisted appointment source or caregiver correction context")
        assertion = db.query(CareAssertion).filter(
            CareAssertion.source_proposal_id == proposal.id,
            CareAssertion.person_id == person_id,
        ).first()
        if assertion is None:
            assertion = CareAssertion(
                id=str(uuid.uuid4()),
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field=str(original.get("field") or "appointment_observation"),
                assertion_kind="time_bound_observation",
                asserted_value=accepted_value,
                source_label="Caregiver-reviewed appointment workspace",
                source_text=source_quote + (("\nCaregiver correction context: " + correction_context) if correction_context else ""),
                source_proposal_id=proposal.id,
                source_conversation_id=None,
                source_message_id=None,
                event_time_text=original.get("event_time_text"),
                evidence_ids=[],
                assertion_status="caregiver_reviewed",
                observed_at=_parse_appointment_event_time(original.get("event_time_text")),
                created_by_caregiver_id=caregiver_id,
                updated_by_caregiver_id=caregiver_id,
            )
            db.add(assertion)
            db.flush()
        assertion_id = assertion.id
        proposal.status = "accepted"
        db.add(AIOutputLCRDecision(
            id=str(uuid.uuid4()), proposal_id=proposal.id, person_id=person_id,
            reviewer_caregiver_id=caregiver_id, decision="accepted",
            rationale="Caregiver explicitly accepted a source-attributed appointment report. This is not independent clinical verification.",
            resulting_lcr_revision_id=assertion_id,
            created_at=now,
        ))

    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    if corrected_proposal is not None:
        db.refresh(corrected_proposal)
    return {
        "proposal": _appointment_proposal_json(proposal),
        "corrected_proposal": _appointment_proposal_json(corrected_proposal) if corrected_proposal else None,
        "decision": decision,
        "assertion_id": assertion_id,
        "written_to_lcr": bool(assertion_id),
        "idempotent_replay": False,
    }


@router.get("/{record_id}")
def get_appointment_workspace(record_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    return _record_out(row)


@router.patch("/{record_id}")
def update_appointment_workspace(record_id: str, payload: AppointmentWorkspaceUpdate, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scope(request, db)
    if len(json.dumps(payload.payload, ensure_ascii=False, default=str)) > 100_000:
        raise HTTPException(status_code=413, detail="Appointment workspace payload exceeds 100 KB")
    row = db.query(AppointmentWorkspaceRecord).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).with_for_update().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    update_fingerprint = _request_fingerprint({
        "expected_version": payload.expected_version,
        "status": payload.status,
        "payload": payload.payload,
        "change_reason": payload.change_reason,
    })
    if payload.idempotency_key:
        prior_revision = db.query(AppointmentWorkspaceRevision).filter(
            AppointmentWorkspaceRevision.record_id == row.id,
            AppointmentWorkspaceRevision.changed_by_caregiver_id == caregiver_id,
            AppointmentWorkspaceRevision.idempotency_key == payload.idempotency_key,
        ).first()
        if prior_revision is not None:
            if prior_revision.idempotency_request_sha256 == update_fingerprint:
                return _record_out(row)
            raise HTTPException(status_code=409, detail="This save key was already used for different appointment information")

    if row.version_number != payload.expected_version:
        raise HTTPException(status_code=409, detail={
            "code": "appointment_workspace_version_conflict",
            "expected_version": payload.expected_version,
            "current_version": row.version_number,
            "message": "This workspace changed elsewhere. Reload before saving to avoid overwriting newer notes.",
        })
    if payload.status is not None and payload.status not in _ALLOWED_STATUS_TRANSITIONS.get(row.status, set()):
        raise HTTPException(status_code=409, detail={
            "code": "invalid_appointment_workspace_transition",
            "current_status": row.status,
            "requested_status": payload.status,
            "allowed_statuses": sorted(_ALLOWED_STATUS_TRANSITIONS.get(row.status, set())),
        })
    next_version = row.version_number + 1
    row.payload = payload.payload
    if payload.status is not None:
        row.status = payload.status
    row.version_number = next_version
    row.updated_by_caregiver_id = caregiver_id
    row.last_revision_reason = payload.change_reason
    db.add(AppointmentWorkspaceRevision(
        id=str(uuid.uuid4()), record_id=row.id, person_id=person_id,
        revision_number=next_version, payload=payload.payload,
        change_reason=payload.change_reason, changed_by_caregiver_id=caregiver_id,
        idempotency_key=payload.idempotency_key,
        idempotency_request_sha256=update_fingerprint if payload.idempotency_key else None,
    ))
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        if payload.idempotency_key:
            prior_revision = db.query(AppointmentWorkspaceRevision).filter(
                AppointmentWorkspaceRevision.record_id == record_id,
                AppointmentWorkspaceRevision.changed_by_caregiver_id == caregiver_id,
                AppointmentWorkspaceRevision.idempotency_key == payload.idempotency_key,
            ).first()
            if prior_revision is not None and prior_revision.idempotency_request_sha256 == update_fingerprint:
                current = db.query(AppointmentWorkspaceRecord).filter(
                    AppointmentWorkspaceRecord.id == record_id,
                    AppointmentWorkspaceRecord.person_id == person_id,
                ).first()
                if current is not None:
                    return _record_out(current)
            if prior_revision is not None:
                raise HTTPException(status_code=409, detail="This save key was already used for different appointment information") from exc
        raise HTTPException(status_code=503, detail="Appointment workspace update could not be persisted") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Appointment workspace update could not be persisted") from exc
    return _record_out(row)


@router.get("/{record_id}/revisions")
def list_appointment_workspace_revisions(record_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scope(request, db)
    row = db.query(AppointmentWorkspaceRecord.id).filter(
        AppointmentWorkspaceRecord.id == record_id,
        AppointmentWorkspaceRecord.person_id == person_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment workspace not found")
    revisions = db.query(AppointmentWorkspaceRevision).filter(
        AppointmentWorkspaceRevision.record_id == record_id,
        AppointmentWorkspaceRevision.person_id == person_id,
    ).order_by(AppointmentWorkspaceRevision.revision_number.asc()).all()
    return {"items": [{
        "id": item.id, "record_id": item.record_id, "revision_number": item.revision_number,
        "payload": item.payload, "change_reason": item.change_reason,
        "changed_at": item.changed_at, "changed_by_caregiver_id": item.changed_by_caregiver_id,
    } for item in revisions]}
