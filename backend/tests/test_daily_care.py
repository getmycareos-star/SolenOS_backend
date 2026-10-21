from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import Caregiver, CareEvent, Person
from app.models.daily_care import DailyCareEntry
from app.models.uploads import StoredUpload
from app.models.care import Evidence

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

def test_image_upload_persists_bytes_and_evidence_record():
    seed_person_and_caregiver()
    client = TestClient(app)
    image_bytes = b"test-png-bytes"
    response = client.post(
        "/api/v1/upload",
        data={"person_id": "daily-person", "caregiver_id": "daily-caregiver"},
        files={"file": ("medication.png", image_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["evidence_id"]
    assert result["file_id"]
    assert result["size_bytes"] == len(image_bytes)

    db = TestingSessionLocal()
    try:
        upload = db.query(StoredUpload).filter_by(id=result["file_id"]).one()
        evidence = db.query(Evidence).filter_by(id=result["evidence_id"]).one()
        assert upload.content == image_bytes
        assert upload.evidence_id == evidence.id
        assert evidence.person_id == "daily-person"
    finally:
        db.close()

    download = client.get(f"/api/v1/upload/{result['file_id']}")
    assert download.status_code == 200
    assert download.content == image_bytes
    assert download.headers["content-type"] == "image/png"


def test_image_upload_rejects_non_image_and_unlinked_caregiver():
    seed_person_and_caregiver()
    client = TestClient(app)
    unsupported = client.post(
        "/api/v1/upload",
        data={"person_id": "daily-person", "caregiver_id": "daily-caregiver"},
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert unsupported.status_code == 415

    unlinked = client.post(
        "/api/v1/upload",
        data={"person_id": "unknown-person", "caregiver_id": "daily-caregiver"},
        files={"file": ("image.png", b"png", "image/png")},
    )
    assert unlinked.status_code == 404
