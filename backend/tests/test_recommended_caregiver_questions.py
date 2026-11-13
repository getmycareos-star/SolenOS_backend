"""API tests for the evidence-validated caregiver question projection."""
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.uncertainty_enums import Priority, GapLifecycleStatus, EpistemicState, GapReason
from app.main import app
from app.models.care import Person, Caregiver, Evidence
from app.models.anonymous_session import AnonymousSession
from app.models.uncertainty import OpenQuestion, InformationGap


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
        "source_kind": "document_extraction",
        "source_id": evidence_id,
        "source_record_refs": [{"record_type": "evidence", "record_id": evidence_id}],
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
        priority=Priority(priority),
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



def test_recommended_projection_reassesses_gap_lifecycle_before_showing_questions():
    setup()
    try:
        db = SessionLocal()
        person = Person(id="gap-person", name="Gap Person")
        caregiver = Caregiver(
            id="gap-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        evidence = Evidence(
            id="gap-evidence", person_id=person.id, type="caregiver_report",
            source_text="A current care decision is waiting on this detail.",
            uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
        )
        db.add_all([person, caregiver, evidence])
        db.flush()
        for gap_id, lifecycle in (
            ("gap-open", GapLifecycleStatus.OPEN),
            ("gap-resolved", GapLifecycleStatus.RESOLVED),
            ("gap-reopened", GapLifecycleStatus.REOPENED),
        ):
            db.add(InformationGap(
                id=gap_id, person_id=person.id, subject_type="care",
                subject_id=gap_id, field="follow_up_status",
                epistemic_state=EpistemicState.UNKNOWN,
                gap_reason=GapReason.NOT_DOCUMENTED,
                priority=Priority.HIGH, lifecycle_status=lifecycle,
                evidence_ids=["gap-evidence"],
            ))
            db.add(OpenQuestion(
                id=f"question-{gap_id}", person_id=person.id,
                question=f"What is the source-backed status for {gap_id}?",
                subject_type="care", subject_id=gap_id, status="open",
                necessity="necessary",
                necessity_rationale="The next care decision depends on this missing detail.",
                priority=Priority.HIGH, evidence_ids=["gap-evidence"],
                context=[{
                    "source_kind": "information_gap", "source_id": gap_id,
                    "necessity_assessment": {
                        "necessity_rationale": "The next care decision depends on this missing detail.",
                        "decision_dependency": "The handoff cannot be completed until this gap is clarified.",
                        "evidence_ids": ["gap-evidence"],
                    },
                }],
            ))
        db.add(OpenQuestion(
            id="question-gap-wrong-subject", person_id=person.id,
            question="What is the source-backed status for the wrong subject?",
            subject_type="care", subject_id="not-the-gap-subject", status="open",
            necessity="necessary",
            necessity_rationale="The next care decision depends on this missing detail.",
            priority=Priority.HIGH, evidence_ids=["gap-evidence"],
            context=[{
                "source_kind": "information_gap", "source_id": "gap-open",
                "necessity_assessment": {
                    "necessity_rationale": "The next care decision depends on this missing detail.",
                    "decision_dependency": "The handoff cannot be completed until this gap is clarified.",
                    "evidence_ids": ["gap-evidence"],
                },
            }],
        ))
        db.commit()
        db.close()

        response = client_for("gap-person").get(
            "/api/v1/uncertainty/questions/recommended/gap-person"
        )
        assert response.status_code == 200, response.text
        ids = {item["id"] for item in response.json()["questions"]}
        assert "question-gap-open" in ids
        assert "question-gap-reopened" in ids
        assert "question-gap-resolved" not in ids
        assert "question-gap-wrong-subject" not in ids
    finally:
        teardown()

def test_one_hundred_case_question_gate_benchmark():
    """100 deterministic synthetic cases: 40 valid, 60 must be withheld."""
    setup()
    try:
        db = SessionLocal()
        person = Person(id="benchmark-person", name="Benchmark Person")
        caregiver = Caregiver(
            id="benchmark-caregiver", name="Benchmark Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        other = Person(id="benchmark-other", name="Other Person")
        other_caregiver = Caregiver(
            id="benchmark-other-caregiver", name="Other Caregiver", email=None,
            relationship="Family", person_id=other.id,
        )
        db.add_all([person, caregiver, other, other_caregiver])
        db.flush()

        expected_eligible = set()
        for index in range(100):
            question_id = f"benchmark-question-{index:03d}"
            evidence_id = f"benchmark-evidence-{index:03d}"
            if index < 80:
                evidence_person_id = person.id
                evidence_uploader_id = caregiver.id
            else:
                evidence_person_id = other.id
                evidence_uploader_id = other_caregiver.id
            db.add(Evidence(
                id=evidence_id,
                person_id=evidence_person_id,
                type="caregiver_report",
                source_text=f"Synthetic source record {index}",
                uploaded_by_caregiver_id=evidence_uploader_id,
                source_type="caregiver_report",
            ))
            necessary = index < 40 or 60 <= index < 80
            source_bound = index < 60 or index >= 80
            assessment = {
                "necessity": "necessary" if necessary else "not_assessed",
                "necessity_rationale": "A recorded care decision depends on this answer." if necessary else None,
                "decision_dependency": "The next handoff step depends on this answer." if necessary else None,
                "evidence_ids": [] if 35 <= index < 40 else [evidence_id],
            }
            context = [{
                **({"source_kind": "document_extraction", "source_id": evidence_id, "source_record_refs": [{"record_type": "evidence", "record_id": evidence_id}]} if source_bound else {}),
                "necessity_assessment": assessment,
            }]
            priority = Priority.CRITICAL if index < 10 else Priority.HIGH
            db.add(OpenQuestion(
                id=question_id,
                person_id=person.id,
                question=f"What is the source-backed status for synthetic case {index}?",
                subject_type="care",
                subject_id=f"benchmark-subject-{index}",
                status="open",
                necessity="necessary" if necessary else "not_assessed",
                necessity_rationale=assessment["necessity_rationale"],
                priority=priority,
                evidence_ids=[evidence_id],
                context=context,
            ))
            if index < 35:
                expected_eligible.add(question_id)
        db.commit()
        db.close()

        response = client_for("benchmark-person").get(
            "/api/v1/uncertainty/questions/recommended/benchmark-person"
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert set(result["eligible_question_ids"]) == expected_eligible
        assert len(result["eligible_question_ids"]) == 35
        assert result["critical_count"] == 10
        assert len(result["recommended_question_ids"]) == 12
        assert result["additional_ordinary_count"] == 23
        assert all(item["id"] in expected_eligible for item in result["questions"])
    finally:
        teardown()
