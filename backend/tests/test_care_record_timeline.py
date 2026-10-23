from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.action import Action
from app.models.care import CareEvent, Evidence
from app.models.time_location import Appointment, CareTransition

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def create_session(client: TestClient, name: str = "Care recipient"):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": name, "caregiver_name": "Family caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_unified_care_record_aggregates_domains_and_keeps_source_evidence():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    now = datetime.now(timezone.utc)

    db = TestSessionLocal()
    try:
        evidence = Evidence(
            person_id=person_id,
            type="discharge_instruction",
            source_text="Discharge note: call the clinic if dizziness worsens.",
            uploaded_by_caregiver_id=caregiver_id,
            time_provenance="document_date_unknown",
            source_type="document",
        )
        db.add(evidence)
        db.flush()

        event = CareEvent(
            person_id=person_id,
            event_type="hospital_discharge",
            status="recorded",
            occurred_at=now - timedelta(hours=2),
            title="Hospital discharge",
            description="Returned home after discharge.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver_id,
            time_provenance="caregiver_reported",
        )
        appointment = Appointment(
            person_id=person_id,
            title="Primary care follow-up",
            scheduled_at=now + timedelta(days=2),
            provider="Primary care",
            preparation_notes="Discuss dizziness since discharge.",
            medication_questions=["Confirm current medication instructions"],
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
            evidence_ids=[evidence.id],
        )
        transition = CareTransition(
            person_id=person_id,
            from_location="Hospital",
            to_location="Home",
            transition_type="hospital_discharge",
            triggered_by_event_id=None,
            occurred_at=now - timedelta(hours=2),
            time_provenance="caregiver_reported",
            evidence_ids=[evidence.id],
            notes="Returned home with discharge instructions.",
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([event, appointment, transition])
        db.flush()

        action = Action(
            person_id=person_id,
            source_evidence_id=evidence.id,
            source_passage="Call the clinic if dizziness worsens.",
            source_document_type="discharge_instruction",
            source_authority="provider",
            extraction_confidence=0.95,
            actor_type="caregiver",
            actor_id=caregiver_id,
            actor_label="Family caregiver",
            subject_type="patient",
            action_type="call",
            action_object="Call the clinic",
            normalized_action="Call the clinic if dizziness worsens",
            original_text="Call the clinic if dizziness worsens.",
            modality="conditional",
            condition_text="If dizziness worsens",
            status="active",
            is_explicit=True,
            is_medication_action=False,
            risk_tier="moderate",
            requires_confirmation=False,
            has_condition=True,
            is_recurring=False,
            created_by_caregiver_id=caregiver_id,
        )
        db.add(action)
        db.commit()
        event_id = event.id
        evidence_id = evidence.id
    finally:
        db.close()

    response = client.get(
        f"/api/v1/care-record/{person_id}?limit=100",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["person_id"] == person_id
    assert body["returned_count"] == 4
    assert {item["kind"] for item in body["timeline"]} == {
        "care_event",
        "appointment",
        "care_transition",
        "care_action",
    }

    event_item = next(item for item in body["timeline"] if item["source_id"] == event_id)
    assert event_item["summary"] == "Returned home after discharge."
    assert event_item["evidence"][0]["id"] == evidence_id
    assert "call the clinic if dizziness worsens" in event_item["evidence"][0]["source_text"].lower()

    appointment_item = next(item for item in body["timeline"] if item["kind"] == "appointment")
    assert appointment_item["details"]["medication_questions"] == [
        "Confirm current medication instructions"
    ]


def test_unified_care_record_requires_token_and_enforces_record_scope():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    first_person_id = first["person"]["id"]

    unauthenticated = client.get(f"/api/v1/care-record/{first_person_id}")
    assert unauthenticated.status_code == 401, unauthenticated.text

    cross_record = client.get(
        f"/api/v1/care-record/{first_person_id}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert cross_record.status_code == 403, cross_record.text


def test_unified_care_record_rejects_missing_person():
    client = TestClient(app)
    session = create_session(client)
    response = client.get(
        "/api/v1/care-record/missing-person",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 403, response.text
