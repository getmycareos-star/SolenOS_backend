"""Read-only, authorization-scoped evidence for caregiver transparency questions.

This service deliberately returns metadata and operation state, not care-source
text, document bytes, credentials, or unrestricted audit metadata. It reuses the
existing PostgreSQL records; it does not create a parallel LCR or audit ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, inspect, or_
from sqlalchemy.orm import Session

from app.models.care import CareEvent, Caregiver, Correction, Evidence
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import CareAssertion
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

    coverage: dict[str, str] = {
        "authenticated_scope": "complete" if caregiver else "partial",
        "sharing_members": "not_authorized",
        "documents": "not_authorized",
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
        if uploads_available:
            evidence_count = (
                db.query(func.count(Evidence.id))
                .join(EvidenceUpload, EvidenceUpload.evidence_id == Evidence.id)
                .filter(Evidence.person_id == person_id)
                .scalar() or 0
            )
            document_rows = (
                db.query(Evidence, EvidenceUpload)
                .join(EvidenceUpload, EvidenceUpload.evidence_id == Evidence.id)
                .filter(Evidence.person_id == person_id)
                .order_by(Evidence.uploaded_at.desc(), Evidence.id.desc())
                .limit(MAX_RECENT_ITEMS + 1)
                .all()
            )
        else:
            evidence_count = 0
            document_rows = []
        has_more = len(document_rows) > MAX_RECENT_ITEMS
        recent_documents = []
        for evidence, upload in document_rows[:MAX_RECENT_ITEMS]:
            recent_documents.append({
                "evidence_id": str(evidence.id),
                "evidence_type": evidence.type,
                "evidence_kind": evidence.evidence_kind,
                "evidence_status": evidence.evidence_status,
                "epistemic_status": evidence.epistemic_status,
                "uploaded_at": _iso(evidence.uploaded_at),
                "filename": upload.filename if upload else None,
                "mime_type": upload.mime_type if upload else None,
                "size_bytes": upload.size_bytes if upload else None,
                "binary_storage_backend": upload.storage_backend if upload else None,
                "upload_record_available": bool(upload),
            })
        extraction_rows = (\n            db.query(CaptureAnalysis.extraction_status, func.count(CaptureAnalysis.id))\n            .filter(\n                CaptureAnalysis.person_id == person_id,\n                CaptureAnalysis.source_evidence_id.is_not(None),\n            )\n            .group_by(CaptureAnalysis.extraction_status)\n            .all()\n        )\n        extraction_counts = {str(status or "unknown"): int(count) for status, count in extraction_rows}
        items.append({
            "kind": "documents_and_extraction",
            "source_service": "evidence_evidence_uploads_capture_analyses",
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "available" if uploads_available else "partial",
            "completeness": "recent_bounded_list" if has_more else ("metadata_complete" if uploads_available else "upload_metadata_unavailable"),
            "details": {
                "evidence_count": int(evidence_count),
                "recent_documents": recent_documents,
                "recent_documents_limit": MAX_RECENT_ITEMS,
                "has_more_recent_documents": has_more,
                "extraction_status_counts": extraction_counts,
                "upload_binary_metadata_available": uploads_available,
            },
        })
        coverage["documents"] = "partial" if has_more or not uploads_available else "complete_metadata"

    if can_view_lcr:
        assertion_status_counts = _count_by(db, CareAssertion, CareAssertion.assertion_status, person_id)
        epistemic_state_counts = _count_by(db, CareAssertion, CareAssertion.epistemic_state, person_id)
        event_count = db.query(func.count(CareEvent.id)).filter(CareEvent.person_id == person_id).scalar() or 0
        correction_status_counts = _count_by(db, Correction, Correction.processing_status, person_id)
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
            "status": "available",
            "completeness": "aggregate_counts_and_recent_bounded_assertions",
            "details": {
                "care_event_count": int(event_count),
                "assertion_status_counts": assertion_status_counts,
                "epistemic_state_counts": epistemic_state_counts,
                "correction_processing_status_counts": correction_status_counts,
                "recent_assertions": [{
                    "assertion_id": str(row.id),
                    "field": row.field,
                    "source_label": row.source_label,
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
        coverage["lcr"] = "partial" if has_more_assertions else "complete_aggregate_and_recent"

    if can_view_lcr and _table_available(db, "audit_log_events"):
        audit_query = db.query(AuditLogEvent).filter(AuditLogEvent.person_id == person_id)
        if not can_manage_members:
            audit_query = audit_query.filter(or_(\n                AuditLogEvent.caregiver_id == caregiver_id,\n                AuditLogEvent.actor_id == caregiver_id,\n                AuditLogEvent.actor_id.like("system:%"),\n            ))\n        audit_count = audit_query.count()
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
        coverage["audit_activity"] = "partial" if has_more_audit else "complete_recent_window"
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
        export_rows = []\n        export_count = 0\n        exports_have_more = False\n        if _table_available(db, "privacy_export_artifacts"):\n            export_query = db.query(PrivacyExportArtifact).filter(\n                PrivacyExportArtifact.person_id == person_id,\n                PrivacyExportArtifact.caregiver_id == caregiver_id,\n            )\n            export_count = export_query.count()\n            export_rows = (\n                export_query.order_by(PrivacyExportArtifact.created_at.desc())\n                .limit(MAX_RECENT_ITEMS + 1)\n                .all()\n            )\n            exports_have_more = len(export_rows) > MAX_RECENT_ITEMS
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
                "export_count": int(export_count),
                "has_more_exports": exports_have_more,
            },
        })
        coverage["privacy_requests"] = "partial" if has_more_requests else "complete_recent_window"
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

    # No runtime-authoritative policy registry or user-scoped integration inventory
    # is currently wired into this provider. Explicitly surface the gap; never infer.
    items.extend([
        {
            "kind": "privacy_security_retention_policy",
            "source_service": None,
            "scope": {"person_id": person_id},
            "observed_at": now.isoformat(),
            "status": "unavailable",
            "completeness": "not_verified",
            "reason": "No authoritative runtime policy/configuration evidence is connected to this request.",
        },
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
