"""API tests for the evidence-validated caregiver question projection."""
import hashlib
import json
import uuid
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.core.uncertainty_enums import Priority, GapLifecycleStatus, EpistemicState, GapReason
import app.main as main_module
from app.main import app
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.models.care_continuity import CareAssertion
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.uncertainty import OpenQuestion, InformationGap, Contradiction

pytestmark = pytest.mark.session_security


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
    main_module.SessionLocal = SessionLocal


def teardown():
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def client_for(person_id):
    token = f"question-projection-{person_id}-{uuid.uuid4()}"
    db = SessionLocal()
    caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
    db.add(AnonymousSession(
        person_id=person_id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    ))
    existing_grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == caregiver.id,
    ).first()
    if existing_grant is None:
        db.add(CaregiverAccessGrant(
            person_id=person_id,
            caregiver_id=caregiver.id,
            role="primary",
            access_scope="full_record",
            status="active",
            can_manage_members=True,
        ))
    db.commit()
    db.close()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def add_question(db, person_id, caregiver_id, question_id, priority, *, necessary=True, evidence_id=None, source_kind="document_extraction"):
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
        "source_kind": source_kind,
        "source_id": evidence_id,
        **({"source_record_refs": [{"record_type": "evidence", "record_id": evidence_id}]} if source_kind in {"document_extraction", "caregiver_report", "daily_capture_extraction"} else {}),
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
        intended_answerer_role="reporting_caregiver",
        necessity_rationale="A recorded care decision depends on this answer." if necessary else None,
        priority=Priority(priority),
        evidence_ids=[evidence_id],
        context=context,
    ))



