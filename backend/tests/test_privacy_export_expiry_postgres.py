from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import text

from app.core.database import engine, SessionLocal
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_export import PrivacyExportArtifact
from app.models.privacy_request import DataSubjectRequest
from app.services.privacy_export_expiry import expire_privacy_exports_once


@pytest.mark.skipif(engine.dialect.name != "postgresql", reason="PostgreSQL locking behavior only")
def test_expired_export_cleanup_uses_postgresql_row_lock_and_persists_receipt():
    suffix = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(DataSubjectRequest(
            id=request_id,
            requester_id="synthetic-ci-caregiver",
            request_type="export",
            request_status="approved_pending_fulfilment_assessment",
            requested_at=now,
        ))
        db.flush()
        db.add(PrivacyExportArtifact(
            id=artifact_id,
            request_id=request_id,
            person_id=f"synthetic-person-{suffix}",
            caregiver_id=f"synthetic-caregiver-{suffix}",
            filename="synthetic-export.zip",
            content=b"synthetic-encrypted-export",
            size_bytes=len(b"synthetic-encrypted-export"),
            sha256="b" * 64,
            expires_at=now - timedelta(minutes=1),
        ))
        db.commit()

        result = expire_privacy_exports_once(batch_size=10)
        assert result["deleted"] == 1
        assert db.query(PrivacyExportArtifact).filter_by(id=artifact_id).count() == 0
        receipt = db.query(AuditLogEvent).filter_by(
            target_id=artifact_id,
            action="privacy_export.expired",
        ).one()
        assert receipt.outcome == "deleted"
        assert receipt.record_metadata["expiry_enforced"] is True
        assert receipt.record_metadata["size_bytes"] == len(b"synthetic-encrypted-export")
        heartbeat = db.execute(text("""
            SELECT last_outcome, last_success_at, last_processed_count
            FROM operational_worker_heartbeats
            WHERE worker_name='privacy_export_expiry'
        """)).one()
        assert heartbeat.last_outcome == "success"
        assert heartbeat.last_success_at is not None
        assert heartbeat.last_processed_count >= 1
    finally:
        db.rollback()
        # Audit events are append-only. Use the same transaction-local capability
        # as reviewed erasure to redact this synthetic fixture, never delete it.
        db.execute(text("SET LOCAL solenos.privacy_erasure = 'on'"))
        db.query(AuditLogEvent).filter_by(target_id=artifact_id).update(
            {
                AuditLogEvent.actor_id: None,
                AuditLogEvent.subject_id: None,
                AuditLogEvent.person_id: None,
                AuditLogEvent.caregiver_id: None,
                AuditLogEvent.target_id: None,
                AuditLogEvent.request_id: None,
                AuditLogEvent.correlation_id: None,
                AuditLogEvent.record_metadata: {"redacted_for_test": True},
            },
            synchronize_session=False,
        )
        db.query(PrivacyExportArtifact).filter_by(id=artifact_id).delete(synchronize_session=False)
        db.query(DataSubjectRequest).filter_by(id=request_id).delete(synchronize_session=False)
        db.commit()
        db.close()
