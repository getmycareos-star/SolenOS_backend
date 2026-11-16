from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_export import PrivacyExportArtifact
from app.services import privacy_export_expiry


def _expiry_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        PrivacyExportArtifact.__table__,
        AuditLogEvent.__table__,
    ])
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(privacy_export_expiry, "SessionLocal", Session)
    return engine, Session


def _artifact(session, artifact_id, expires_at):
    row = PrivacyExportArtifact(
        id=artifact_id,
        request_id=f"request-{artifact_id}",
        person_id="person-synthetic",
        caregiver_id="caregiver-synthetic",
        filename="export.zip",
        content=b"encrypted-synthetic-export",
        size_bytes=26,
        sha256="a" * 64,
        expires_at=expires_at,
    )
    session.add(row)
    session.commit()


def test_expiry_worker_deletes_only_expired_export_bytes_and_writes_receipt(monkeypatch):
    engine, Session = _expiry_db(monkeypatch)
    now = datetime.now(timezone.utc)
    db = Session()
    try:
        _artifact(db, "expired", now - timedelta(seconds=1))
        _artifact(db, "still-valid", now + timedelta(hours=1))
    finally:
        db.close()

    try:
        result = privacy_export_expiry.expire_privacy_exports_once()
        assert result == {"examined": 1, "deleted": 1, "failed": 0}
        db = Session()
        try:
            assert db.query(PrivacyExportArtifact).filter_by(id="expired").count() == 0
            assert db.query(PrivacyExportArtifact).filter_by(id="still-valid").count() == 1
            receipt = db.query(AuditLogEvent).filter_by(
                target_id="expired",
                action="privacy_export.expired",
            ).one()
            assert receipt.actor_id == "system:privacy-export-expiry"
            assert receipt.outcome == "deleted"
            assert receipt.record_metadata == {
                "size_bytes": 26,
                "sha256": "a" * 64,
                "expiry_enforced": True,
            }
            assert b"encrypted-synthetic-export" not in str(receipt.record_metadata).encode()
        finally:
            db.close()
    finally:
        engine.dispose()


def test_expiry_worker_is_idempotent_and_batch_bounded(monkeypatch):
    engine, Session = _expiry_db(monkeypatch)
    db = Session()
    try:
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        for index in range(3):
            _artifact(db, f"expired-{index}", now)
    finally:
        db.close()

    try:
        assert privacy_export_expiry.expire_privacy_exports_once(batch_size=2)["deleted"] == 2
        assert privacy_export_expiry.expire_privacy_exports_once(batch_size=2)["deleted"] == 1
        assert privacy_export_expiry.expire_privacy_exports_once(batch_size=2)["deleted"] == 0
    finally:
        engine.dispose()


def test_expiry_worker_rejects_unbounded_or_invalid_batches():
    for value in (0, -1, 1001, True, 1.5, "10"):
        try:
            privacy_export_expiry.expire_privacy_exports_once(batch_size=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected ValueError for batch_size={value!r}")
