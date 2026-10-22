from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import CareEvent, Caregiver, Evidence, Person

engine = create_engine(
    "sqlite:///:memory:",
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
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def seed_person_and_caregiver():
    db = TestingSessionLocal()
    person = Person(id="person-daily-test", name="Daily Capture Test Person")
    caregiver = Caregiver(
        id="caregiver-daily-test",
        name="Daily Capture Test Caregiver",
        email="daily-capture-test@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()


def test_daily_capture_persists_for_linked_caregiver():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "mood": "Calm",
                "general_notes": "User-recorded note",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True

        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        assert event.person_id == "person-daily-test"
        assert event.created_by_caregiver_id == "caregiver-daily-test"
        assert "User-recorded note" in event.description
        db.close()
    finally:
        teardown_db()


def test_daily_capture_rejects_unknown_person():
    setup_db()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            json={
                "person_id": "missing-person",
                "caregiver_id": "caregiver-daily-test",
                "mood": "Calm",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "You are not a member of this care circle."
    finally:
        teardown_db()


def test_daily_capture_rejects_unlinked_caregiver():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "not-linked-caregiver",
                "mood": "Calm",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "You are not a member of this care circle."
    finally:
        teardown_db()



def seed_second_person_and_caregiver():
    db = TestingSessionLocal()
    person = Person(id="person-other-test", name="Other Test Person")
    caregiver = Caregiver(
        id="caregiver-other-test",
        name="Other Test Caregiver",
        email="other-test-caregiver@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()


def test_event_write_rejects_caregiver_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/events",
            json={
                "person_id": "person-daily-test",
                "event_type": "observation",
                "occurred_at": "2026-10-22T00:00:00Z",
                "title": "Ownership test",
                "created_by_caregiver_id": "caregiver-other-test",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] in {
            "Caregiver is not linked to this care record",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()


def test_appointment_write_rejects_caregiver_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/appointments",
            json={
                "person_id": "person-daily-test",
                "title": "Ownership test appointment",
                "scheduled_at": "2026-10-23T10:00:00Z",
                "created_by_caregiver_id": "caregiver-other-test",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] in {
            "Caregiver is not linked to this care record",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()



def test_caregiver_creation_rejects_unknown_person():
    setup_db()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/caregivers",
            json={
                "name": "Unlinked Caregiver",
                "email": "unknown-person@example.invalid",
                "relationship": "Family",
                "person_id": "missing-person",
            },
        )
        # The authorization middleware may reject the unknown care-record ID
        # before the endpoint can return its more specific 404.
        assert response.status_code in {403, 404}, response.text
        assert response.json()["detail"] in {
            "Person not found",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()


def test_insight_rejects_evidence_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        db = TestingSessionLocal()
        foreign_evidence = Evidence(
            id="foreign-evidence-test",
            person_id="person-other-test",
            type="note",
            source_text="Evidence belongs to a different care record",
            uploaded_by_caregiver_id="caregiver-other-test",
        )
        db.add(foreign_evidence)
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/v1/insights",
            json={
                "person_id": "person-daily-test",
                "title": "Cross-record evidence test",
                "description": "This should be rejected",
                "insight_type": "observation",
                "confidence": 0.5,
                "evidence_ids": ["foreign-evidence-test"],
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Every evidence item must exist and belong to the same care record"
    finally:
        teardown_db()
