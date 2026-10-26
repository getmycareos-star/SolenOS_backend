from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Caregiver, Person
from app.models.collaboration import ConsentRecord
from app.models.qwen_background_job import QwenBackgroundJob
from app.services.qwen_event_triggers import enqueue_qwen_event_analysis


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[
        Person.__table__, Caregiver.__table__, ConsentRecord.__table__, QwenBackgroundJob.__table__,
    ])
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = sessions()
    person = Person(id="person-1", name="Care recipient")
    caregiver = Caregiver(id="caregiver-1", name="Caregiver", relationship="Family", person_id=person.id)
    db.add_all([person, caregiver])
    db.commit()
    return db


def _consent(db, *, acknowledged=True, revoked_at=None):
    db.add(ConsentRecord(
        person_id="person-1",
        caregiver_id="caregiver-1",
        privacy_model_acknowledged=acknowledged,
        accepted_at=datetime.now(timezone.utc),
        revoked_at=revoked_at,
    ))
    db.commit()


def test_event_trigger_requires_privacy_acknowledgement():
    db = _db()
    try:
        inserted = enqueue_qwen_event_analysis(
            db, person_id="person-1", caregiver_id="caregiver-1",
            source_type="care_event", source_id="event-1",
            summary="Care recipient slept poorly and woke repeatedly overnight.",
        )
        assert inserted is False
        assert db.query(QwenBackgroundJob).count() == 0
    finally:
        db.close()


def test_event_trigger_is_idempotent_per_source_event():
    db = _db()
    try:
        _consent(db)
        args = dict(
            person_id="person-1", caregiver_id="caregiver-1",
            source_type="care_event", source_id="event-1",
            summary="Care recipient slept poorly and woke repeatedly overnight.",
            context_text="status=recorded; provenance=caregiver report",
        )
        assert enqueue_qwen_event_analysis(db, **args) is True
        db.commit()
        assert enqueue_qwen_event_analysis(db, **args) is False
        db.commit()
        assert db.query(QwenBackgroundJob).count() == 1
    finally:
        db.close()


def test_event_trigger_skips_community_and_nonmeaningful_content():
    db = _db()
    try:
        _consent(db)
        assert enqueue_qwen_event_analysis(
            db, person_id="person-1", caregiver_id="caregiver-1",
            source_type="community", source_id="post-1",
            summary="A caregiver posted a community story with enough text.",
        ) is False
        assert enqueue_qwen_event_analysis(
            db, person_id="person-1", caregiver_id="caregiver-1",
            source_type="care_event", source_id="event-2", summary="Saved.",
        ) is False
        assert db.query(QwenBackgroundJob).count() == 0
    finally:
        db.close()


def test_event_trigger_respects_rate_limit(monkeypatch):
    db = _db()
    try:
        _consent(db)
        monkeypatch.setattr("app.services.qwen_event_triggers.MAX_TRIGGERED_JOBS_PER_HOUR", 0)
        assert enqueue_qwen_event_analysis(
            db, person_id="person-1", caregiver_id="caregiver-1",
            source_type="care_event", source_id="event-3",
            summary="Care recipient slept poorly and woke repeatedly overnight.",
        ) is False
        assert db.query(QwenBackgroundJob).count() == 0
    finally:
        db.close()