def test_caregiver_report_question_requires_its_exact_persisted_evidence_source():
    setup()
    try:
        db = SessionLocal()
        person = Person(id="caregiver-report-person", name="Caregiver Report Person")
        caregiver = Caregiver(
            id="caregiver-report-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        add_question(
            db, person.id, caregiver.id, "caregiver-report-question", "high",
            evidence_id="caregiver-report-evidence", source_kind="caregiver_report",
        )
        db.commit()
        db.close()

        response = client_for("caregiver-report-person").get(
            "/api/v1/uncertainty/questions/recommended/caregiver-report-person"
        )
        assert response.status_code == 200, response.text
        assert "caregiver-report-question" in response.json()["eligible_question_ids"]
    finally:
        teardown()


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
                intended_answerer_role="reporting_caregiver",
                necessity_rationale="The next care decision depends on this missing detail.",
                priority=Priority.HIGH, evidence_ids=["gap-evidence"],
                context=[{
                    "source_kind": "information_gap", "source_id": gap_id,
                    "necessity_assessment": {
                        "necessity_rationale": "The next care decision depends on this missing detail.",
                        "decision_dependency": "The handoff cannot be completed until this gap is clarified.",
                        "evidence_ids": [] if gap_id == "gap-open" else ["gap-evidence"],
                    },
                }],
            ))
        db.add(OpenQuestion(
            id="question-gap-wrong-subject", person_id=person.id,
            question="What is the source-backed status for the wrong subject?",
            subject_type="care", subject_id="not-the-gap-subject", status="open",
            necessity="necessary",
            intended_answerer_role="reporting_caregiver",
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
        assert "question-gap-open" not in ids
        assert "question-gap-reopened" in ids
        assert "question-gap-resolved" not in ids
        assert "question-gap-wrong-subject" not in ids
    finally:
        teardown()

def test_generator_selects_only_explicit_evidence_backed_care_scenarios():
    setup()
    try:
        from app.services.care_continuity import (
            next_best_questions,
            has_caregiver_visible_answerer,
            has_valid_caregiver_necessity,
        )
        db = SessionLocal()
        person = Person(id="generator-person", name="Generator Person")
        other_person = Person(id="generator-other-person", name="Other Person")
        caregiver = Caregiver(
            id="generator-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        other_caregiver = Caregiver(
            id="generator-other-caregiver", name="Other Person Caregiver", email=None,
            relationship="Family", person_id=other_person.id,
        )
        db.add_all([person, other_person, caregiver, other_caregiver])
        db.flush()
        for index in range(20):
            evidence = Evidence(
                id=f"generator-med-evidence-{index:02d}", person_id=person.id,
                type="document", source_text="Conflicting current medication instruction.",
                uploaded_by_caregiver_id=caregiver.id, source_type="document",
            )
            db.add_all([
                evidence,
                Contradiction(
                    id=f"generator-med-conflict-{index:02d}", person_id=person.id,
                    subject_type="medication", subject_id=f"medication-{index:02d}",
                    field="current_instruction",
                    description="Two saved sources disagree about the current instruction.",
                    evidence_ids=[evidence.id], source_assertion_ids=[],
                    resolution_status="unresolved",
                ),
            ])
        for index in range(10):
            evidence = Evidence(
                id=f"generator-mobility-evidence-{index:02d}", person_id=person.id,
                type="note", source_text="Two reports differ about walking stability.",
                uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
            )
            db.add_all([
                evidence,
                Contradiction(
                    id=f"generator-mobility-conflict-{index:02d}", person_id=person.id,
                    subject_type="mobility", subject_id=f"mobility-{index:02d}",
                    field="walking_stability",
                    description="Two reports differ about walking stability.",
                    evidence_ids=[evidence.id], source_assertion_ids=[],
                    resolution_status="unresolved",
                ),
            ])
        for index in range(10):
            evidence = Evidence(
                id=f"generator-gap-evidence-{index:02d}", person_id=person.id,
                type="note", source_text="Current transfer support is recorded.",
                uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
            )
            db.add_all([
                evidence,
                InformationGap(
                    id=f"generator-explicit-gap-{index:02d}", person_id=person.id,
                    subject_type="mobility", subject_id=f"transfer-{index:02d}",
                    field=f"transfer_support_{index:02d}",
                    description="The current transfer support needs confirmation.",
                    evidence_ids=[evidence.id], lifecycle_status=GapLifecycleStatus.OPEN,
                    priority=Priority.HIGH,
                    context=[{
                        "caregiver_attention_required": True,
                        "necessity_rationale": "The next handoff depends on this detail.",
                        "decision_dependency": "The receiving caregiver cannot follow the handoff without this detail.",
                    }],
                ),
            ])
        for index in range(5):
            evidence = Evidence(
                id=f"generator-unassessed-evidence-{index:02d}", person_id=person.id,
                type="note", source_text="An unassessed detail is present.",
                uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
            )
            db.add_all([
                evidence,
                InformationGap(
                    id=f"generator-unassessed-gap-{index:02d}", person_id=person.id,
                    subject_type="mobility", subject_id=f"unassessed-{index:02d}",
                    field=f"unassessed_field_{index:02d}",
                    description="This information has not been assessed for caregiver necessity.",
                    evidence_ids=[evidence.id], lifecycle_status=GapLifecycleStatus.OPEN,
                    priority=Priority.HIGH, context=[],
                ),
            ])
        for index in range(5):
            other_evidence = Evidence(
                id=f"generator-cross-person-evidence-{index:02d}", person_id=other_person.id,
                type="note", source_text="Another person care detail.",
                uploaded_by_caregiver_id=other_caregiver.id,
                source_type="caregiver_report",
            )
            db.add_all([
                other_evidence,
                InformationGap(
                    id=f"generator-cross-person-gap-{index:02d}", person_id=person.id,
                    subject_type="mobility", subject_id=f"cross-person-{index:02d}",
                    field=f"cross_person_field_{index:02d}",
                    description="A source from a different care record is not valid here.",
                    evidence_ids=[other_evidence.id], lifecycle_status=GapLifecycleStatus.OPEN,
                    priority=Priority.HIGH,
                    context=[{
                        "caregiver_attention_required": True,
                        "necessity_rationale": "A handoff depends on this detail.",
                        "decision_dependency": "The receiving caregiver needs this detail.",
                    }],
                ),
            ])
        db.flush()
        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        persisted = db.query(OpenQuestion).filter_by(person_id=person.id).all()
        assert len(candidates) == 50
        assert len(persisted) == 30
        assert sum(row.intended_answerer_role == "professional" for row in persisted) == 20
        assert sum(row.intended_answerer_role == "reporting_caregiver" for row in persisted) == 10
        assert all(has_caregiver_visible_answerer(row) for row in persisted)
        assert all(has_valid_caregiver_necessity(row, db, person.id) for row in persisted)
        assert all(row.context and row.context[0].get("source_field") for row in persisted)
        assert not db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.subject_id.like("mobility-%"),
        ).count()
        assert not db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.subject_id.like("unassessed-%"),
        ).count()
        assert not db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.subject_id.like("cross-person-%"),
        ).count()
        db.rollback()
        db.close()
    finally:
        teardown()

