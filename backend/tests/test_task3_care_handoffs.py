from starlette.requests import Request
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app.core.database import Base
from app.models.care import Person, Caregiver
from app.models.care_handoff import CareHandoff, CareHandoffEvent
from app.schemas.care_handoff import CareHandoffCreate
from app.api.care_handoffs import (
    create_handoff,
    acknowledge_handoff,
    close_handoff,
    list_handoff_events,
)


def make_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def request_for(person_id, caregiver_id):
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/care-handoffs", "headers": []})
    request.state.scoped_person_id = person_id
    request.state.scoped_caregiver_id = caregiver_id
    return request


def seed_people(db):
    person = Person(id="p-handoff", name="Handoff Test")
    caregiver = Caregiver(
        id="c-handoff", name="Creator", email="creator@example.test",
        relationship="Family", person_id=person.id, timezone="UTC",
    )
    assigned = Caregiver(
        id="c-handoff-assigned", name="Next Caregiver", email="next@example.test",
        relationship="Family", person_id=person.id, timezone="UTC",
    )
    other_person = Person(id="p-handoff-other", name="Other Person")
    other_caregiver = Caregiver(
        id="c-handoff-other", name="Other Caregiver", email="other@example.test",
        relationship="Family", person_id=other_person.id, timezone="UTC",
    )
    db.add_all([person, caregiver, assigned, other_person, other_caregiver])
    db.commit()
    return person, caregiver, assigned, other_person, other_caregiver


def test_handoff_persists_owner_outstanding_items_and_audit_history():
    engine, db = make_db()
    try:
        person, caregiver, assigned, _, _ = seed_people(db)
        payload = CareHandoffCreate(
            person_id=person.id,
            assigned_to_caregiver_id=assigned.id,
            title="Evening handoff",
            summary="Review today's care and outstanding follow-up.",
            outstanding_items=["Confirm appointment"],
            recent_changes=["Follow-up appointment was rescheduled"],
            safety_concerns=["Report new symptoms to the care team"],
        )
        item = create_handoff(payload, request_for(person.id, caregiver.id), db)
        assert item.status == "open"
        assert item.created_by_caregiver_id == caregiver.id
        assert item.assigned_to_caregiver_id == assigned.id
        assert item.outstanding_items == ["Confirm appointment"]
        assert item.safety_concerns == ["Report new symptoms to the care team"]
        events = db.query(CareHandoffEvent).filter_by(handoff_id=item.id).all()
        assert len(events) == 1
        assert events[0].event_type == "created"
        assert events[0].actor_caregiver_id == caregiver.id
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_handoff_rejects_cross_person_and_unlinked_assignee():
    engine, db = make_db()
    try:
        person, caregiver, _, other_person, other_caregiver = seed_people(db)
        request = request_for(person.id, caregiver.id)
        wrong_person = CareHandoffCreate(
            person_id=other_person.id, title="Wrong person", summary="Must be denied"
        )
        with pytest.raises(HTTPException) as exc:
            create_handoff(wrong_person, request, db)
        assert exc.value.status_code == 403

        invalid_assignee = CareHandoffCreate(
            person_id=person.id,
            assigned_to_caregiver_id=other_caregiver.id,
            title="Invalid assignee",
            summary="Must be denied",
        )
        with pytest.raises(HTTPException) as exc:
            create_handoff(invalid_assignee, request, db)
        assert exc.value.status_code == 422
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_only_assigned_caregiver_can_acknowledge_and_status_changes_are_audited():
    engine, db = make_db()
    try:
        person, caregiver, assigned, _, _ = seed_people(db)
        item = create_handoff(
            CareHandoffCreate(
                person_id=person.id,
                assigned_to_caregiver_id=assigned.id,
                title="Morning handoff",
                summary="Outstanding care follow-up.",
            ),
            request_for(person.id, caregiver.id),
            db,
        )
        with pytest.raises(HTTPException) as exc:
            acknowledge_handoff(item.id, request_for(person.id, caregiver.id), db)
        assert exc.value.status_code == 403

        acknowledged = acknowledge_handoff(item.id, request_for(person.id, assigned.id), db)
        assert acknowledged.status == "acknowledged"
        assert acknowledged.acknowledged_by_caregiver_id == assigned.id
        assert acknowledged.acknowledged_at is not None

        # A repeated acknowledgement by the same caregiver is idempotent.
        again = acknowledge_handoff(item.id, request_for(person.id, assigned.id), db)
        assert again.status == "acknowledged"

        closed = close_handoff(item.id, request_for(person.id, assigned.id), db)
        assert closed.status == "closed"
        assert closed.closed_by_caregiver_id == assigned.id
        assert closed.closed_at is not None
        events = list_handoff_events(item.id, request_for(person.id, assigned.id), db)
        assert [event.event_type for event in events] == ["created", "acknowledged", "closed"]
        assert all(event.actor_caregiver_id for event in events)
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
