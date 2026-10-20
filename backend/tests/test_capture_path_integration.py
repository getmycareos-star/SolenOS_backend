"""Integration tests for the caregiver capture path.

These tests verify persisted behavior, not merely that an endpoint returned 200.
The API uses the per-test in-memory database supplied by conftest.py.
"""
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.models.caregiver import Caregiver
from app.models.care_event import CareEvent
from app.models.evidence import Evidence
from app.models.identity import Person
from app.schemas.situation import SituationInput


def setup_person_and_caregiver(db: Session, email_suffix: str = ""):
    person = Person(name="Test Person")
    db.add(person)
    db.flush()

    caregiver = Caregiver(
        name="Test Caregiver",
        email=f"test{email_suffix}@example.com",
        relationship="family",
        person_id=person.id,
        timezone="UTC",
    )
    db.add(caregiver)
    db.commit()
    db.refresh(person)
    db.refresh(caregiver)

    return person, caregiver


def make_payload(person, caregiver, raw_input, timestamp=None):
    return SituationInput(
        caregiver_id=caregiver.id,
        person_id=person.id,
        raw_input=raw_input,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
    )


def test_post_situation_persists_evidence_and_event_with_provenance(db, client):
    person, caregiver = setup_person_and_caregiver(db, "_001")
    raw_input = "The person fell while getting out of bed."
    payload = make_payload(person, caregiver, raw_input)

    response = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {caregiver.id}"})
    assert response.status_code == 200, response.text
    data = response.json()

    # The response must identify the records that were actually persisted.
    assert data["person_id"] == person.id
    assert data["care_event_id"]
    assert data["evidence_id"]
    assert isinstance(data.get("events"), list)
    assert "situations" in data
    assert "what_changed" in data
    assert "attention_candidate" in data
    assert "follow_up_candidate" in data

    evidence = db.query(Evidence).filter(Evidence.id == data["evidence_id"]).one()
    assert evidence.person_id == person.id
    assert evidence.uploaded_by_caregiver_id == caregiver.id
    assert evidence.source_text == raw_input
    assert evidence.source_type == "caregiver_observation"
    assert evidence.evidence_status == "reported"

    event = db.query(CareEvent).filter(CareEvent.id == data["care_event_id"]).one()
    assert event.person_id == person.id
    assert event.created_by_caregiver_id == caregiver.id
    assert event.event_type.value == "observation"
    assert evidence.id in (event.evidence_ids or [])
    assert event.original_expression in (None, raw_input) or event.description == raw_input


def test_multiple_observations_persist_as_distinct_time_ordered_events(db, client):
    person, caregiver = setup_person_and_caregiver(db, "_002")
    base_time = datetime.now(timezone.utc) - timedelta(days=2)
    observations = [
        "The person fell while getting out of bed.",
        "Another fall occurred in the bathroom today.",
        "The person seems unsteady on their feet lately.",
    ]
    response_data = []

    for index, observation in enumerate(observations):
        event_time = base_time + timedelta(days=index)
        payload = make_payload(person, caregiver, observation, event_time.isoformat())
        response = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {caregiver.id}"})
        assert response.status_code == 200, f"Observation {index + 1} failed: {response.text}"
        response_data.append(response.json())

    events = (
        db.query(CareEvent)
        .filter(CareEvent.person_id == person.id)
        .order_by(CareEvent.event_time.asc())
        .all()
    )
    evidence_items = db.query(Evidence).filter(Evidence.person_id == person.id).all()

    assert len(events) == 3
    assert len({event.id for event in events}) == 3
    assert len(evidence_items) == 3
    assert len({item.id for item in evidence_items}) == 3
    assert [event.description for event in events] == observations
    assert all(event.evidence_ids for event in events)
    assert all(data["care_event_id"] for data in response_data)
    assert all(data["evidence_id"] for data in response_data)


def test_identical_retry_does_not_duplicate_event_or_evidence(db, client):
    person, caregiver = setup_person_and_caregiver(db, "_003")
    timestamp = datetime.now(timezone.utc).isoformat()
    raw_input = "Identical observation submitted twice."
    payload = make_payload(person, caregiver, raw_input, timestamp)
    body = payload.model_dump(mode="json")

    headers = {"Authorization": f"Bearer {caregiver.id}", "Idempotency-Key": "capture-retry-003"}
    first = client.post("/api/situation", json=body, headers=headers)
    retry = client.post("/api/situation", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text

    events = db.query(CareEvent).filter(CareEvent.person_id == person.id).all()
    evidence_items = db.query(Evidence).filter(Evidence.person_id == person.id).all()

    # Exact same actor, subject, text and event timestamp is a retry, not a
    # second occurrence. A genuinely new observation should have a new time.
    assert len(events) == 1
    assert len(evidence_items) == 1
    assert retry.json()["care_event_id"] == first.json()["care_event_id"]
    assert retry.json()["evidence_id"] == first.json()["evidence_id"]


def test_empty_input_returns_client_error(db, client):
    person, caregiver = setup_person_and_caregiver(db, "_004")
    payload = SituationInput(
        caregiver_id=caregiver.id,
        person_id=person.id,
        raw_input="   ",
    )
    response = client.post("/api/situation", json=payload.model_dump(mode="json"), headers={"Authorization": f"Bearer {caregiver.id}"})
    assert response.status_code == 400, response.text


def test_reusing_idempotency_key_for_different_content_is_rejected(db, client):
    person, caregiver = setup_person_and_caregiver(db, "_005")
    timestamp = datetime.now(timezone.utc).isoformat()
    headers = {"Authorization": f"Bearer {caregiver.id}", "Idempotency-Key": "capture-key-reuse-005"}

    first = client.post(
        "/api/situation",
        json=make_payload(
            person, caregiver, "The person fell beside the bed.", timestamp
        ).model_dump(mode="json"),
        headers=headers,
    )
    conflicting = client.post(
        "/api/situation",
        json=make_payload(
            person, caregiver, "The person did not fall; caregiver corrected the report.", timestamp
        ).model_dump(mode="json"),
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert conflicting.status_code == 409, conflicting.text
    assert "different capture request" in conflicting.text
    assert db.query(CareEvent).filter(CareEvent.person_id == person.id).count() == 1
    assert db.query(Evidence).filter(Evidence.person_id == person.id).count() == 1

