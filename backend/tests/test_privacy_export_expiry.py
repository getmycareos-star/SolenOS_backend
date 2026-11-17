from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.privacy_delivery import AuditLogEvent
from app.models.privacy_request import DataSubjectRequest
from app.models.privacy_export import PrivacyExportArtifact
from app.services import operational_heartbeats, privacy_export_expiry


def _expiry_db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        DataSubjectRequest.__table__,
        PrivacyExportArtifact.__table__,
        AuditLogEvent.__table__,
    ])
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE operational_worker_heartbeats (
                worker_name VARCHAR(100) PRIMARY KEY,
                last_started_at DATETIME NOT NULL,
                last_completed_at DATETIME NOT NULL,
                last_success_at DATETIME,
                last_outcome VARCHAR(20) NOT NULL,
                last_error_type VARCHAR(120),
                last_processed_count INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL
            )
        """))
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(privacy_export_expiry, "SessionLocal", Session)
    monkeypatch.setattr(operational_heartbeats, "SessionLocal", Session)
    heartbeats = []
    monkeypatch.setattr(
        privacy_export_expiry,
        "record_worker_heartbeat",
        lambda worker_name, **kwargs: heartbeats.append((worker_name, kwargs)),
    )
    return engine, Session, heartbeats


def _artifact(session, artifact_id, expires_at):
    request_id = f"request-{artifact_id}"
    session.add(DataSubjectRequest(
        id=request_id,
        requester_id="caregiver-synthetic",
        request_type="export",
        request_status="approved_pending_fulfilment_assessment",
        requested_at=datetime.now(timezone.utc),
    ))
    session.flush()
    row = PrivacyExportArtifact(
        id=artifact_id,
        request_id=request_id,
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
    engine, Session, heartbeats = _expiry_db(monkeypatch)
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
        assert heartbeats == [(
            "privacy_export_expiry",
            {"outcome": "success", "processed_count": 1},
        )]
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
            heartbeat = db.execute(text("""
                SELECT last_outcome, last_success_at, last_processed_count
                FROM operational_worker_heartbeats
                WHERE worker_name='privacy_export_expiry'
            """)).one()
            assert heartbeat.last_outcome == "success"
            assert heartbeat.last_success_at is not None
            assert heartbeat.last_processed_count == 1
            last_success = heartbeat.last_success_at
            operational_heartbeats.record_worker_heartbeat(
                "privacy_export_expiry",
                outcome="error",
                processed_count=0,
                error_type="SyntheticFailure",
            )
            failed_heartbeat = db.execute(text("""
                SELECT last_outcome, last_success_at, last_error_type
                FROM operational_worker_heartbeats
                WHERE worker_name='privacy_export_expiry'
            """)).one()
            assert failed_heartbeat.last_outcome == "error"
            assert failed_heartbeat.last_success_at == last_success
            assert failed_heartbeat.last_error_type == "SyntheticFailure"
        finally:
            db.close()
    finally:
        engine.dispose()


def test_expiry_worker_is_idempotent_and_batch_bounded(monkeypatch):
    engine, Session, heartbeats = _expiry_db(monkeypatch)
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
        assert [item[1]["processed_count"] for item in heartbeats] == [2, 1, 0]
        assert all(item[0] == "privacy_export_expiry" for item in heartbeats)
        assert all(item[1]["outcome"] == "success" for item in heartbeats)
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
