"""Integration tests for the full caregiver runtime path.

Trace: input → evidence → event → change detection → situation formation → response
"""
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.models.evidence import Evidence
from app.models.care_event import CareEvent
from app.models.change import Change, Situation
from app.schemas.situation import SituationInput

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    engine.dispose()
    app.dependency_overrides.clear()


def test_post_situation_persists_change_detection_and_situation_results():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="int-person-1", name="Test Person")
        caregiver = Caregiver(
            id="int-cg-1",
            name="Test Caregiver",
            email="int-test-1@example.com",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app, base_url="http://localhost")
        payload = SituationInput(
                caregiver_id="int-cg-1",
                person_id="int-person-1",
                raw_input="The person fell while getting out of bed.",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        response = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {payload.caregiver_id}"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "situations" in data
        assert "what_changed" in data
        assert "attention_candidate" in data
        assert "follow_up_candidate" in data

        db = TestingSessionLocal()
        evidence = db.query(Evidence).filter(Evidence.person_id == "int-person-1").first()
        assert evidence is not None
        assert evidence.source_type == "caregiver_observation"
        assert evidence.evidence_status == "reported"

        event = db.query(CareEvent).filter(CareEvent.person_id == "int-person-1").first()
        assert event is not None
        assert event.event_type == "observation"

        changes = db.query(Change).filter(Change.person_id == "int-person-1").all()
        assert changes, "The capture pipeline must persist a change-detection result"
        assert max(change.current_count or 0 for change in changes) >= 1

        situations = db.query(Situation).filter(Situation.person_id == "int-person-1").all()
        # Situation formation is conditional on meaningful evidence. The API
        # must return exactly the situations that were actually persisted; an
        # empty list is valid, but an unconditional >= 0 assertion is not.
        response_situations = data.get("situations") or []
        assert len(response_situations) == len(situations), (
            f"Response/persistence mismatch: response={response_situations!r}, "
            f"persisted={len(situations)}"
        )
        db.close()
    finally:
        teardown_db()


def test_multi_update_longitudinal_reasoning():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="int-person-2", name="Test Person 2")
        caregiver = Caregiver(
            id="int-cg-2",
            name="Test Caregiver 2",
            email="int-test-2@example.com",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app, base_url="http://localhost")
        inputs = [
            "The person fell while getting out of bed.",
            "Another fall occurred in the bathroom today.",
            "The person seems unsteady on their feet lately.",
        ]
        for i, text in enumerate(inputs):
            payload = SituationInput(
                caregiver_id="int-cg-2",
                person_id="int-person-2",
                raw_input=text,
                timestamp=(datetime.now(timezone.utc) - timedelta(days=2 - i)).isoformat(),
            )
            response = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {payload.caregiver_id}"})
            assert response.status_code == 200, f"Turn {i+1} failed: {response.text}"

        db = TestingSessionLocal()
        events = db.query(CareEvent).filter(CareEvent.person_id == "int-person-2").count()
        assert events == 3

        evidence_items = db.query(Evidence).filter(Evidence.person_id == "int-person-2").count()
        assert evidence_items == 3

        changes = db.query(Change).filter(Change.person_id == "int-person-2").all()
        assert changes, "Repeated observations must reach persisted change detection"
        assert max(change.current_count or 0 for change in changes) == 3, (
            "The later result must reflect all three observations, not only the "
            f"latest request; observed counts={[change.current_count for change in changes]!r}"
        )
        db.close()
    finally:
        teardown_db()


def test_identical_payload_without_idempotency_key_is_two_submissions():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="int-person-3", name="Test Person 3")
        caregiver = Caregiver(
            id="int-cg-3",
            name="Test Caregiver 3",
            email="int-test-3@example.com",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app, base_url="http://localhost")
        payload = SituationInput(
            caregiver_id="int-cg-3",
            person_id="int-person-3",
            raw_input="Identical observation submitted twice.",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        r1 = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {payload.caregiver_id}"})
        r2 = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {payload.caregiver_id}"})
        assert r1.status_code == 200
        assert r2.status_code == 200

        db = TestingSessionLocal()
        events = db.query(CareEvent).filter(CareEvent.person_id == "int-person-3").count()
        evidence_items = db.query(Evidence).filter(Evidence.person_id == "int-person-3").count()
        # Without an Idempotency-Key the server cannot distinguish a retry
        # from a second intentional capture; the client contract must supply it.
        assert events == 2
        assert evidence_items == 2
        db.close()
    finally:
        teardown_db()


def test_empty_input_returns_400():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="int-person-4", name="Test Person 4")
        caregiver = Caregiver(
            id="int-cg-4",
            name="Test Caregiver 4",
            email="int-test-4@example.com",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app, base_url="http://localhost")
        payload = SituationInput(
            caregiver_id="int-cg-4",
            person_id="int-person-4",
            raw_input="   ",
        )
        response = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {payload.caregiver_id}"})
        assert response.status_code == 400
    finally:
        teardown_db()
