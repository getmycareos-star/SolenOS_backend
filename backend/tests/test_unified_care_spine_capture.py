"""Regression test for the unified Daily Care -> LCR -> Companion retrieval spine.

The capture is written through the public HTTP route, then read through a fresh
SQLAlchemy session using the exact retrieval composition used by Companion.
"""
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.services.unified_lcr_retrieval import build_unified_lcr_context


def test_daily_capture_is_available_to_companion_from_a_fresh_database_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    person_id = "unified-spine-person"
    caregiver_id = "unified-spine-caregiver"
    token = "unified-spine-test-token"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with TestingSessionLocal() as db:
            person = Person(id=person_id, name="Unified spine test person")
            caregiver = Caregiver(
                id=caregiver_id,
                name="Family caregiver",
                email="unified-spine@example.invalid",
                relationship="Family",
                person_id=person_id,
            )
            db.add_all([person, caregiver])
            db.flush()
            db.add(AnonymousSession(
                person_id=person_id,
                caregiver_id=caregiver_id,
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=headers,
            json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "general_notes": "Mum was unusually confused this afternoon and refused lunch.",
            },
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["success"] is True
        assert len(receipt["evidence_ids"]) == 1

        event_id = receipt["id"]
        evidence_id = receipt["evidence_ids"][0]

        # Close the write session and reconstruct retrieval from a new DB session.
        with TestingSessionLocal() as db:
            event = db.query(CareEvent).filter_by(id=event_id, person_id=person_id).one()
            evidence = db.query(Evidence).filter_by(id=evidence_id, person_id=person_id).one()
            assert event.evidence_ids == [evidence_id]
            assert evidence.reported_by_caregiver_id == caregiver_id
            assert evidence.epistemic_status == "caregiver_reported"

        with TestingSessionLocal() as db:
            context = build_unified_lcr_context(
                db, person_id, caregiver_id, limit_per_source=50
            )

        sources = context["sources"]
        event_rows = [row for row in sources["events"]["items"] if row["id"] == event_id]
        evidence_rows = [row for row in sources["evidence"]["items"] if row["id"] == evidence_id]
        assert len(event_rows) == 1
        assert len(evidence_rows) == 1
        assert event_rows[0]["evidence_ids"] == [evidence_id]
        assert evidence_rows[0]["epistemic_status"] == "caregiver_reported"
        assert context["retrieval_contract"]["retrieval_strategy"] == "hybrid_lexical_semantic_temporal"
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
