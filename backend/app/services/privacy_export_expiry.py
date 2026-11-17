"""Bounded cleanup for expired, encrypted privacy-export artifacts.

Only short-lived export artifacts are eligible. Care records, source evidence,
documents, audit events and account data are deliberately out of scope.
"""
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_export import PrivacyExportArtifact
from app.services.operational_heartbeats import record_worker_heartbeat


def expire_privacy_exports_once(batch_size: int = 100) -> dict:
    """Delete one bounded batch of expired exports and record metadata-only receipts.

    The row lock plus SKIP LOCKED allows safe operation with more than one
    application replica. The operation is idempotent: deleted rows cannot be
    processed again. The artifact bytes are never copied into the audit log.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be an integer between 1 and 1000")

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    deleted = 0
    try:
        rows = (
            db.query(PrivacyExportArtifact)
            .filter(PrivacyExportArtifact.expires_at <= now)
            .order_by(PrivacyExportArtifact.expires_at.asc(), PrivacyExportArtifact.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .all()
        )
        for artifact in rows:
            db.add(AuditLogEvent(
                actor_id="system:privacy-export-expiry",
                subject_id=artifact.person_id,
                person_id=artifact.person_id,
                caregiver_id=artifact.caregiver_id,
                action="privacy_export.expired",
                target_type="privacy_export_artifact",
                target_id=artifact.id,
                occurred_at=now,
                outcome="deleted",
                request_id=artifact.request_id,
                correlation_id=artifact.request_id,
                policy_version="privacy-export-expiry-v1",
                record_metadata={
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "expiry_enforced": True,
                },
            ))
            db.delete(artifact)
            deleted += 1
        db.commit()
        record_worker_heartbeat(
            "privacy_export_expiry",
            outcome="success",
            processed_count=deleted,
            started_at=now,
        )
        return {"examined": deleted, "deleted": deleted, "failed": 0}
    except Exception as exc:
        db.rollback()
        try:
            record_worker_heartbeat(
                "privacy_export_expiry",
                outcome="error",
                processed_count=deleted,
                error_type=type(exc).__name__,
                started_at=now,
            )
        except Exception:
            # Preserve the original failure; the application logger records it.
            pass
        raise
    finally:
        db.close()
