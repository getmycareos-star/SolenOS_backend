"""Read-only, authorization-scoped evidence for caregiver transparency questions.

This service deliberately returns metadata and operation state, not care-source
text, document bytes, credentials, or unrestricted audit metadata. It reuses the
existing PostgreSQL records; it does not create a parallel LCR or audit ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import func, inspect, or_, text
from sqlalchemy.orm import Session

from app.models.anonymous_session import AnonymousSession
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.care import CareEvent, Caregiver, Correction, Evidence
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.capture_analysis import CaptureAnalysis, DocumentReviewEvent
from app.models.care_continuity import CareAssertion, CareStateRevision
from app.models.collaboration import EvidenceUpload
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_export import PrivacyExportArtifact
from app.models.privacy_request import DataSubjectRequest


EVIDENCE_SCHEMA_VERSION = "1.0"
MAX_RECENT_ITEMS = 25


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _table_available(db: Session, table_name: str) -> bool:
    return inspect(db.get_bind()).has_table(table_name)


def _is_expired(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= now


_REVISION_VALUE_FIELDS = frozenset({
    "field", "asserted_value", "assertion_status", "epistemic_state",
    "observed_at", "recorded_at", "occurred_at", "title", "description",
    "status", "evidence_status", "epistemic_status", "source_label",
    "event_time_text", "change_kind", "superseded_by_id", "withdrawn_at",
})


def _revision_state_summary(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"state_available": False}
    if not isinstance(value, dict):
        return {"state_available": False}
    return {
        key: (str(item)[:500] if isinstance(item, str) else item)
        for key, item in value.items()
        if key in _REVISION_VALUE_FIELDS and item is not None
    }


def _revision_changed_fields(before_raw: str | None, after_raw: str | None) -> list[str]:
    def decode(raw):
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    before, after = decode(before_raw), decode(after_raw)
    return sorted(
        key for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    )


_TRANSPARENCY_VALUE_KEYS = frozenset({
    "field", "value", "asserted_value", "assertion_kind", "assertion_status",
    "epistemic_state", "epistemic_status", "observed_at", "recorded_at",
    "occurred_at", "event_time_text", "title", "description", "status",
    "source_label", "source_quote", "source_span_start", "source_span_end",
    "observation", "subject", "claim", "reporter", "reported_by",
    "review_status", "decision", "reason", "rationale", "change_kind",
    "event_type", "target_type", "target_id", "source_event_id",
    "source_evidence_id", "evidence_ids", "open_questions",
    "possible_safety_flags", "observations", "proposed_assertions",
    "proposed_change", "operation", "candidate_index",
})


def _bounded_projection(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(value, str):
        return value[:600]
    if isinstance(value, dict):
        return {
            str(key): _bounded_projection(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
            if str(key) in _TRANSPARENCY_VALUE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_projection(item, depth=depth + 1) for item in list(value)[:12]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:300]


def _extraction_candidate_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {"candidate_summary_available": False}
    if not isinstance(value, dict):
        return {"candidate_summary_available": False}
    return {
        "candidate_summary_available": True,
        "observations": _bounded_projection(value.get("observations") or []),
        "open_questions": _bounded_projection(value.get("open_questions") or []),
        "possible_safety_flags": _bounded_projection(value.get("possible_safety_flags") or []),
    }


def _count_by(db: Session, model, column, person_id: str) -> dict[str, int]:
    rows = (
        db.query(column, func.count(model.id))
        .filter(model.person_id == person_id)
        .group_by(column)
        .all()
    )
    return {str(key or "unknown"): int(count) for key, count in rows}


def build_transparency_evidence(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    session_id: str,
    grant: CaregiverAccessGrant,
) -> dict[str, Any]:
    """Build a minimal evidence bundle for the already-authorized session scope.

    Authorization must be checked by the calling API before this function runs.
    Every section declares its own completeness; bounded lists never imply that
    the complete underlying history was returned.
    """
    now = datetime.now(timezone.utc)
    can_view_lcr = grant.access_scope == "full_record" and (
        bool(grant.can_manage_members) or "view_lcr" in set(grant.permissions or [])
    )
    can_view_documents = (
        grant.access_scope == "documents_only" and "view_documents" in set(grant.permissions or [])
    ) or (
        grant.access_scope == "full_record"
        and (bool(grant.can_manage_members) or "view_documents" in set(grant.permissions or []))
    )
    can_manage_members = bool(grant.can_manage_members)

    caregiver = db.query(Caregiver).filter(
        Caregiver.id == caregiver_id,
        Caregiver.person_id == person_id,
    ).first()
    items: list[dict[str, Any]] = [{
        "kind": "authenticated_scope",
        "source_service": "anonymous_session_and_caregiver_access_grants",
        "scope": {"person_id": person_id, "caregiver_id": caregiver_id},
        "observed_at": now.isoformat(),
        "status": "available" if caregiver else "partial",
        "reference_id": str(grant.id),
        "completeness": "current_session_scope_only",
        "details": {
            "caregiver_name": caregiver.name if caregiver else None,
            "role": grant.role,
            "access_scope": grant.access_scope,
            "permissions": sorted(set(grant.permissions or [])),
            "can_manage_members": can_manage_members,
            "granted_at": _iso(grant.granted_at),
            "grant_status": grant.status,
        },
    }]

    active_session = db.query(AnonymousSession).filter(
        AnonymousSession.id == session_id,
        AnonymousSession.person_id == person_id,
        AnonymousSession.caregiver_id == caregiver_id,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > now,
    ).first()
    items.append({
        "kind": "active_session",
        "source_service": "anonymous_sessions",
        "scope": {"person_id": person_id, "caregiver_id": caregiver_id},
        "observed_at": now.isoformat(),
        "status": "available" if active_session else "unavailable",
        "reference_id": session_id,
        "completeness": "current_session_record",
        "details": {
            "session_status": "active" if active_session else "not_verified",
            "created_at": _iso(active_session.created_at) if active_session else None,
            "expires_at": _iso(active_session.expires_at) if active_session else None,
        },
    })

    coverage: dict[str, str] = {
        "authenticated_scope": "complete" if caregiver else "partial",
        "active_session": "complete" if active_session else "unavailable",
        "sharing_members": "not_authorized",
        "documents": "not_authorized",
        "document_review": "not_authorized",
        "lcr": "not_authorized",
        "audit_activity": "not_authorized",
        "privacy_requests": "not_checked",
        "privacy_policy": "unavailable",
        "integrations": "unavailable",
    }

    if can_manage_members and _table_available(db, "caregiver_access_grants"):
        rows = (
            db.query(CaregiverAccessGrant, Caregiver.name)
            .join(Caregiver, Caregiver.id == CaregiverAccessGrant.caregiver_id)
            .filter(
                CaregiverAccessGrant.person_id == person_id,
                CaregiverAccessGrant.status == "active",
                CaregiverAccessGrant.revoked_at.is_(None),
            )
            .order_by(CaregiverAccessGrant.granted_at.asc())
            .all()
        )
        items.append({
            "kind": "active_caregiver_access",
            "source_service": "caregiver_access_grants",
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "available",
            "completeness": "complete_active_grants",
            "details": {
                "members": [{
                    "caregiver_name": name,
                    "role": row.role,
                    "access_scope": row.access_scope,
                    "permissions": sorted(set(row.permissions or [])),
                    "granted_at": _iso(row.granted_at),
                } for row, name in rows],
                "active_member_count": len(rows),
            },
        })
        coverage["sharing_members"] = "complete_active_grants"
    elif can_view_lcr:
        coverage["sharing_members"] = "not_authorized_for_member_roster"

    if can_view_documents:
        uploads_available = _table_available(db, "evidence_uploads")
        document_filter = or_(
            Evidence.type == "document",
            Evidence.source_type == "document",
            Evidence.evidence_kind.in_(("uploaded_document", "document_extraction")),
        )
        evidence_count = (
            db.query(func.count(Evidence.id))
            .filter(Evidence.person_id == person_id, document_filter)
            .scalar() or 0
        )
        if uploads_available:
            # Select upload metadata only. Loading EvidenceUpload as an ORM
            # entity also loads its binary content column, which this read-only
            # transparency response must never need.
            document_rows = (
                db.query(
                    Evidence,
                    EvidenceUpload.filename.label("upload_filename"),
                    EvidenceUpload.mime_type.label("upload_mime_type"),
                    EvidenceUpload.size_bytes.label("upload_size_bytes"),
                    EvidenceUpload.sha256.label("upload_sha256"),
                    EvidenceUpload.storage_backend.label("upload_storage_backend"),
                    EvidenceUpload.storage_key.label("upload_storage_key"),
                    EvidenceUpload.created_at.label("upload_created_at"),
                )
                .outerjoin(EvidenceUpload, EvidenceUpload.evidence_id == Evidence.id)
                .filter(Evidence.person_id == person_id, document_filter)
                .order_by(Evidence.uploaded_at.desc(), Evidence.id.desc())
                .limit(MAX_RECENT_ITEMS + 1)
                .all()
            )
        else:
            document_rows = [
                (row, None, None, None, None, None, None, None) for row in (
                    db.query(Evidence)
                    .filter(
                        Evidence.person_id == person_id,
                        or_(Evidence.type == "document", Evidence.source_type == "document"),
                    )
                    .order_by(Evidence.uploaded_at.desc(), Evidence.id.desc())
                    .limit(MAX_RECENT_ITEMS + 1)
                    .all()
                )
            ]
        has_more = len(document_rows) > MAX_RECENT_ITEMS
        missing_upload_metadata = (
            sum(1 for row in document_rows[:MAX_RECENT_ITEMS] if row[1] is None)
            if uploads_available else None
        )
        recent_documents = []
        uploader_ids = {
            str(row[0].uploaded_by_caregiver_id)
            for row in document_rows[:MAX_RECENT_ITEMS]
            if row[0].uploaded_by_caregiver_id
        }
        uploader_names = {
            str(row.id): row.name for row in db.query(Caregiver).filter(
                Caregiver.person_id == person_id,
                Caregiver.id.in_(uploader_ids),
            ).all()
        } if uploader_ids else {}
        for (
            evidence, filename, mime_type, size_bytes, sha256,
            storage_backend, storage_key, upload_created_at,
        ) in document_rows[:MAX_RECENT_ITEMS]:
            recent_documents.append({
                "evidence_id": str(evidence.id),
                "evidence_type": evidence.type,
                "evidence_kind": evidence.evidence_kind,
                "evidence_status": evidence.evidence_status,
                "epistemic_status": evidence.epistemic_status,
                "uploaded_at": _iso(evidence.uploaded_at),
                "uploaded_by": uploader_names.get(str(evidence.uploaded_by_caregiver_id), "Another caregiver"),
                "filename": filename,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "binary_storage_backend": storage_backend,
                "storage_reference_available": bool(storage_key),
                "upload_record_created_at": _iso(upload_created_at),
                "upload_record_available": bool(filename or sha256 or size_bytes is not None),
            })
        extraction_rows = (
            db.query(CaptureAnalysis.extraction_status, func.count(CaptureAnalysis.id))
            .filter(
                CaptureAnalysis.person_id == person_id,
                CaptureAnalysis.source_evidence_id.is_not(None),
            )
            .group_by(CaptureAnalysis.extraction_status)
            .all()
        )
        extraction_counts = {str(status or "unknown"): int(count) for status, count in extraction_rows}
        extraction_query = db.query(CaptureAnalysis).filter(
            CaptureAnalysis.person_id == person_id,
            CaptureAnalysis.source_evidence_id.is_not(None),
        )
        extraction_count = extraction_query.count()
        recent_extractions = (
            extraction_query.order_by(CaptureAnalysis.created_at.desc(), CaptureAnalysis.id.desc())
            .limit(MAX_RECENT_ITEMS + 1)
            .all()
        )
        has_more_extractions = len(recent_extractions) > MAX_RECENT_ITEMS
        items.append({
            "kind": "documents_and_extraction",
            "source_service": "evidence_uploads_and_capture_analyses",
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "available" if uploads_available and not missing_upload_metadata and not has_more_extractions else "partial",
            "completeness": "partial" if not uploads_available or missing_upload_metadata or has_more_extractions else ("recent_bounded_list" if has_more else "metadata_complete"),
            "details": {
                "evidence_count": int(evidence_count),
                "recent_documents": recent_documents,
                "recent_documents_limit": MAX_RECENT_ITEMS,
                "has_more_recent_documents": has_more,
                "extraction_status_counts": extraction_counts,
                "extraction_record_count": int(extraction_count),
                "recent_extractions": [{
                    "analysis_id": str(row.id),
                    "source_evidence_id": str(row.source_evidence_id),
                    "extraction_status": row.extraction_status,
                    "candidate_summary": _extraction_candidate_summary(row.candidates),
                    "created_at": _iso(row.created_at),
                    "updated_at": _iso(row.updated_at),
                } for row in recent_extractions[:MAX_RECENT_ITEMS]],
                "recent_extractions_limit": MAX_RECENT_ITEMS,
                "has_more_extractions": has_more_extractions,
                "upload_binary_metadata_available": uploads_available,
                "records_missing_upload_metadata_in_returned_window": missing_upload_metadata,
            },
        })
        coverage["documents"] = "partial" if has_more or has_more_extractions or not uploads_available or missing_upload_metadata else "complete_metadata"

        if _table_available(db, "document_review_events"):
            review_query = db.query(DocumentReviewEvent).filter(
                DocumentReviewEvent.person_id == person_id
            )
            review_count = review_query.count()
            review_rows = (
                review_query.order_by(DocumentReviewEvent.created_at.desc(), DocumentReviewEvent.id.desc())
                .limit(MAX_RECENT_ITEMS + 1)
                .all()
            )
            has_more_reviews = len(review_rows) > MAX_RECENT_ITEMS
            reviewer_ids = {str(row.caregiver_id) for row in review_rows[:MAX_RECENT_ITEMS]}
            reviewer_names = {
                str(row.id): row.name for row in db.query(Caregiver).filter(
                    Caregiver.person_id == person_id,
                    Caregiver.id.in_(reviewer_ids),
                ).all()
            } if reviewer_ids else {}
            items.append({
                "kind": "document_review_history",
                "source_service": "document_review_events",
                "scope": {"person_id": person_id},
                "observed_at": now.isoformat(),
                "status": "available",
                "completeness": "recent_bounded_append_only_review_events",
                "details": {
                    "review_event_count": int(review_count),
                    "recent_reviews": [{
                        "review_event_id": str(row.id),
                        "analysis_id": str(row.analysis_id),
                        "source_evidence_id": str(row.source_evidence_id),
                        "candidate_index": row.candidate_index,
                        "decision_version": row.decision_version,
                        "decision": row.decision,
                        "reviewer_name": reviewer_names.get(str(row.caregiver_id), "Another caregiver"),
                        "resulting_assertion_id": str(row.resulting_assertion_id) if row.resulting_assertion_id else None,
                        "created_at": _iso(row.created_at),
                    } for row in review_rows[:MAX_RECENT_ITEMS]],
                    "recent_reviews_limit": MAX_RECENT_ITEMS,
                    "has_more_reviews": has_more_reviews,
                },
            })
            coverage["document_review"] = "partial" if has_more_reviews else "complete_recent_window"
        else:
            items.append({
                "kind": "document_review_history",
                "source_service": "document_review_events",
                "scope": {"person_id": person_id},
                "observed_at": now.isoformat(),
                "status": "unavailable",
                "completeness": "unavailable",
                "reason": "The document review history table is not available in the connected database schema.",
            })
            coverage["document_review"] = "unavailable"

    if can_view_lcr:
        assertion_status_counts = _count_by(db, CareAssertion, CareAssertion.assertion_status, person_id)
        epistemic_state_counts = _count_by(db, CareAssertion, CareAssertion.epistemic_state, person_id)
        event_count = db.query(func.count(CareEvent.id)).filter(CareEvent.person_id == person_id).scalar() or 0
        correction_status_counts = _count_by(db, Correction, Correction.processing_status, person_id)
        correction_query = db.query(Correction).filter(Correction.person_id == person_id)
        correction_count = correction_query.count()
        recent_corrections = (
            correction_query.order_by(Correction.created_at.desc(), Correction.id.desc())
            .limit(MAX_RECENT_ITEMS + 1)
            .all()
        )
        has_more_corrections = len(recent_corrections) > MAX_RECENT_ITEMS
        correction_actor_ids = {str(row.caregiver_id) for row in recent_corrections[:MAX_RECENT_ITEMS]}
        correction_actor_names = {
            str(row.id): row.name for row in db.query(Caregiver).filter(
                Caregiver.person_id == person_id,
                Caregiver.id.in_(correction_actor_ids),
            ).all()
        } if correction_actor_ids else {}

        revision_history_available = _table_available(db, "care_state_revisions")
        revision_rows = []
        has_more_revisions = False
        if revision_history_available:
            revision_rows = (
                db.query(CareStateRevision)
                .filter(CareStateRevision.person_id == person_id)
                .order_by(CareStateRevision.changed_at.desc(), CareStateRevision.id.desc())
                .limit(MAX_RECENT_ITEMS + 1)
                .all()
            )
            has_more_revisions = len(revision_rows) > MAX_RECENT_ITEMS
        revision_actor_ids = {
            str(row.changed_by_caregiver_id) for row in revision_rows[:MAX_RECENT_ITEMS]
            if row.changed_by_caregiver_id
        }
        revision_actor_names = {
            str(row.id): row.name for row in db.query(Caregiver).filter(
                Caregiver.person_id == person_id,
                Caregiver.id.in_(revision_actor_ids),
            ).all()
        } if revision_actor_ids else {}

        proposal_history_available = _table_available(db, "ai_output_lcr_proposals")
        decision_history_available = _table_available(db, "ai_output_lcr_decisions")
        proposal_rows = []
        proposal_count = None
        has_more_proposals = False
        if proposal_history_available:
            proposal_query = db.query(AIOutputLCRProposal).filter(
                AIOutputLCRProposal.person_id == person_id
            )
            if not can_manage_members:
                proposal_query = proposal_query.filter(AIOutputLCRProposal.caregiver_id == caregiver_id)
            proposal_count = proposal_query.count()
            proposal_rows = (
                proposal_query.order_by(AIOutputLCRProposal.created_at.desc(), AIOutputLCRProposal.id.desc())
                .limit(MAX_RECENT_ITEMS + 1)
                .all()
            )
            has_more_proposals = len(proposal_rows) > MAX_RECENT_ITEMS
        proposal_ids = [str(row.id) for row in proposal_rows[:MAX_RECENT_ITEMS]]
        decision_count = None
        decision_rows = []
        has_more_decisions = False
        if decision_history_available and proposal_ids:
            decision_query = db.query(AIOutputLCRDecision).filter(
                AIOutputLCRDecision.person_id == person_id,
                AIOutputLCRDecision.proposal_id.in_(proposal_ids),
            )
            decision_count = decision_query.count()
            decision_rows = (
                decision_query.order_by(AIOutputLCRDecision.created_at.desc(), AIOutputLCRDecision.id.desc())
                .limit(MAX_RECENT_ITEMS * 2 + 1)
                .all()
            )
            has_more_decisions = len(decision_rows) > MAX_RECENT_ITEMS * 2
        decision_actor_ids = {
            str(row.reviewer_caregiver_id) for row in decision_rows if row.reviewer_caregiver_id
        }
        decision_actor_names = {
            str(row.id): row.name for row in db.query(Caregiver).filter(
                Caregiver.person_id == person_id,
                Caregiver.id.in_(decision_actor_ids),
            ).all()
        } if decision_actor_ids else {}
        decisions_by_proposal: dict[str, list[dict[str, Any]]] = {}
        for row in decision_rows:
            decisions_by_proposal.setdefault(str(row.proposal_id), []).append({
                "decision_id": str(row.id),
                "decision": row.decision,
                "reviewer_name": decision_actor_names.get(str(row.reviewer_caregiver_id), "Another caregiver"),
                "resulting_lcr_revision_id": row.resulting_lcr_revision_id,
                "created_at": _iso(row.created_at),
            })

        assertion_rows = (
            db.query(CareAssertion)
            .filter(CareAssertion.person_id == person_id)
            .order_by(CareAssertion.recorded_at.desc(), CareAssertion.id.desc())
            .limit(MAX_RECENT_ITEMS + 1)
            .all()
        )
        has_more_assertions = len(assertion_rows) > MAX_RECENT_ITEMS
        items.append({
            "kind": "living_care_record_state",
            "source_service": "care_assertions_care_events_corrections",
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "partial" if (
                not revision_history_available or not proposal_history_available or not decision_history_available
            ) else "available",
            "completeness": "partial" if (
                not revision_history_available or not proposal_history_available or not decision_history_available
            ) else "aggregate_counts_and_recent_bounded_assertions",
            "details": {
                "care_event_count": int(event_count),
                "assertion_status_counts": assertion_status_counts,
                "epistemic_state_counts": epistemic_state_counts,
                "correction_processing_status_counts": correction_status_counts,
                "correction_count": int(correction_count),
                "recent_corrections": [{
                    "correction_id": str(row.id),
                    "target_type": row.target_type,
                    "target_id": str(row.target_id),
                    "processing_status": row.processing_status,
                    "processing_attempts": row.processing_attempts,
                    "original_text": row.original_text,
                    "corrected_text": row.corrected_text,
                    "reason": row.reason,
                    "actor_name": correction_actor_names.get(str(row.caregiver_id), "Another caregiver"),
                    "created_at": _iso(row.created_at),
                    "processed_at": _iso(row.processed_at),
                } for row in recent_corrections[:MAX_RECENT_ITEMS]],
                "recent_corrections_limit": MAX_RECENT_ITEMS,
                "has_more_corrections": has_more_corrections,
                "recent_revisions": [{
                    "revision_id": str(row.id),
                    "target_type": row.target_type,
                    "target_id": str(row.target_id),
                    "revision_number": row.revision_number,
                    "change_kind": row.change_kind,
                    "changed_fields": _revision_changed_fields(row.before_state, row.after_state),
                    "before_state": _revision_state_summary(row.before_state),
                    "after_state": _revision_state_summary(row.after_state),
                    "changed_by": revision_actor_names.get(str(row.changed_by_caregiver_id), "SolenOS system" if not row.changed_by_caregiver_id else "Another caregiver"),
                    "changed_at": _iso(row.changed_at),
                    "evidence_ids": [str(value) for value in (row.evidence_ids or [])],
                } for row in revision_rows[:MAX_RECENT_ITEMS]],
                "has_more_revisions": has_more_revisions,
                "revision_history_status": "available" if revision_history_available else "unavailable",
                "proposal_count": int(proposal_count) if proposal_count is not None else None,
                "proposal_history_status": "available" if proposal_history_available else "unavailable",
                "decision_count": int(decision_count) if decision_count is not None else None,
                "decision_history_status": "available" if decision_history_available else "unavailable",
                "has_more_decisions": has_more_decisions if decision_history_available else None,
                "recent_proposals": [{
                    "proposal_id": str(row.id),
                    "proposal_type": row.proposal_type,
                    "status": row.status,
                    "proposed_change": _bounded_projection(row.proposed_change_json),
                    "created_at": _iso(row.created_at),
                    "expires_at": _iso(row.expires_at),
                    "source_event_ids": [str(value) for value in (row.source_event_ids_json or [])],
                    "source_evidence_ids": [str(value) for value in (row.source_evidence_ids_json or [])],
                    "decisions": decisions_by_proposal.get(str(row.id), []),
                } for row in proposal_rows[:MAX_RECENT_ITEMS]],
                "recent_proposals_limit": MAX_RECENT_ITEMS,
                "has_more_proposals": has_more_proposals,
                "recent_assertions": [{
                    "assertion_id": str(row.id),
                    "field": row.field,
                    "asserted_value": row.asserted_value[:600],
                    "assertion_kind": row.assertion_kind,
                    "source_label": row.source_label,
                    "source_text": (row.source_text or "")[:600] or None,
                    "source_event_id": str(row.source_event_id) if row.source_event_id else None,
                    "source_proposal_id": str(row.source_proposal_id) if row.source_proposal_id else None,
                    "assertion_status": row.assertion_status,
                    "epistemic_state": row.epistemic_state,
                    "recorded_at": _iso(row.recorded_at),
                    "observed_at": _iso(row.observed_at),
                    "evidence_ids": [str(value) for value in (row.evidence_ids or [])],
                } for row in assertion_rows[:MAX_RECENT_ITEMS]],
                "recent_assertions_limit": MAX_RECENT_ITEMS,
                "has_more_assertions": has_more_assertions,
            },
        })
        coverage["lcr"] = "partial" if (
            has_more_assertions or has_more_corrections or has_more_revisions or has_more_proposals
            or has_more_decisions or not revision_history_available
            or not proposal_history_available or not decision_history_available
        ) else "complete_aggregate_and_recent"

    if can_view_lcr and _table_available(db, "audit_log_events"):
        audit_query = db.query(AuditLogEvent).filter(AuditLogEvent.person_id == person_id)
        if not can_manage_members:
            audit_query = audit_query.filter(or_(
                AuditLogEvent.caregiver_id == caregiver_id,
                AuditLogEvent.actor_id == caregiver_id,
                AuditLogEvent.actor_id.like("system:%"),
            ))
        audit_count = audit_query.count()
        audit_rows = (
            audit_query.order_by(AuditLogEvent.occurred_at.desc(), AuditLogEvent.id.desc())
            .limit(MAX_RECENT_ITEMS + 1)
            .all()
        )
        has_more_audit = len(audit_rows) > MAX_RECENT_ITEMS
        actor_ids = {str(row.actor_id) for row in audit_rows[:MAX_RECENT_ITEMS] if row.actor_id}
        actor_names = {
            str(row.id): row.name for row in db.query(Caregiver).filter(
                Caregiver.person_id == person_id,
                Caregiver.id.in_(actor_ids),
            ).all()
        } if actor_ids else {}
        items.append({
            "kind": "audit_activity",
            "source_service": "audit_log_events",
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "available",
            "completeness": "recent_bounded_audit_events" if can_manage_members else "current_caregiver_and_system_events_only",
            "details": {
                "total_event_count": int(audit_count),
                "recent_events": [{
                    "event_id": str(row.id),
                    "actor_name": actor_names.get(str(row.actor_id), "SolenOS system" if str(row.actor_id or "").startswith("system:") else "Other caregiver or service"),
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "outcome": row.outcome,
                    "occurred_at": _iso(row.occurred_at),
                    "request_id": row.request_id,
                } for row in audit_rows[:MAX_RECENT_ITEMS]],
                "recent_events_limit": MAX_RECENT_ITEMS,
                "has_more_events": has_more_audit,
            },
        })
        coverage["audit_activity"] = "partial" if has_more_audit else ("complete_recent_window" if can_manage_members else "limited_actor_scope")
    elif can_view_lcr:
        items.append({
            "kind": "audit_activity",
            "source_service": "audit_log_events",
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "unavailable",
            "completeness": "unavailable",
            "reason": "The audit event table is not available in the connected database schema.",
        })
        coverage["audit_activity"] = "unavailable"

    if _table_available(db, "data_subject_requests"):
        requests = (
            db.query(DataSubjectRequest)
            .filter(
                DataSubjectRequest.person_id == person_id,
                DataSubjectRequest.caregiver_id == caregiver_id,
            )
            .order_by(DataSubjectRequest.requested_at.desc())
            .limit(MAX_RECENT_ITEMS + 1)
            .all()
        )
        has_more_requests = len(requests) > MAX_RECENT_ITEMS
        export_rows = []
        export_count = 0
        exports_have_more = False
        exports_available = _table_available(db, "privacy_export_artifacts")
        if exports_available:
            export_query = db.query(PrivacyExportArtifact).filter(
                PrivacyExportArtifact.person_id == person_id,
                PrivacyExportArtifact.caregiver_id == caregiver_id,
            )
            export_count = export_query.count()
            export_rows = (
                export_query.order_by(PrivacyExportArtifact.created_at.desc())
                .limit(MAX_RECENT_ITEMS + 1)
                .all()
            )
            exports_have_more = len(export_rows) > MAX_RECENT_ITEMS
        items.append({
            "kind": "privacy_data_control_operations",
            "source_service": "data_subject_requests_privacy_export_artifacts",
            "scope": {"person_id": person_id, "caregiver_id": caregiver_id},
            "observed_at": now.isoformat(),
            "status": "available",
            "completeness": "recent_bounded_requests_and_exports",
            "details": {
                "requests": [{
                    "request_id": str(row.id),
                    "request_type": row.request_type,
                    "request_status": row.request_status,
                    "requested_at": _iso(row.requested_at),
                    "resolved_at": _iso(row.resolved_at),
                    "resolution_code": row.resolution_code,
                } for row in requests[:MAX_RECENT_ITEMS]],
                "has_more_requests": has_more_requests,
                "exports": [{
                    "artifact_id": str(row.id),
                    "request_id": str(row.request_id),
                    "created_at": _iso(row.created_at),
                    "expires_at": _iso(row.expires_at),
                    "downloaded_at": _iso(row.downloaded_at),
                    "status": "expired" if _is_expired(row.expires_at, now) else "artifact_present",
                } for row in export_rows[:MAX_RECENT_ITEMS]],
                "export_count": int(export_count) if exports_available else None,
                "has_more_exports": exports_have_more if exports_available else None,
                "export_inventory_status": "available" if exports_available else "unavailable",
            },
        })
        coverage["privacy_requests"] = "partial" if (has_more_requests or not exports_available or exports_have_more) else "complete_recent_window"
    else:
        items.append({
            "kind": "privacy_data_control_operations",
            "source_service": "data_subject_requests",
            "scope": {"person_id": person_id, "caregiver_id": caregiver_id},
            "observed_at": now.isoformat(),
            "status": "unavailable",
            "completeness": "unavailable",
            "reason": "The privacy request table is not available in the connected database schema.",
        })
        coverage["privacy_requests"] = "unavailable"

    # Reuse the migration-owned privacy processing and legal-hold tables.
    # These are legacy SQL tables rather than ORM models; query only the current
    # care-recipient scope and expose no reviewer secrets or unrestricted rows.
    processing_policy_available = _table_available(db, "data_processing_records")
    processing_rows = []
    processing_count = None
    processing_has_more = False
    if processing_policy_available:
        processing_scope = "person_id = :person_id OR subject_id = :person_id"
        processing_count = db.execute(
            text(f"SELECT COUNT(*) FROM data_processing_records WHERE ({processing_scope}) "
                 "AND (active_from IS NULL OR active_from <= :now) "
                 "AND (active_until IS NULL OR active_until > :now)"),
            {"person_id": person_id, "now": now},
        ).scalar() or 0
        processing_rows = db.execute(
            text(f"SELECT id, processing_purpose, data_categories, legal_basis, retention_policy, "
                 f"active_from, active_until FROM data_processing_records WHERE ({processing_scope}) "
                 "AND (active_from IS NULL OR active_from <= :now) "
                 "AND (active_until IS NULL OR active_until > :now) "
                 "ORDER BY active_from DESC, id DESC LIMIT :limit"),
            {"person_id": person_id, "now": now, "limit": MAX_RECENT_ITEMS + 1},
        ).mappings().all()
        processing_has_more = len(processing_rows) > MAX_RECENT_ITEMS

    legal_holds_available = _table_available(db, "legal_holds")
    legal_hold_rows = []
    legal_hold_count = None
    legal_holds_have_more = False
    if legal_holds_available:
        legal_hold_count = db.execute(
            text("SELECT COUNT(*) FROM legal_holds WHERE subject_id = :person_id"),
            {"person_id": person_id},
        ).scalar() or 0
        legal_hold_rows = db.execute(
            text("SELECT id, resource_type, resource_id, hold_status, reason_code, "
                 "effective_at, released_at FROM legal_holds "
                 "WHERE subject_id = :person_id ORDER BY effective_at DESC, id DESC LIMIT :limit"),
            {"person_id": person_id, "limit": MAX_RECENT_ITEMS + 1},
        ).mappings().all()
        legal_holds_have_more = len(legal_hold_rows) > MAX_RECENT_ITEMS
    terminal_hold_states = {"released", "expired", "closed", "cancelled", "inactive"}
    recent_holds = [{
        "hold_id": str(row["id"]),
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "hold_status": row["hold_status"],
        "reason_code": row["reason_code"],
        "effective_at": _iso(row["effective_at"]),
        "released_at": _iso(row["released_at"]),
        "blocks_deletion": str(row["hold_status"] or "").strip().lower() not in terminal_hold_states,
    } for row in legal_hold_rows[:MAX_RECENT_ITEMS]]
    active_hold_count = sum(1 for row in recent_holds if row["blocks_deletion"])
    items.append({
        "kind": "privacy_security_retention_policy",
        "source_service": "data_processing_records_and_legal_holds",
        "scope": {"person_id": person_id},
        "observed_at": now.isoformat(),
        "status": (
            "available" if processing_policy_available and legal_holds_available
            else "partial" if processing_policy_available or legal_holds_available
            else "unavailable"
        ),
        "completeness": (
            "partial" if processing_has_more or legal_holds_have_more
            else "scoped_runtime_records"
            if processing_policy_available and legal_holds_available
            else "partial_or_unavailable_schema"
        ),
        "details": {
            "processing_policy_record_count": int(processing_count) if processing_count is not None else None,
            "active_processing_policies": [{
                "record_id": str(row["id"]),
                "purpose": row["processing_purpose"],
                "data_categories": row["data_categories"],
                "legal_basis": row["legal_basis"],
                "retention_policy": row["retention_policy"],
                "active_from": _iso(row["active_from"]),
                "active_until": _iso(row["active_until"]),
            } for row in processing_rows[:MAX_RECENT_ITEMS]],
            "has_more_processing_policies": processing_has_more if processing_policy_available else None,
            "processing_policy_inventory_status": (
                "available" if processing_policy_available and processing_count
                else "no_active_records" if processing_policy_available
                else "unavailable"
            ),
            "legal_hold_record_count": int(legal_hold_count) if legal_hold_count is not None else None,
            "active_or_unclassified_hold_count_in_returned_window": active_hold_count if legal_holds_available else None,
            "recent_legal_holds": recent_holds,
            "has_more_legal_holds": legal_holds_have_more if legal_holds_available else None,
            "legal_hold_inventory_status": (
                "available" if legal_holds_available and legal_hold_count
                else "no_records_found" if legal_holds_available
                else "unavailable"
            ),
        },
    })
    coverage["privacy_policy"] = (
        "partial" if (
            not processing_policy_available or not legal_holds_available
            or processing_has_more or legal_holds_have_more
        ) else "scoped_runtime_records"
    )

    # No user-scoped integration inventory is wired into this request. Explicitly
    # surface that gap; a missing registry is not evidence that no integrations exist.
    items.extend([
        {
            "kind": "connected_integrations",
            "source_service": None,
            "scope": {"caregiver_id": caregiver_id},
            "observed_at": now.isoformat(),
            "status": "unavailable",
            "completeness": "not_verified",
            "reason": "No user-scoped integration inventory is connected to this request.",
        },
    ])

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "scope": {
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "access_scope": grant.access_scope,
            "permissions": sorted(set(grant.permissions or [])),
            "can_manage_members": can_manage_members,
        },
        "coverage": coverage,
        "items": items,
        "limitations": [
            "This is a bounded transparency evidence snapshot, not a complete export of the care record.",
            "Document source text, uploaded bytes, credentials, and unrestricted audit metadata are intentionally excluded.",
            "Unavailable policy or integration evidence must not be inferred by the AI model.",
        ],
    }
