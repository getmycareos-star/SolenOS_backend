from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.action import Action
from app.models.care_continuity import CareActionOutcome, CareAssertion
from app.models.memory import Observation
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
        medication_list = Evidence(
            person_id=person_id,
            type="medication_list",
            source_text="Current medication list: medicine A, one tablet daily (caregiver-provided list).",
            uploaded_by_caregiver_id=caregiver_id,
            time_provenance="document_date_unknown",
            source_type="document",
        )
        db.add(medication_list)
        assertion_evidence = Evidence(
            person_id=person_id,
            type="care_instruction",
            source_text="Use walker when walking.",
            uploaded_by_caregiver_id=caregiver_id,
            time_provenance="document_date_unknown",
            source_type="document",
        )
        db.add(assertion_evidence)
        db.flush()
        event = CareEvent(
            person_id=person_id, event_type="hospital_discharge", status="recorded",
            occurred_at=now - timedelta(hours=2), title="Hospital discharge",
            description="Returned home after discharge.", evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver_id, time_provenance="caregiver_reported",
        )
        appointment = Appointment(
            person_id=person_id, title="Primary care follow-up",
            scheduled_at=now + timedelta(days=2), provider="Primary care",
            preparation_notes="Discuss dizziness since discharge.",
            medication_questions=["Confirm current medication instructions"],
            status="scheduled", created_by_caregiver_id=caregiver_id,
            evidence_ids=[evidence.id],
        )
        transition = CareTransition(
            person_id=person_id, from_location="Hospital", to_location="Home",
            transition_type="hospital_discharge", triggered_by_event_id=None,
            occurred_at=now - timedelta(hours=2), time_provenance="caregiver_reported",
            evidence_ids=[evidence.id], notes="Returned home with discharge instructions.",
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([event, appointment, transition])
        db.flush()
        observation = Observation(
            person_id=person_id,
            observed_at=now - timedelta(hours=3),
            original_text="Dizziness was reported before discharge.",
            structured_data=[{"field": "symptom", "value": "dizziness"}],
            tags=["symptom"],
            time_provenance="caregiver_reported",
            evidence_ids=[evidence.id],
            caregiver_id=caregiver_id,
        )
        assertion = CareAssertion(
            person_id=person_id,
            subject_type="person",
            subject_id=person_id,
            field="mobility_support",
            asserted_value="Use walker when walking",
            assertion_kind="care_instruction",
            source_event_id=event.id,
            source_label="discharge instruction",
            source_text="Use walker when walking.",
            evidence_ids=[assertion_evidence.id],
            assertion_status="reported",
            observed_at=now - timedelta(hours=2),
            created_by_caregiver_id=caregiver_id,
        )
        db.add_all([observation, assertion])
        action = Action(
            person_id=person_id, source_evidence_id=evidence.id,
            source_passage="Call the clinic if dizziness worsens.",
            source_document_type="discharge_instruction", source_authority="provider",
            extraction_confidence=0.95, actor_type="caregiver", actor_id=caregiver_id,
            actor_label="Family caregiver", subject_type="patient", action_type="call",
            action_object="Call the clinic", normalized_action="Call the clinic if dizziness worsens",
            original_text="Call the clinic if dizziness worsens.", modality="conditional",
            condition_text="If dizziness worsens", status="active", is_explicit=True,
            is_medication_action=False, risk_tier="moderate", requires_confirmation=False,
            has_condition=True, is_recurring=False, created_by_caregiver_id=caregiver_id,
        )
        db.add(action)
        db.flush()
        outcome = CareActionOutcome(
            person_id=person_id,
            action_id=action.id,
            action_type="call",
            outcome_status="pending",
            notes="Call the clinic if dizziness worsens.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver_id,
        )
        db.add(outcome)
        db.commit()
        event_id, evidence_id, medication_list_id, assertion_evidence_id, action_id = event.id, evidence.id, medication_list.id, assertion_evidence.id, action.id
    finally:
        db.close()

    response = client.get(
        f"/api/v1/care-record/{person_id}?limit=100",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["person_id"] == person_id
    assert body["returned_count"] == 5
    assert {item["kind"] for item in body["timeline"]} == {
        "care_event", "appointment", "care_transition", "care_action", "evidence",
    }
    event_item = next(item for item in body["timeline"] if item["source_id"] == event_id)
    assert event_item["summary"] == "Returned home after discharge."
    assert event_item["evidence"][0]["id"] == evidence_id
    assert "call the clinic if dizziness worsens" in event_item["evidence"][0]["source_text"].lower()
    assert event_item["related_observations"][0]["original_text"] == "Dizziness was reported before discharge."
    assert event_item["assertions"][0]["asserted_value"] == "Use walker when walking"
    assert event_item["assertions"][0]["evidence"][0]["id"] == assertion_evidence_id
    assert event_item["assertions"][0]["evidence"][0]["source_text"] == "Use walker when walking."
    assert body["counts"]["observations"] == 1
    assert body["counts"]["care_assertions"] == 1
    assert body["counts"]["action_outcomes"] == 1
    action_item = next(item for item in body["timeline"] if item["source_id"] == action_id)
    assert action_item["outcomes"][0]["notes"] == "Call the clinic if dizziness worsens."
    medication_item = next(item for item in body["timeline"] if item["source_id"] == medication_list_id)
    assert medication_item["kind"] == "evidence"
    assert medication_item["title"] == "Medication List"
    assert "medicine A" in medication_item["summary"]
    appointment_item = next(item for item in body["timeline"] if item["kind"] == "appointment")
    assert appointment_item["details"]["medication_questions"] == [
        "Confirm current medication instructions"
    ]


def test_unified_care_record_requires_token_and_enforces_record_scope():
    client = TestClient(app)
    first = create_session(client, "First person")
    second = create_session(client, "Second person")
    person_id = first["person"]["id"]
    assert client.get(f"/api/v1/care-record/{person_id}").status_code == 401
    cross_record = client.get(
        f"/api/v1/care-record/{person_id}",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert cross_record.status_code == 403



def test_unified_care_record_paginates_without_repeating_items():
    client = TestClient(app)
    session = create_session(client)
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    now = datetime.now(timezone.utc)

    db = TestSessionLocal()
    try:
        db.add_all([
            CareEvent(
                person_id=person_id,
                event_type="observation",
                occurred_at=now - timedelta(hours=hours),
                title=f"Observation {hours}",
                description=f"Recorded observation {hours}",
                created_by_caregiver_id=caregiver_id,
            )
            for hours in (1, 2, 3)
        ])
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {session['access_token']}"}
    first = client.get(f"/api/v1/care-record/{person_id}?limit=2&offset=0", headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["timeline"]) == 2
    assert first_body["has_more"] is True

    second = client.get(f"/api/v1/care-record/{person_id}?limit=2&offset=2", headers=headers)
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["timeline"]) == 1
    assert second_body["has_more"] is False
    first_ids = {item["id"] for item in first_body["timeline"]}
    second_ids = {item["id"] for item in second_body["timeline"]}
    assert first_ids.isdisjoint(second_ids)


def test_unified_care_record_rejects_missing_person():
    client = TestClient(app)
    session = create_session(client)
    response = client.get(
        "/api/v1/care-record/missing-person",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 403, response.text
