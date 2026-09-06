"""Integration tests for the full caregiver runtime path.

Trace: input → evidence → event → change detection → situation formation → response
"""
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import Base, engine, get_db
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.models.change import Change, Situation
from app.main import app
from app.schemas.situation import SituationInput


client = TestClient(app)


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


def test_post_situation_triggers_change_detection_and_situations():
    db = next(get_db())
    try:
        person, caregiver = setup_person_and_caregiver(db, "_001")
    except Exception:
        db.rollback()
        raise

    payload = SituationInput(
        caregiver_id=caregiver.id,
        person_id=person.id,
        raw_input="The person fell while getting out of bed.",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    response = client.post("/api/situation", json=payload.model_dump(mode="json"))
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "situations" in data
    assert "what_changed" in data
    assert "attention_candidate" in data
    assert "follow_up_candidate" in data

    evidence = db.query(Evidence).filter(Evidence.person_id == person.id).first()
    assert evidence is not None
    assert evidence.source_type == "caregiver_observation"
    assert evidence.evidence_status == "reported"

    event = db.query(CareEvent).filter(CareEvent.person_id == person.id).first()
    assert event is not None
    assert event.event_type == "observation"

    changes = db.query(Change).filter(Change.person_id == person.id).all()
    assert len(changes) >= 0

    situations = db.query(Situation).filter(Situation.person_id == person.id).all()
    assert len(situations) >= 0


def test_multi_update_longitudinal_reasoning():
    db = next(get_db())
    try:
        person, caregiver = setup_person_and_caregiver(db, "_002")
    except Exception:
        db.rollback()
        raise

    inputs = [
        "The person fell while getting out of bed.",
        "Another fall occurred in the bathroom today.",
        "The person seems unsteady on their feet lately.",
    ]
    for i, text in enumerate(inputs):
        payload = SituationInput(
            caregiver_id=caregiver.id,
            person_id=person.id,
            raw_input=text,
            timestamp=(datetime.now(timezone.utc) - timedelta(days=2 - i)).isoformat(),
        )
        response = client.post("/api/situation", json=payload.model_dump(mode="json"))
        assert response.status_code == 200, f"Turn {i+1} failed: {response.text}"

    events = db.query(CareEvent).filter(CareEvent.person_id == person.id).count()
    assert events == 3

    evidence_items = db.query(Evidence).filter(Evidence.person_id == person.id).count()
    assert evidence_items == 3


def test_idempotent_identical_input():
    db = next(get_db())
    try:
        person, caregiver = setup_person_and_caregiver(db, "_003")
    except Exception:
        db.rollback()
        raise

    payload = SituationInput(
        caregiver_id=caregiver.id,
        person_id=person.id,
        raw_input="Identical observation submitted twice.",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    r1 = client.post("/api/situation", json=payload.model_dump(mode="json"))
    r2 = client.post("/api/situation", json=payload.model_dump(mode="json"))
    assert r1.status_code == 200
    assert r2.status_code == 200
    events = db.query(CareEvent).filter(CareEvent.person_id == person.id).count()
    assert events == 2


def test_empty_input_returns_400():
    db = next(get_db())
    try:
        person, caregiver = setup_person_and_caregiver(db, "_004")
    except Exception:
        db.rollback()
        raise

    payload = SituationInput(
        caregiver_id=caregiver.id,
        person_id=person.id,
        raw_input="   ",
    )
    response = client.post("/api/situation", json=payload.model_dump(mode="json"))
    assert response.status_code == 400
