from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import Caregiver, CareEvent, Person
from app.models.daily_care import DailyCareEntry

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


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_function():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def seed_person_and_caregiver():
    db = TestingSessionLocal()
    try:
        person = Person(id="daily-person", name="Daily Care Test Person")
        caregiver = Caregiver(
            id="daily-caregiver",
            name="Daily Care Test Caregiver",
            email="daily-caregiver@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
    finally:
        db.close()


def test_daily_care_form_persists_and_can_be_listed():
    seed_person_and_caregiver()
    client = TestClient(app)
    response = client.post(
        "/api/v1/daily-care/entries",
        json={
            "person_id": "daily-person",
            "caregiver_id": "daily-caregiver",
            "mood": "Calm",
            "mood_scale": 4,
            "agitation_level": "None",
            "vitals": {"heart_rate": "72"},
            "general_notes": "A stable day.",
            "location": {"latitude": 6.5, "longitude": 3.4},
            "location_provenance": "user_provided",
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["person_id"] == "daily-person"
    assert created["mood"] == "Calm"
    assert created["vitals"] == {"heart_rate": "72"}

    listed = client.get("/api/v1/daily-care/entries", params={"person_id": "daily-person"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == created["id"]

    db = TestingSessionLocal()
    try:
        assert db.query(DailyCareEntry).filter_by(id=created["id"]).count() == 1
        assert db.query(CareEvent).filter_by(person_id="daily-person").count() == 1
    finally:
        db.close()


def test_structured_daily_log_alias_persists_arrays():
    seed_person_and_caregiver()
    client = TestClient(app)
    response = client.post(
        "/api/v1/daily/log",
        json={
            "person_id": "daily-person",
            "caregiver_id": "daily-caregiver",
            "medications": [{"medication_name": "Example", "status": "given"}],
            "vitals": [{"vital_type": "heart_rate", "value": 72, "measured_at": "2026-10-21T10:00:00Z"}],
            "scales": [{"axis": "mood", "score": 4, "observed_at": "2026-10-21T10:00:00Z"}],
            "notes": "Structured entry",
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["medications"][0]["medication_name"] == "Example"
    assert created["vitals"][0]["vital_type"] == "heart_rate"
    assert created["scales"][0]["axis"] == "mood"


def test_daily_care_rejects_caregiver_linked_to_another_person():
    db = TestingSessionLocal()
    db.add_all([
        Person(id="daily-person", name="Daily Care Test Person"),
        Person(id="other-person", name="Other Person"),
    ])
    db.add(Caregiver(
        id="daily-caregiver",
        name="Daily Care Test Caregiver",
        email="daily-caregiver@example.com",
        relationship="Family",
        person_id="other-person",
    ))
    db.commit()
    db.close()

    response = TestClient(app).post(
        "/api/v1/daily-care/entries",
        json={"person_id": "daily-person", "caregiver_id": "daily-caregiver", "general_notes": "should fail"},
    )
    assert response.status_code == 400