def test_recommended_projection_requires_an_explicit_known_answerer():
    setup()
    try:
        db = SessionLocal()
        person = Person(id="answerer-person", name="Answerer Person")
        caregiver = Caregiver(
            id="answerer-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        add_question(db, person.id, caregiver.id, "professional-question", "critical")
        add_question(db, person.id, caregiver.id, "unknown-answerer-question", "high")
        add_question(db, person.id, caregiver.id, "other-caregiver-question", "medium")
        add_question(db, person.id, caregiver.id, "reporting-caregiver-question", "low")
        add_question(db, person.id, caregiver.id, "care-recipient-question", "high")
        add_question(db, person.id, caregiver.id, "mismatched-assessment-question", "high")
        add_question(db, person.id, caregiver.id, "mislinked-evidence-question", "high", evidence_id="mislinked-source-evidence")
        db.add(Evidence(
            id="unrelated-same-person-evidence", person_id=person.id,
            type="caregiver_report", source_text="Unrelated source record",
            uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
        ))
        db.flush()
        mismatched = db.query(OpenQuestion).filter_by(id="mismatched-assessment-question").one()
        mismatched.context = [{
            **mismatched.context[0],
            "necessity_assessment": {
                **mismatched.context[0]["necessity_assessment"],
                "necessity": "not_assessed",
            },
        }]
        mislinked = db.query(OpenQuestion).filter_by(id="mislinked-evidence-question").one()
        mislinked.context = [{
            **mislinked.context[0],
            "necessity_assessment": {
                **mislinked.context[0]["necessity_assessment"],
                "evidence_ids": ["unrelated-same-person-evidence"],
            },
        }]
        db.query(OpenQuestion).filter_by(id="professional-question").one().intended_answerer_role = "professional"
        db.query(OpenQuestion).filter_by(id="unknown-answerer-question").one().intended_answerer_role = "not_known"
        db.query(OpenQuestion).filter_by(id="other-caregiver-question").one().intended_answerer_role = "other_caregiver"
        db.query(OpenQuestion).filter_by(id="care-recipient-question").one().intended_answerer_role = "person_receiving_care"
        db.query(OpenQuestion).filter_by(id="professional-question").one().intended_answerer_label = "prescribing clinician"
        db.commit()
        db.close()

        response = client_for("answerer-person").get(
            "/api/v1/uncertainty/questions/recommended/answerer-person"
        )
        assert response.status_code == 200, response.text
        ids = set(response.json()["eligible_question_ids"])
        assert ids == {"professional-question", "other-caregiver-question", "reporting-caregiver-question", "care-recipient-question"}
        assert "unknown-answerer-question" not in ids
        assert "mismatched-assessment-question" not in ids
        assert "mislinked-evidence-question" not in ids
        metrics = client_for("answerer-person").get("/api/v1/uncertainty/question-quality")
        assert metrics.status_code == 200, metrics.text
        assert metrics.json()["caregiver_actionable_questions"] == 4
        assert metrics.json()["questions_awaiting_answerer_assignment"] == 1
        assert metrics.json()["non_actionable_active_questions"] == 3
        listed = client_for("answerer-person").get("/api/v1/uncertainty/questions/answerer-person")
        assert listed.status_code == 200, listed.text
        assert {item["id"] for item in listed.json()} == ids
    finally:
        teardown()


def test_reworded_active_question_for_the_same_care_field_is_updated_not_duplicated():
    setup()
    try:
        from app.services.care_continuity import next_best_questions
        db = SessionLocal()
        person = Person(id="active-topic-person", name="Active Topic Person")
        caregiver = Caregiver(
            id="active-topic-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        evidence = Evidence(
            id="active-topic-evidence", person_id=person.id,
            type="document", source_text="Current medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        db.add_all([person, caregiver, evidence])
        db.flush()
        rationale = "The current medication instruction still needs professional confirmation."
        existing = OpenQuestion(
            id="active-topic-question", person_id=person.id,
            question="What instruction is currently on file for this medication?",
            subject_type="medication", subject_id="medication-1",
            status="open", necessity="necessary", necessity_rationale=rationale,
            intended_answerer_role="professional", evidence_ids=[evidence.id],
            context=[{
                "source_kind": "contradiction",
                "source_id": "active-topic-old-conflict",
                "source_ids": ["active-topic-old-conflict"],
                "source_field": "current_instruction",
                "necessity_assessment": {
                    "necessity": "necessary",
                    "necessity_rationale": rationale,
                    "decision_dependency": "The family needs professional confirmation before relying on the instruction.",
                    "evidence_ids": [evidence.id],
                },
            }],
        )
        conflict = Contradiction(
            id="active-topic-new-conflict", person_id=person.id,
            subject_type="medication", subject_id="medication-1",
            field="current_instruction",
            description="The same instruction was imported from a second source.",
            evidence_ids=[evidence.id], source_assertion_ids=[],
            resolution_status="unresolved",
        )
        db.add_all([existing, conflict])
        db.flush()
        next_best_questions(db, person.id, persist=True, commit=False)
        questions = db.query(OpenQuestion).filter_by(person_id=person.id).all()
        assert len(questions) == 1
        assert questions[0].id == existing.id
        assert questions[0].status == "open"
        assert questions[0].question == "Can the prescribing clinician or pharmacist confirm the current instruction for this medication?"
        assert {entry["source_id"] for entry in questions[0].context} == {
            "active-topic-old-conflict", "active-topic-new-conflict",
        }
        db.rollback()
        db.close()
    finally:
        teardown()

def test_answered_question_is_not_repeated_from_a_new_source_row_for_the_same_field():
    setup()
    try:
        from app.services.care_continuity import next_best_questions
        db = SessionLocal()
        person = Person(id="no-repeat-person", name="No Repeat Person")
        caregiver = Caregiver(
            id="no-repeat-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        evidence = Evidence(
            id="no-repeat-evidence", person_id=person.id,
            type="document", source_text="Current medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        db.add_all([person, caregiver, evidence])
        db.flush()
        rationale = "The current medication instruction was already confirmed by the care team."
        answered = OpenQuestion(
            id="no-repeat-answered-question", person_id=person.id,
            question="What instruction is currently on file for this medication?",
            subject_type="medication", subject_id="medication-1",
            status="answered", necessity="necessary",
            necessity_rationale=rationale, intended_answerer_role="professional",
            evidence_ids=[evidence.id], answer="The pharmacist confirmed the current instruction.",
            answer_provenance="Caregiver spoke with the pharmacist.",
            answered_by_caregiver_id=caregiver.id, answered_at=datetime.now(timezone.utc),
            context=[{
                "source_kind": "contradiction",
                "source_id": "prior-medication-conflict",
                "source_ids": ["prior-medication-conflict"],
                "source_field": "current_instruction",
                "necessity_assessment": {
                    "necessity": "necessary",
                    "necessity_rationale": rationale,
                    "decision_dependency": "The family needed confirmation before relying on the instruction.",
                    "evidence_ids": [evidence.id],
                },
            }],
        )
        db.add_all([
            answered,
            Contradiction(
                id="new-medication-conflict-row", person_id=person.id,
                subject_type="medication", subject_id="medication-1",
                field="current_instruction",
                description="The same medication instruction was re-imported as a new conflict row.",
                evidence_ids=[evidence.id], source_assertion_ids=[],
                resolution_status="unresolved",
            ),
        ])
        db.flush()
        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        questions = db.query(OpenQuestion).filter_by(person_id=person.id).all()
        assert len(questions) == 1
        assert questions[0].id == answered.id
        assert questions[0].status == "answered"
        assert questions[0].answer == "The pharmacist confirmed the current instruction."
        assert candidates[0]["persisted_question_id"] == answered.id
        new_evidence = Evidence(
            id="no-repeat-new-evidence", person_id=person.id,
            type="document", source_text="A newly issued medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        db.add(new_evidence)
        db.flush()
        conflict = db.query(Contradiction).filter_by(id="new-medication-conflict-row").one()
        conflict.evidence_ids = [evidence.id, new_evidence.id]
        db.flush()
        next_best_questions(db, person.id, persist=True, commit=False)
        refreshed_questions = db.query(OpenQuestion).filter_by(person_id=person.id).all()
        assert len(refreshed_questions) == 2
        reopened = next(row for row in refreshed_questions if row.status == "open")
        assert reopened.id != answered.id
        assert reopened.context[0]["prior_terminal_question_id"] == answered.id
        assert new_evidence.id in reopened.evidence_ids
        db.rollback()
        db.close()
    finally:
        teardown()

def test_new_conflict_evidence_can_ground_a_question_linked_to_an_older_claim():
    setup()
    try:
        from app.services.care_continuity import has_valid_caregiver_necessity
        db = SessionLocal()
        person = Person(id="new-conflict-person", name="New Conflict Person")
        caregiver = Caregiver(
            id="new-conflict-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        old_evidence = Evidence(
            id="new-conflict-old-evidence", person_id=person.id,
            type="document", source_text="Earlier medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        new_evidence = Evidence(
            id="new-conflict-new-evidence", person_id=person.id,
            type="document", source_text="New conflicting medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        claim = CareAssertion(
            id="new-conflict-claim", person_id=person.id,
            subject_type="medication", subject_id="medication-1",
            field="current_instruction", asserted_value="Earlier instruction",
            source_label="Caregiver", evidence_ids=[old_evidence.id],
        )
        conflict = Contradiction(
            id="new-conflict-record", person_id=person.id,
            subject_type="medication", subject_id="medication-1",
            field="current_instruction", description="A newer report conflicts with the earlier instruction.",
            evidence_ids=[new_evidence.id], source_assertion_ids=[claim.id],
            resolution_status="unresolved",
        )
        db.add_all([person, caregiver, old_evidence, new_evidence, claim, conflict])
        db.flush()
        rationale = "The current medication instruction remains unconfirmed."
        question = OpenQuestion(
            id="new-conflict-question", person_id=person.id,
            question="Can the prescribing clinician confirm the current medication instruction?",
            subject_type="medication", subject_id="medication-1",
            claim_id=claim.id, status="open", necessity="necessary",
            necessity_rationale=rationale, intended_answerer_role="professional",
            evidence_ids=[new_evidence.id],
            context=[{
                "source_kind": "contradiction",
                "source_id": conflict.id,
                "source_ids": [conflict.id],
                "necessity_assessment": {
                    "necessity_rationale": rationale,
                    "decision_dependency": "The care plan cannot rely on the current instruction until it is confirmed.",
                    "evidence_ids": [new_evidence.id],
                },
            }],
        )
        db.add(question)
        db.flush()
        assert set(claim.evidence_ids).isdisjoint(question.evidence_ids)
        assert has_valid_caregiver_necessity(question, db, person.id) is True
        db.rollback()
        db.close()
    finally:
        teardown()


def test_only_explicit_source_backed_information_gaps_enter_return_tasks():
    from app.services.care_continuity import explicit_caregiver_gap_assessment

    evidence_id = "return-gap-evidence"
    valid_context = {
        "caregiver_attention_required": True,
        "necessity_rationale": "The upcoming handoff depends on this missing detail.",
        "decision_dependency": "The care team cannot complete the handoff without this answer.",
    }
    valid_gap = SimpleNamespace(
        context=[valid_context],
        evidence_ids=[evidence_id],
    )
    assert explicit_caregiver_gap_assessment(valid_gap, {evidence_id}) is valid_context

    unassessed_gap = SimpleNamespace(
        context=[{**valid_context, "caregiver_attention_required": False}],
        evidence_ids=[evidence_id],
    )
    assert explicit_caregiver_gap_assessment(unassessed_gap, {evidence_id}) is None

    unlinked_gap = SimpleNamespace(
        context=[valid_context],
        evidence_ids=["evidence-not-in-this-care-record"],
    )
    assert explicit_caregiver_gap_assessment(unlinked_gap, {evidence_id}) is None

    missing_dependency_gap = SimpleNamespace(
        context=[{**valid_context, "decision_dependency": " "}],
        evidence_ids=[evidence_id],
    )
    assert explicit_caregiver_gap_assessment(missing_dependency_gap, {evidence_id}) is None


def test_duplicate_candidate_merge_preserves_the_selected_source_evidence_and_answerer():
    setup()
    try:
        from app.services.care_continuity import next_best_questions, has_valid_caregiver_necessity
        db = SessionLocal()
        person = Person(id="dedupe-source-person", name="Dedupe Source Person")
        caregiver = Caregiver(
            id="dedupe-source-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        first_evidence = Evidence(
            id="dedupe-source-evidence-a", person_id=person.id,
            type="document", source_text="First medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        second_evidence = Evidence(
            id="dedupe-source-evidence-b", person_id=person.id,
            type="document", source_text="Conflicting medication instruction",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        db.add_all([person, caregiver, first_evidence, second_evidence])
        db.flush()
        db.add_all([
            Contradiction(
                id="dedupe-medication-conflict-a", person_id=person.id,
                subject_type="medication", subject_id="medication-1",
                field="current_instruction", description="First current instruction report.",
                evidence_ids=[first_evidence.id], source_assertion_ids=[],
                resolution_status="unresolved",
            ),
            Contradiction(
                id="dedupe-medication-conflict-b", person_id=person.id,
                subject_type="medication", subject_id="medication-1",
                field="current_instruction", description="Conflicting current instruction report.",
                evidence_ids=[second_evidence.id], source_assertion_ids=[],
                resolution_status="unresolved",
            ),
        ])
        db.flush()
        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        question = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.subject_type == "medication",
            OpenQuestion.subject_id == "medication-1",
        ).one()
        assert question.intended_answerer_role == "professional"
        assert set(question.evidence_ids) == {first_evidence.id, second_evidence.id}
        assert len(question.context) == 2
        contexts_by_source = {entry["source_id"]: entry for entry in question.context}
        assert set(contexts_by_source) == {
            "dedupe-medication-conflict-a", "dedupe-medication-conflict-b",
        }
        assert contexts_by_source["dedupe-medication-conflict-a"]["necessity_assessment"]["evidence_ids"] == [first_evidence.id]
        assert contexts_by_source["dedupe-medication-conflict-b"]["necessity_assessment"]["evidence_ids"] == [second_evidence.id]
        assert has_valid_caregiver_necessity(question, db, person.id) is True
        assert sum(1 for item in candidates if item.get("persisted_question_id") == question.id) == 1
        db.rollback()
        db.close()
    finally:
        teardown()

def test_one_hundred_case_question_gate_benchmark():
    """100 deterministic synthetic cases: 38 eligible, 62 must be withheld."""
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
                "evidence_ids": [] if 38 <= index < 40 else [evidence_id],
            }
            if 35 <= index < 40:
                event_id = f"benchmark-event-{index:03d}"
                db.add(CareEvent(
                    id=event_id, person_id=person.id,
                    event_type="daily_capture", status="recorded",
                    occurred_at=datetime.now(timezone.utc),
                    title=f"Synthetic event {index}", description="Synthetic daily-care observation.",
                    evidence_ids=[] if 38 <= index < 40 else [evidence_id], created_by_caregiver_id=caregiver.id,
                ))
                source_context = {
                    "source_kind": "daily_capture_extraction",
                    "source_id": event_id,
                    "source_record_refs": [{"record_type": "care_event", "record_id": event_id}],
                }
            elif 30 <= index < 35:
                source_context = {
                    "source_kind": "caregiver_report",
                    "source_id": evidence_id,
                }
            elif source_bound:
                source_context = {
                    "source_kind": "document_extraction",
                    "source_id": evidence_id,
                    "source_record_refs": [{"record_type": "evidence", "record_id": evidence_id}],
                }
            else:
                source_context = {}
            context = [{**source_context, "necessity_assessment": assessment}]
            priority = Priority.CRITICAL if index < 10 else Priority.HIGH
            db.add(OpenQuestion(
                id=question_id,
                person_id=person.id,
                question=f"What is the source-backed status for synthetic case {index}?",
                subject_type="care",
                subject_id=f"benchmark-subject-{index}",
                status="open",
                necessity="necessary" if necessary else "not_assessed",
                intended_answerer_role=(
                    "professional" if index < 10 else
                    "other_caregiver" if index < 20 else
                    "person_receiving_care" if index < 30 else
                    "reporting_caregiver"
                ),
                necessity_rationale=assessment["necessity_rationale"],
                priority=priority,
                evidence_ids=[evidence_id],
                context=context,
            ))
            if index < 38:
                expected_eligible.add(question_id)
        duplicate_evidence_id = "benchmark-evidence-duplicate"
        db.add(Evidence(
            id=duplicate_evidence_id, person_id=person.id,
            type="caregiver_report", source_text="Duplicate synthetic source",
            uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
        ))
        db.add(OpenQuestion(
            id="benchmark-question-duplicate", person_id=person.id,
            question="What is the source-backed status for synthetic case 34?",
            subject_type="care", subject_id="benchmark-subject-34",
            status="open", necessity="necessary",
            intended_answerer_role="reporting_caregiver",
            necessity_rationale="A recorded care decision depends on this answer.",
            priority=Priority.LOW, evidence_ids=[duplicate_evidence_id],
            context=[{
                "source_kind": "document_extraction",
                "source_id": duplicate_evidence_id,
                "source_record_refs": [{"record_type": "evidence", "record_id": duplicate_evidence_id}],
                "necessity_assessment": {
                    "necessity_rationale": "A recorded care decision depends on this answer.",
                    "decision_dependency": "The next handoff step depends on this answer.",
                    "evidence_ids": [duplicate_evidence_id],
                },
            }],
        ))
        db.commit()
        db.close()

        client = client_for("benchmark-person")
        response = client.get(
            "/api/v1/uncertainty/questions/recommended/benchmark-person"
        )
        assert response.status_code == 200, response.text
        result = response.json()
        listed = client.get("/api/v1/uncertainty/questions/benchmark-person")
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == result["eligible_question_ids"]
        assert {
            item["intended_answerer_role"] for item in listed.json()
        } == {
            "professional", "other_caregiver",
            "person_receiving_care", "reporting_caregiver",
        }
        predicted_eligible = set(result["eligible_question_ids"])
        true_positives = len(predicted_eligible.intersection(expected_eligible))
        precision = true_positives / max(1, len(predicted_eligible))
        recall = true_positives / max(1, len(expected_eligible))
        print("QUESTION_SELECTION_BENCHMARK=" + json.dumps({
            "cases_total": 100,
            "expected_eligible": len(expected_eligible),
            "predicted_eligible": len(predicted_eligible),
            "precision": precision,
            "recall": recall,
            "answerer_roles_preserved": {
                item["intended_answerer_role"] for item in listed.json()
            } == {
                "professional", "other_caregiver",
                "person_receiving_care", "reporting_caregiver",
            },
            "unknown_answerer_suppressed": all(
                item["intended_answerer_role"] != "not_known"
                for item in listed.json()
            ),
            "critical_preserved": result["critical_count"],
            "ordinary_initial_limit": result["ordinary_visible_count"],
            "duplicate_suppressed": "benchmark-question-duplicate" not in result["eligible_question_ids"],
            "cross_person_evidence_rejected": all(
                f"benchmark-question-{index:03d}" not in predicted_eligible
                for index in range(80, 100)
            ),
        }, sort_keys=True))
        assert precision == 1.0
        assert recall == 1.0
        assert predicted_eligible == expected_eligible
        assert len(result["eligible_question_ids"]) == 38
        assert "benchmark-question-duplicate" not in result["eligible_question_ids"]
        assert result["critical_count"] == 10
        assert len(result["recommended_question_ids"]) == 12
        assert result["additional_ordinary_count"] == 26
        assert [item["priority"] for item in result["questions"]] == ["critical"] * 10 + ["high"] * 2
        assert all(item["id"] in expected_eligible for item in result["questions"])
    finally:
        teardown()

def test_delta_topic_medication_conflict_stays_internal_without_instruction_field():
    setup()
    try:
        from app.services.care_continuity import next_best_questions

        db = SessionLocal()
        person = Person(id="delta-med-topic-person", name="Delta Medication Topic")
        caregiver = Caregiver(
            id="delta-med-topic-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        evidence = Evidence(
            id="delta-med-topic-evidence", person_id=person.id,
            type="caregiver_report",
            source_text="Historical medication evidence is incomplete.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        )
        contradiction = Contradiction(
            id="delta-med-topic-contradiction",
            person_id=person.id,
            subject_type="delta_engine_report",
            subject_id="medication",
            field="medication",
            description="Historical medication evidence remains uncertain.",
            evidence_ids=[evidence.id],
            source_assertion_ids=[],
            resolution_status="unresolved",
        )
        db.add_all([person, caregiver, evidence, contradiction])
        db.flush()

        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        candidate = next(
            item for item in candidates
            if item.get("source_id") == contradiction.id
        )
        assert candidate["necessity"] == "not_assessed"
        assert db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).count() == 0
        db.close()
    finally:
        teardown()


def test_source_clarification_does_not_create_caregiver_work_without_valid_necessity_evidence():
    """Unassessed or cross-person gaps must not turn extraction wording into caregiver tasks."""
    from app.services.uncertainty import persist_source_clarification_questions

    setup()
    try:
        db = SessionLocal()
        person = Person(id="clarification-owner", name="Care Record Owner")
        other_person = Person(id="clarification-other", name="Other Care Record")
        caregiver = Caregiver(
            id="clarification-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        other_caregiver = Caregiver(
            id="clarification-other-caregiver", name="Other Caregiver", email=None,
            relationship="Family", person_id=other_person.id,
        )
        own_source = Evidence(
            id="clarification-own-source", person_id=person.id, type="document",
            source_text="The discharge document mentions medication but gives no confirmed dose.",
            uploaded_by_caregiver_id=caregiver.id, source_type="document",
        )
        foreign_evidence = Evidence(
            id="clarification-foreign-evidence", person_id=other_person.id, type="caregiver_report",
            source_text="Current medication dose is 10 mg.",
            uploaded_by_caregiver_id=other_caregiver.id, source_type="caregiver_report",
        )
        db.add_all([person, other_person, caregiver, other_caregiver, own_source, foreign_evidence])
        db.flush()
        db.add_all([
            InformationGap(
                id="clarification-unassessed-gap", person_id=person.id,
                subject_type="medication", subject_id="current-dose", field="current medication dose",
                description="Dose has not been assessed for caregiver necessity.",
                evidence_ids=[own_source.id], lifecycle_status=GapLifecycleStatus.OPEN,
                priority=Priority.HIGH, context=[],
            ),
            InformationGap(
                id="clarification-cross-person-gap", person_id=person.id,
                subject_type="medication", subject_id="current-dose", field="current medication dose",
                description="The linked evidence belongs to another care record.",
                evidence_ids=[foreign_evidence.id], lifecycle_status=GapLifecycleStatus.OPEN,
                priority=Priority.HIGH, context=[{
                    "caregiver_attention_required": True,
                    "necessity_rationale": "A handoff depends on this detail.",
                    "decision_dependency": "The receiving caregiver needs this detail.",
                }],
            ),
        ])
        db.flush()

        persisted = persist_source_clarification_questions(
            db,
            person_id=person.id,
            caregiver_id=caregiver.id,
            source_kind="document_extraction",
            source_record_type="evidence",
            source_record_id=own_source.id,
            evidence_ids=[own_source.id],
            questions=["What is the current medication dose?"],
        )

        assert persisted == []
        assert db.query(OpenQuestion).filter(OpenQuestion.person_id == person.id).count() == 0
        db.rollback()
        db.close()
    finally:
        teardown()


def test_broad_delta_medication_topic_does_not_create_caregiver_question():
    setup()
    try:
        from app.services.care_continuity import next_best_questions

        db = SessionLocal()
        person = Person(id="delta-medication-scope-person", name="Scope Person")
        caregiver = Caregiver(
            id="delta-medication-scope-caregiver", name="Caregiver", email=None,
            relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()

        for evidence_id, text in (
            ("delta-medication-topic-evidence", "Synthetic unresolved medication topic."),
            ("vague-medication-field-evidence", "Synthetic medication reference."),
            ("actionable-medication-evidence", "Two reports conflict about the current medication instruction."),
        ):
            db.add(Evidence(
                id=evidence_id,
                person_id=person.id,
                type="caregiver_report",
                source_text=text,
                uploaded_by_caregiver_id=caregiver.id,
                source_type="caregiver_report",
            ))

        db.add_all([
            Contradiction(
                id="delta-medication-topic-conflict",
                person_id=person.id,
                subject_type="delta_engine_report",
                subject_id="medication",
                field="medication",
                description="Broad Delta topic label; not a medication instruction field.",
                evidence_ids=["delta-medication-topic-evidence"],
                source_assertion_ids=[],
                resolution_status="unresolved",
            ),
            Contradiction(
                id="vague-medication-field-conflict",
                person_id=person.id,
                subject_type="medication",
                subject_id="medication-vague",
                field="medication",
                description="Medication is mentioned, but no actionable instruction field is identified.",
                evidence_ids=["vague-medication-field-evidence"],
                source_assertion_ids=[],
                resolution_status="unresolved",
            ),
            Contradiction(
                id="actionable-medication-conflict",
                person_id=person.id,
                subject_type="medication",
                subject_id="medication-actionable",
                field="current_instruction",
                description="Two source reports disagree about the current instruction.",
                evidence_ids=["actionable-medication-evidence"],
                source_assertion_ids=[],
                resolution_status="unresolved",
            ),
        ])
        db.flush()

        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        persisted = db.query(OpenQuestion).filter_by(person_id=person.id, status="open").all()

        assert len(persisted) == 1
        assert persisted[0].subject_id == "medication-actionable"
        assert persisted[0].necessity == "necessary"
        assert persisted[0].intended_answerer_role == "professional"
        assert {item["source_id"] for item in candidates if item["necessity"] == "necessary"} == {
            "actionable-medication-conflict"
        }
    finally:
        teardown()
