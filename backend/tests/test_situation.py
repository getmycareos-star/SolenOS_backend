from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver, CareEvent
from app.models.evidence import Evidence

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


app.dependency_overrides = {}


def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    engine.dispose()
    app.dependency_overrides.clear()


def test_post_situation_creates_evidence_and_event():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="care_test_key_00000001",
            name="Test Caregiver",
            email="test@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers={"Authorization": "Bearer care_test_key_00000001"},
            json={
                "caregiver_id": "care_test_key_00000001",
                "person_id": "person-1",
                "raw_input": "Patient seemed more confused than usual today.",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["care_key"] == "caregiver-1"
        assert data["person_id"] == "person-1"
        assert data["message"] == "Care input recorded successfully."
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Patient seemed more confused than usual today."
        assert data["evidence_id"] is not None
        assert data["care_event_id"] is not None
    finally:
        teardown_db()


def test_post_situation_with_documents():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-2", name="Test Person 2")
        caregiver = Caregiver(
            id="care_test_key_00000002",
            name="Test Caregiver 2",
            email="test2@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers={"Authorization": "Bearer care_test_key_00000002"},
            json={
                "caregiver_id": "care_test_key_00000002",
                "person_id": "person-2",
                "raw_input": "",
                "documents": [
                    {
                        "id": "doc-1",
                        "name": "discharge_summary.pdf",
                        "extracted_text": "Patient cleared for discharge with follow-up in 1 week.",
                        "mime_type": "application/pdf",
                        "ocr_confidence": 0.95,
                    }
                ],
                "provenance": {
                    "input_type": "document",
                    "entry_method": "upload",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "recognition_confidence": 0.95,
                    "transcript_uncertain": False,
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["events"]) == 1
        assert data["events"][0]["title"] == "Document observation"
    finally:
        teardown_db()


def test_post_situation_missing_caregiver():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        db.add(person)
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers={"Authorization": "Bearer care_missing_key_00000001"},
            json={
                "caregiver_id": "care_missing_key_00000001",
                "person_id": "person-1",
                "raw_input": "Some input",
            },
        )
        assert response.status_code == 403
        assert "not authorized for this person" in response.text
    finally:
        teardown_db()


def test_post_situation_rejects_person_id_outside_care_key():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        caregiver = Caregiver(
            id="care_test_key_00000003",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id="person-3",
        )
        db.add(caregiver)
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers={"Authorization": "Bearer care_test_key_00000003"},
            json={
                "caregiver_id": "care_test_key_00000003",
                "person_id": "nonexistent",
                "raw_input": "Some input",
            },
        )
        assert response.status_code == 404
        assert "Person not found" in response.json().get("detail", response.json().get("error", ""))
    finally:
        teardown_db()


def test_post_situation_empty_input_and_no_documents():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-4", name="Test Person 4")
        caregiver = Caregiver(
            id="care_test_key_00000004",
            name="Test Caregiver 4",
            email="test4@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers={"Authorization": "Bearer care_test_key_00000004"},
            json={
                "caregiver_id": "care_test_key_00000004",
                "person_id": "person-4",
                "raw_input": "   ",
                "documents": [],
            },
        )
        assert response.status_code == 400
        assert "No valid evidence provided" in response.json()["error"]
    finally:
        teardown_db()


def test_post_situation_temporal_uncertainty():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-5", name="Test Person 5")
        caregiver = Caregiver(
            id="care_test_key_00000005",
            name="Test Caregiver 5",
            email="test5@example.com",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/situation",
            headers={"Authorization": "Bearer care_test_key_00000005"},
            json={
                "caregiver_id": "care_test_key_00000005",
                "person_id": "person-5",
                "raw_input": "The person seemed confused in the evening.",
                "timestamp": "maybe Tuesday evening",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

        db = TestingSessionLocal()
        event = db.query(CareEvent).filter(CareEvent.person_id == "person-5").first()
        assert event is not None
        assert event.occurred_at is None
        assert event.time_provenance == "submission_time_used:unparseable_timestamp:maybe Tuesday evening"
        db.close()
    finally:
        teardown_db()



def test_situation_rejects_unauthenticated_capture():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        response = client.post(
            "/api/situation",
            json={
                "caregiver_id": "caregiver-unauthenticated",
                "person_id": "person-unauthenticated",
                "raw_input": "This must not be stored.",
            },
        )
        assert response.status_code == 401
        db = TestingSessionLocal()
        try:
            assert db.query(Evidence).count() == 0
            assert db.query(CareEvent).count() == 0
        finally:
            db.close()
    finally:
        teardown_db()


def test_situation_rejects_a_person_outside_the_callers_care_context():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-owned-by-someone-else", name="Private Person")
        caregiver = Caregiver(
            id="care_test_key_00000006",
            name="Authorized Email",
            email="authorized@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        person_id = person.id
        caregiver_id = caregiver.id
        db.close()

        response = TestClient(app).post(
            "/api/situation",
            headers={"Authorization": f"Bearer {caregiver_id}"},
            json={
                "caregiver_id": caregiver_id,
                "person_id": "different-person-id",
                "raw_input": "This must not be stored.",
            },
        )
        assert response.status_code == 403
        db = TestingSessionLocal()
        try:
            assert db.query(Evidence).filter(Evidence.person_id == person_id).count() == 0
            assert db.query(CareEvent).filter(CareEvent.person_id == person_id).count() == 0
        finally:
            db.close()
    finally:
        teardown_db()


def test_situation_rejects_spoofed_caregiver_attribution():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-shared-context", name="Shared Person")
        actual_caregiver = Caregiver(
            id="care_test_key_00000007",
            name="Authenticated Caregiver",
            email="actual@example.com",
            relationship="Family",
            person_id=person.id,
        )
        spoofed_caregiver = Caregiver(
            id="care_test_key_00000008",
            name="Other Caregiver",
            email="other@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, actual_caregiver, spoofed_caregiver])
        db.commit()
        person_id = person.id
        spoofed_caregiver_id = spoofed_caregiver.id
        db.close()

        response = TestClient(app).post(
            "/api/situation",
            headers={"Authorization": "Bearer care_test_key_00000007"},
            json={
                "caregiver_id": spoofed_caregiver_id,
                "person_id": person_id,
                "raw_input": "This must not be attributed to another caregiver.",
            },
        )
        assert response.status_code == 403
        db = TestingSessionLocal()
        try:
            assert db.query(Evidence).filter(Evidence.person_id == person_id).count() == 0
            assert db.query(CareEvent).filter(CareEvent.person_id == person_id).count() == 0
        finally:
            db.close()
    finally:
        teardown_db()
