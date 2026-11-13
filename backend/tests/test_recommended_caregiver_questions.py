"""API tests for the evidence-validated caregiver question projection."""
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import Person, Caregiver, Evidence
from app.models.anonymous_session import AnonymousSession
from app.models.uncertainty import OpenQuestion


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def override_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def client_for(person_id):
    token = f"question-projection-{person_id}"
    db = SessionLocal()
    caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
    db.add(AnonymousSession(
        person_id=person_id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    db.commit()
    db.close()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def add_question(db, person_id, caregiver_id, question_id, priority, *, necessary=True, evidence_id=None):
    evidence_id = evidence_id or f"evidence-{question_id}"
    db.add(Evidence(
        id=evidence_id,
        person_id=person_id,
        type="caregiver_report",
        source_text=f"Persisted source for {question_id}",
        uploaded_by_caregiver_id=caregiver_id,
        source_type="caregiver_report",
    ))
    context = [{
        "source_kind": "caregiver_report",
        "source_id": evidence_id,
        "necessity_assessment": {
            "necessity": "necessary" if necessary else "not_assessed",
            "necessity_rationale": "A recorded care decision depends on this answer." if necessary else None,
            "decision_dependency": "The current handoff cannot be completed accurately without this answer." if necessary else None,
            "evidence_ids": [evidence_id],
        },
    }]
    db.add(OpenQuestion(
        id=question_id,
        person_id=person_id,
        question=f"What should the care team know about {question_id}?",
        subject_type="care",
        subject_id="current-plan",
        status="open",
        necessity="necessary" if necessary else "not_assessed",
        necessity_rationale="A recorded care decision depends on this answer." if necessary else None,
        priority=priority,
        evidence_ids=[evidence_id],
        context=context,
    ))


def test_recommended_projection_keeps_critical_and_caps_ordinary_questions():
    setup()
    try:
        db = SessionLocal()
        person = Person(id="projection-person", name="Projection Person")
        caregiver = Caregiver(
            id="projection-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        add_question(db, person.id, caregiver.id, "critical-1", "critical")
        add_question(db, person.id, caregiver.id, "high-1", "high")
        add_question(db, person.id, caregiver.id, "medium-1", "medium")
        add_question(db, person.id, caregiver.id, "low-1", "low")
        add_question(db, person.id, caregiver.id, "unassessed-1", "critical", necessary=False)
        db.commit()
        db.close()

        response = client_for("projection-person").get(
            "/api/v1/uncertainty/questions/recommended/projection-person"
        )
        assert response.status_code == 200, response.text
        result = response.json()
        ids = [item["id"] for item in result["questions"]]
        assert "critical-1" in ids
        assert "unassessed-1" not in ids
        assert len(ids) == 3
        assert result["critical_count"] == 1
        assert result["ordinary_visible_count"] == 2
        assert result["additional_ordinary_count"] == 1
        assert result["status"] == "questions_to_review"
    finally:
        teardown()


def test_recommended_projection_is_person_scoped_and_empty_is_explicit():
    setup()
    try:
        db = SessionLocal()
        person_a = Person(id="projection-a", name="Person A")
        caregiver_a = Caregiver(
            id="projection-caregiver-a", name="Caregiver A", email=None,
            relationship="Family", person_id=person_a.id,
        )
        person_b = Person(id="projection-b", name="Person B")
        caregiver_b = Caregiver(
            id="projection-caregiver-b", name="Caregiver B", email=None,
            relationship="Family", person_id=person_b.id,
        )
        db.add_all([person_a, caregiver_a, person_b, caregiver_b])
        db.flush()
        add_question(db, person_b.id, caregiver_b.id, "foreign-question", "critical")
        db.commit()
        db.close()

        response = client_for("projection-a").get(
            "/api/v1/uncertainty/questions/recommended/projection-b"
        )
        assert response.status_code == 403

        empty = client_for("projection-a").get(
            "/api/v1/uncertainty/questions/recommended/projection-a"
        )
        assert empty.status_code == 200, empty.text
        assert empty.json()["questions"] == []
        assert empty.json()["status"] == "nothing_to_ask"
    finally:
        teardown()
