from fastapi.testclient import TestClient
import pytest
import hashlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Person, Caregiver, Evidence, CareEvent
from app.models.action import Action
from app.models.care_continuity import CareAssertion
from app.models.anonymous_session import AnonymousSession
from app.models.collaboration import ConversationSession, ConversationMessage
from app.models.uncertainty import OpenQuestion, InformationGap, Contradiction
from app.core.uncertainty_enums import EpistemicState, GapReason, GapLifecycleStatus, Priority
from app.schemas.uncertainty import InformationGapUpdate

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()



def authorized_client(person_id: str) -> TestClient:
    """Issue a real test bearer capability scoped to an existing test person."""
    db = TestingSessionLocal()
    try:
        caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
        assert caregiver is not None, f"test caregiver missing for {person_id}"
        token = f"test-token-for-{person_id}"
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=caregiver.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
        db.commit()
    finally:
        db.close()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client

def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def test_create_information_gap():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-1", name="Test Person")
        caregiver = Caregiver(
            id="caregiver-1",
            name="Test Caregiver",
            email="test@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-1")
        response = client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-1",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "dose",
                "epistemic_state": "unknown",
                "gap_reason": "not_documented",
                "priority": "high",
                "description": "Medication dose is unknown",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["person_id"] == "person-1"
        assert data["field"] == "dose"
        assert data["epistemic_state"] == "unknown"
        assert data["gap_reason"] == "not_documented"
        assert data["lifecycle_status"] == "open"

        necessary_payload = {
            "person_id": "person-1",
            "subject_type": "medication",
            "subject_id": "med-1",
            "field": "current_dose",
            "epistemic_state": "unknown",
            "gap_reason": "not_documented",
            "priority": "high",
            "description": "The current dose is not established for the upcoming handoff.",
            "context": [{
                "caregiver_attention_required": True,
                "necessity_rationale": "The upcoming handoff requires the current prescribed dose.",
                "decision_dependency": "The medication handoff cannot accurately state the current dose without this source.",
            }],
        }
        missing_source = client.post("/api/v1/uncertainty/gaps", json=necessary_payload)
        assert missing_source.status_code == 422, missing_source.text

        db = TestingSessionLocal()
        db.add(Evidence(
            id="finding6-producer-evidence",
            person_id="person-1",
            type="document",
            source_text="Prescriber medication instruction document.",
            uploaded_by_caregiver_id="caregiver-1",
            source_type="document",
        ))
        db.commit()
        db.close()
        grounded = client.post(
            "/api/v1/uncertainty/gaps",
            json={**necessary_payload, "evidence_ids": ["finding6-producer-evidence"]},
        )
        assert grounded.status_code == 200, grounded.text

        db = TestingSessionLocal()
        from app.services.care_continuity import next_best_questions
        # Even a legacy/directly-written assessment marker cannot create a
        # caregiver request without persisted source evidence.
        ungrounded_gap = InformationGap(
            person_id="person-1",
            subject_type="sleep",
            subject_id="sleep-pattern",
            field="usual_sleep_schedule",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_DOCUMENTED,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="Usual sleep schedule is not documented.",
            context=[{
                "caregiver_attention_required": True,
                "necessity_rationale": "The next care decision depends on the sleep schedule.",
                "decision_dependency": "A documented schedule is required to evaluate the new care plan.",
            }],
            evidence_ids=["nonexistent-source-evidence"],
        )
        db.add(ungrounded_gap)
        db.flush()
        candidates = next_best_questions(db, "person-1", persist=True, commit=False)
        ungrounded_candidate = next(
            item for item in candidates
            if item["source_kind"] == "information_gap"
            and item["subject_id"] == "sleep-pattern"
        )
        assert ungrounded_candidate["necessity"] == "not_assessed"
        assert db.query(OpenQuestion).filter_by(
            person_id="person-1", subject_id="sleep-pattern"
        ).count() == 0
        assert any(
            item["source_kind"] == "information_gap"
            and item["necessity"] == "necessary"
            and item["decision_dependency"].startswith("The medication handoff")
            for item in candidates
        )
        necessary_question = db.query(OpenQuestion).filter_by(
            person_id="person-1", necessity="necessary"
        ).one()
        assert "finding6-producer-evidence" in necessary_question.evidence_ids
        gap_context = next(
            item for item in necessary_question.context
            if item.get("source_kind") == "information_gap"
        )
        assert gap_context["source_id"] == grounded.json()["id"]
        assert gap_context["necessity_assessment"]["evidence_ids"] == ["finding6-producer-evidence"]
        assert necessary_question.status == "open"

        # Updating the structured necessity assessment must suppress the
        # linked request immediately, without marking it answered.
        from app.services.uncertainty import UncertaintyEngine
        engine = UncertaintyEngine(db, person_id="person-1")
        updated_gap = engine.update_information_gap(
            grounded.json()["id"],
            InformationGapUpdate(context=[]),
        )
        assert updated_gap is not None
        db.refresh(necessary_question)
        assert necessary_question.status == "closed"
        assert necessary_question.closed_reason.startswith("automatically_suppressed")
        assert necessary_question.answered_at is None
        db.close()
    finally:
        teardown_db()


def test_next_question_projection_excludes_internal_gaps_and_unassessed_questions():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-f6-projection", name="Projection Person")
        caregiver = Caregiver(
            id="caregiver-f6-projection",
            name="Projection Caregiver",
            email="projection@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="evidence-f6-projection",
            person_id=person.id,
            type="document",
            source_text="Current discharge instruction used for the handoff.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        db.add_all([person, caregiver, evidence])
        db.flush()
        db.add_all([
            InformationGap(
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="preferred_pharmacy",
                epistemic_state=EpistemicState.UNKNOWN,
                gap_reason=GapReason.NOT_DOCUMENTED,
                priority=Priority.INFORMATIONAL,
                lifecycle_status=GapLifecycleStatus.OPEN,
                description="Preferred pharmacy has not been recorded.",
            ),
            OpenQuestion(
                person_id=person.id,
                question="Which discharge instruction applies to the current handoff?",
                subject_type="person",
                subject_id=person.id,
                status="open",
                necessity="necessary",
                necessity_rationale="The handoff decision depends on the active instruction.",
                priority=Priority.HIGH,
                evidence_ids=[evidence.id],
                context=[{
                    "source_kind": "document_extraction",
                    "source_id": evidence.id,
                    "source_record_refs": [{"record_type": "evidence", "record_id": evidence.id}],
                    "necessity_assessment": {
                        "necessity_rationale": "The handoff decision depends on the active instruction.",
                        "decision_dependency": "The handoff cannot identify the active instruction without clarification.",
                        "evidence_ids": [evidence.id],
                    },
                }],
            ),
            OpenQuestion(
                person_id=person.id,
                question="What is the preferred pharmacy?",
                subject_type="person",
                subject_id=person.id,
                status="open",
                necessity="not_assessed",
                priority=Priority.INFORMATIONAL,
            ),
            OpenQuestion(
                person_id=person.id,
                question="Which older source should be used for the handoff?",
                subject_type="person",
                subject_id=person.id,
                status="open",
                necessity="necessary",
                necessity_rationale="The handoff decision depends on the source.",
                priority=Priority.HIGH,
                evidence_ids=[evidence.id],
                # Evidence exists on the question, but the decision dependency
                # is not linked to a persisted source context.
                context=[{
                    "necessity_assessment": {
                        "necessity_rationale": "The handoff decision depends on the source.",
                        "decision_dependency": "The handoff needs a confirmed source.",
                    }
                }],
            ),
            OpenQuestion(
                person_id=person.id,
                question="Is the older medication dose still current?",
                subject_type="person",
                subject_id=person.id,
                status="open",
                necessity="necessary",
                necessity_rationale="Legacy request has no source evidence.",
                priority=Priority.HIGH,
                evidence_ids=[],
                context=[{"decision_dependency": "A current handoff depends on the dose."}],
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        response = client.post(f"/api/v1/care-continuity/next-questions/{person.id}")
        assert response.status_code == 200, response.text
        questions = response.json()
        assert [item["question"] for item in questions] == [
            "Which discharge instruction applies to the current handoff?"
        ]
        assert all(
            item["question"] != "Which older source should be used for the handoff?"
            for item in questions
        ), "A decision dependency without a source-bound context must not reach the caregiver."
    finally:
        teardown_db()


def test_accepted_current_state_evidence_resolves_gap_and_supersedes_question():
    setup_db()
    try:
        db = TestingSessionLocal()
        person = Person(id="person-gap-reconcile", name="Gap Reconcile Person")
        caregiver = Caregiver(
            id="caregiver-gap-reconcile",
            name="Gap Reconcile Caregiver",
            email="gap-reconcile@example.com",
            relationship="Family",
            person_id=person.id,
        )
        gap_source = Evidence(
            id="evidence-gap-reconcile-original",
            person_id=person.id,
            type="document",
            source_text="Earlier record notes that the prescribed dose is not documented.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        current_source = Evidence(
            id="evidence-gap-reconcile-current",
            person_id=person.id,
            type="document",
            source_text="Current prescriber instruction states the medication dose.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        gap = InformationGap(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-1",
            field="current_dose",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_DOCUMENTED,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="The current dose is not established for the care handoff.",
            evidence_ids=[gap_source.id],
            context=[{
                "caregiver_attention_required": True,
                "necessity_rationale": "The current handoff depends on the prescribed dose.",
                "decision_dependency": "The handoff cannot state the current dose without this source.",
            }],
        )
        stale_source = Evidence(
            id="evidence-gap-reconcile-stale",
            person_id=person.id,
            type="document",
            source_text="An old prescription record outside its review window.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        stale_gap = InformationGap(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-3",
            field="current_dose",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_DOCUMENTED,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="Current dose remains unestablished because only an expired source exists.",
            evidence_ids=[stale_source.id],
        )
        conflict_gap_source = Evidence(
            id="evidence-gap-reconcile-conflict",
            person_id=person.id,
            type="document",
            source_text="A separate medication field remains contested.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        conflict_gap = InformationGap(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-2",
            field="current_dose",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.CONFLICTING,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="The current dose has competing reports.",
            evidence_ids=[conflict_gap_source.id],
        )
        db.add_all([
            person, caregiver, gap_source, current_source, gap,
            stale_source, stale_gap, conflict_gap_source, conflict_gap,
        ])
        db.flush()

        from app.services.care_continuity import (
            next_best_questions,
            reconcile_information_gaps_from_accepted_assertions,
            reassess_open_question_lifecycle,
        )
        next_best_questions(db, person.id, persist=True, commit=False)
        question = db.query(OpenQuestion).filter_by(
            person_id=person.id, subject_id="medication-1", necessity="necessary"
        ).one()
        assert question.status == "open"

        accepted_assertion = CareAssertion(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-1",
            field="current_dose",
            assertion_kind="current_state",
            asserted_value="Dose stated in current prescriber instruction",
            source_label="Caregiver-reviewed current prescriber instruction",
            source_text="Current prescriber instruction states the medication dose.",
            evidence_ids=[current_source.id],
            assertion_status="reported",
            epistemic_state="reported",
            review_after_days=30,
            created_by_caregiver_id=caregiver.id,
            updated_by_caregiver_id=caregiver.id,
        )
        stale_assertion = CareAssertion(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-3",
            field="current_dose",
            assertion_kind="current_state",
            asserted_value="Old dose from an expired prescription",
            source_label="Historical prescription",
            source_text="An old prescription record outside its review window.",
            evidence_ids=[stale_source.id],
            assertion_status="accepted",
            epistemic_state="reported",
            observed_at=datetime.now(timezone.utc) - timedelta(days=60),
            review_after_days=30,
            created_by_caregiver_id=caregiver.id,
            updated_by_caregiver_id=caregiver.id,
        )
        accepted_conflicting_assertion = CareAssertion(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-2",
            field="current_dose",
            assertion_kind="current_state",
            asserted_value="A different dose is reported",
            source_label="Caregiver-reviewed medication report",
            source_text="The competing medication report.",
            evidence_ids=[conflict_gap_source.id],
            assertion_status="accepted",
            epistemic_state="reported",
            review_after_days=30,
            created_by_caregiver_id=caregiver.id,
            updated_by_caregiver_id=caregiver.id,
        )
        db.add_all([accepted_assertion, accepted_conflicting_assertion, stale_assertion])
        db.add(Contradiction(
            person_id=person.id,
            subject_type="medication",
            subject_id="medication-2",
            field="current_dose",
            description="Two source-backed dose reports conflict.",
            evidence_ids=[conflict_gap_source.id],
            resolution_status="unresolved",
        ))
        db.flush()

        # Exercise the real caregiver review transition: it must trigger
        # reconciliation itself rather than requiring a later manual refresh.
        from app.services.care_continuity import set_assertion_status
        set_assertion_status(
            db, accepted_assertion.id, person.id, "accepted",
            updated_by_caregiver_id=caregiver.id, review_after_days=30,
        )
        db.refresh(gap)
        db.refresh(question)
        db.refresh(question)
        assert gap.lifecycle_status == GapLifecycleStatus.RESOLVED
        assert conflict_gap.lifecycle_status == GapLifecycleStatus.OPEN
        assert stale_gap.lifecycle_status == GapLifecycleStatus.OPEN
        assert gap.resolution_mechanism.value == "new_evidence"
        assert current_source.id in gap.evidence_ids
        assert question.status == "closed"
        assert question.closed_reason.startswith("automatically_superseded")
        assert question.answered_at is None
        db.close()
    finally:
        teardown_db()


def test_unknown_vs_false():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-2", name="Test Person 2")
        caregiver = Caregiver(
            id="caregiver-2",
            name="Test Caregiver 2",
            email="test2@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-2")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-2",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "allergies",
                "epistemic_state": "unknown",
                "gap_reason": "not_assessed",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "unknown"
        
        response = client.get(
            "/api/v1/uncertainty/view/person-2/medication/med-1"
        )
        assert response.status_code == 200
        view = response.json()
        assert view["epistemic_state"] == "unknown"
        assert view["known_value"] is None
    finally:
        teardown_db()


def test_contradiction_not_collapsed_to_unknown():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-3", name="Test Person 3")
        caregiver = Caregiver(
            id="caregiver-3",
            name="Test Caregiver 3",
            email="test3@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add_all([
            Evidence(id="ev-1", person_id=person.id, type="document", source_text="Medication list states discontinued.", uploaded_by_caregiver_id=caregiver.id, source_type="document"),
            Evidence(id="ev-2", person_id=person.id, type="document", source_text="Current medication list includes metformin.", uploaded_by_caregiver_id=caregiver.id, source_type="document"),
        ])
        db.commit()
        db.close()

        client = authorized_client("person-3")
        
        response = client.post(
            "/api/v1/uncertainty/contradictions",
            json={
                "person_id": "person-3",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "status",
                "description": "One document says metformin is discontinued, another lists it as current medication",
                "evidence_ids": ["ev-1", "ev-2"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["resolution_status"] == "unresolved"
        
        response = client.get(
            "/api/v1/uncertainty/view/person-3/medication/med-1"
        )
        assert response.status_code == 200
        view = response.json()
        assert len(view["contradictions"]) == 1
    finally:
        teardown_db()


def test_ambiguous_not_silently_resolved():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-4", name="Test Person 4")
        caregiver = Caregiver(
            id="caregiver-4",
            name="Test Caregiver 4",
            email="test4@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-4")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-4",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "instruction",
                "epistemic_state": "ambiguous",
                "gap_reason": "ambiguous",
                "known_value": "Continue current medications",
                "context": [{"note": "No medication list attached"}],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "ambiguous"
        assert data["known_value"] == "Continue current medications"
    finally:
        teardown_db()


def test_open_question_lifecycle():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-5", name="Test Person 5")
        caregiver = Caregiver(
            id="caregiver-5",
            name="Test Caregiver 5",
            email="test5@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="person-5-other", name="Other Test Person")
        other_caregiver = Caregiver(
            id="caregiver-5-other",
            name="Other Test Caregiver",
            email="test5-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-5")
        other_client = authorized_client("person-5-other")
        
        response = client.post(
            "/api/v1/uncertainty/questions",
            json={
                "person_id": "person-5",
                "question": "Did the patient attend the cardiology appointment?",
                "subject_type": "appointment",
                "subject_id": "appt-1",
                "priority": "high",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "open"
        assert data["answer"] is None
        
        question_id = data["id"]

        necessity_source = client.post(
            "/api/v1/evidence",
            json={
                "person_id": "person-5",
                "uploaded_by_caregiver_id": "caregiver-5",
                "type": "document",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "caregiver_reported",
                "source_text": "Appointment handoff record identifying a decision that depends on attendance status.",
            },
        )
        assert necessity_source.status_code in {200, 201}, necessity_source.text
        necessity_evidence_id = necessity_source.json()["id"]
        unsupported_necessity = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "necessity": "necessary",
                "necessity_rationale": "The handoff needs the attendance status.",
            },
        )
        assert unsupported_necessity.status_code == 422, unsupported_necessity.text
        missing_dependency = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "necessity": "necessary",
                "necessity_rationale": "The current handoff decision depends on whether the appointment occurred.",
                "evidence_ids": [necessity_evidence_id],
            },
        )
        assert missing_dependency.status_code == 422, missing_dependency.text
        assessed_necessity = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "necessity": "necessary",
                "necessity_rationale": "The current handoff decision depends on whether the appointment occurred.",
                "evidence_ids": [necessity_evidence_id],
                "context": [{
                    "decision_dependency": "The current handoff cannot state attendance status without the source-backed answer."
                }],
            },
        )
        assert assessed_necessity.status_code == 200, assessed_necessity.text
        assert assessed_necessity.json()["necessity"] == "necessary"
        assert assessed_necessity.json()["necessity_rationale"]

        # A caregiver can answer without uploading a separate document. The
        # answer is saved as a new, source-attributed caregiver report.
        first_answer = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "answered",
                "answer": "The caregiver reports that the appointment was attended.",
                "answer_provenance": "Caregiver report recorded during the current session.",
            },
        )
        assert first_answer.status_code == 200, first_answer.text
        assert first_answer.json()["answered_by_caregiver_id"] == "caregiver-5"

        first_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert first_history.status_code == 200, first_history.text
        assert len(first_history.json()) == 1
        assert first_history.json()[0]["answer"] == "The caregiver reports that the appointment was attended."
        assert first_history.json()[0]["answered_by_caregiver_id"] == "caregiver-5"
        first_evidence_id = first_history.json()[0]["evidence_ids"][0]
        assert first_evidence_id
        assert first_history.json()[0]["answer_version"] == 1

        audit_db = TestingSessionLocal()
        try:
            answer_source = audit_db.query(Evidence).filter(Evidence.id == first_evidence_id).one()
            assert answer_source.evidence_kind == "caregiver_report"
            assert answer_source.epistemic_status == "caregiver_reported"
            assert answer_source.uploaded_by_caregiver_id == "caregiver-5"
            assert "The caregiver reports that the appointment was attended." in answer_source.source_text
            assert "Caregiver report recorded during the current session." in answer_source.source_text
        finally:
            audit_db.close()

        repeated_answer = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "answered",
                "answer": "The caregiver reports that the appointment was attended.",
                "answer_provenance": "Caregiver report recorded during the current session.",
                "evidence_ids": [first_evidence_id],
            },
        )
        assert repeated_answer.status_code == 200, repeated_answer.text
        repeated_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert len(repeated_history.json()) == 1, "identical answer retries must not append duplicate answer events"
        metadata_only_update = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"context": [{"note": "Caregiver reviewed the question context."}]},
        )
        assert metadata_only_update.status_code == 200, metadata_only_update.text
        metadata_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert len(metadata_history.json()) == 1, "Metadata-only updates must not fabricate answer history"

        reopened = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"status": "open"},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["answer"] == "The caregiver reports that the appointment was attended."

        second_source = client.post(
            "/api/v1/evidence",
            json={
                "person_id": "person-5",
                "uploaded_by_caregiver_id": "caregiver-5",
                "type": "note",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "unverified",
                "source_text": "The visit note also records that follow-up was discussed.",
            },
        )
        assert second_source.status_code in {200, 201}, second_source.text
        second_evidence_id = second_source.json()["id"]
        corrected_answer = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "answered",
                "answer": "The caregiver reports attendance and says follow-up was discussed.",
                "answer_provenance": "Caregiver clarification updated after checking the visit note.",
                "evidence_ids": [second_evidence_id],
            },
        )
        assert corrected_answer.status_code == 200, corrected_answer.text

        second_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert second_history.status_code == 200, second_history.text
        assert len(second_history.json()) == 2
        assert second_history.json()[0]["answer"] != second_history.json()[1]["answer"]
        assert all(row["person_id"] == "person-5" for row in second_history.json())
        assert second_history.json()[0]["evidence_ids"] == [first_evidence_id]
        assert second_history.json()[1]["evidence_ids"] == [second_evidence_id]
        assert [row["answer_version"] for row in second_history.json()] == [1, 2]
        assert other_client.get(
            f"/api/v1/uncertainty/questions/{question_id}/answer-history"
        ).status_code == 404

        from app.models.uncertainty import OpenQuestionAnswerEvent
        audit_db = TestingSessionLocal()
        try:
            answer_event = audit_db.query(OpenQuestionAnswerEvent).order_by(
                OpenQuestionAnswerEvent.created_at.asc()
            ).first()
            answer_event.answer = "Attempted history rewrite"
            with pytest.raises(ValueError, match="append-only"):
                audit_db.commit()
            audit_db.rollback()
        finally:
            audit_db.close()

        response = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"status": "closed"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["answer"] == "The caregiver reports attendance and says follow-up was discussed."
    finally:
        teardown_db()



def test_clarification_question_requires_same_record_claim_and_matching_subject_scope():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="question-claim-person", name="Question Claim Person")
        caregiver = Caregiver(
            id="question-claim-caregiver",
            name="Question Claim Caregiver",
            email="question-claim@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="question-claim-other-person", name="Other Question Claim Person")
        other_caregiver = Caregiver(
            id="question-claim-other-caregiver",
            name="Other Question Claim Caregiver",
            email="question-claim-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.flush()
        claim_evidence = Evidence(
            id="question-claim-evidence",
            person_id=person.id,
            type="daily_care",
            source_text="Breakfast intake was lower than usual.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        )
        db.add(claim_evidence)
        db.flush()
        db.add_all([
            CareAssertion(
                id="question-claim-own",
                person_id=person.id,
                subject_type="daily_care",
                subject_id="breakfast",
                field="intake",
                assertion_kind="reported_state",
                asserted_value="Breakfast intake was lower than usual.",
                source_label="caregiver report",
                source_text="Breakfast intake was lower than usual.",
                evidence_ids=[claim_evidence.id],
                assertion_status="reported",
            ),
            CareAssertion(
                id="question-claim-foreign",
                person_id=other_person.id,
                subject_type="daily_care",
                subject_id="breakfast",
                field="intake",
                assertion_kind="reported_state",
                asserted_value="Other person's breakfast intake.",
                source_label="caregiver report",
                source_text="Other person's breakfast intake.",
                evidence_ids=[],
                assertion_status="reported",
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client("question-claim-person")
        base = {
            "person_id": "question-claim-person",
            "question": "Was the lower intake also observed at lunch?",
            "subject_type": "daily_care",
            "subject_id": "breakfast",
            "claim_id": "question-claim-own",
            "priority": "informational",
        }
        created = client.post("/api/v1/uncertainty/questions", json=base)
        assert created.status_code == 200, created.text
        assert created.json()["claim_id"] == "question-claim-own"

        mismatched_subject = client.post(
            "/api/v1/uncertainty/questions",
            json={**base, "subject_id": "sleep", "question": "Was sleep affected?"},
        )
        assert mismatched_subject.status_code == 422, mismatched_subject.text

        foreign_claim = client.post(
            "/api/v1/uncertainty/questions",
            json={**base, "claim_id": "question-claim-foreign"},
        )
        assert foreign_claim.status_code == 422, foreign_claim.text

        missing_necessity_rationale = client.post(
            "/api/v1/uncertainty/questions",
            json={
                **base,
                "question": "Who can confirm the medication list?",
                "necessity": "necessary",
                "intended_answerer_role": "professional",
            },
        )
        assert missing_necessity_rationale.status_code == 422, missing_necessity_rationale.text

        with_intent = client.post(
            "/api/v1/uncertainty/questions",
            json={
                **base,
                "question": "Who can confirm the medication list?",
                "intended_answerer_role": "professional",
                "intended_answerer_label": "Cardiology clinic",
                "necessity": "necessary",
                "necessity_rationale": "The current medication status is disputed and is needed for the appointment handoff.",
                "context": [{"decision_dependency": "The appointment handoff cannot identify the current medication status without this answer."}],
            },
        )
        assert with_intent.status_code == 200, with_intent.text
        assert with_intent.json()["intended_answerer_role"] == "professional"
        assert with_intent.json()["intended_answerer_label"] == "Cardiology clinic"
        assert with_intent.json()["necessity"] == "necessary"
        assert "needed for the appointment handoff" in with_intent.json()["necessity_rationale"]

        invalid_answerer = client.post(
            "/api/v1/uncertainty/questions",
            json={**base, "question": "Who can confirm this?", "intended_answerer_role": "ai_model"},
        )
        assert invalid_answerer.status_code == 422, invalid_answerer.text
    finally:
        teardown_db()


def test_partial_knowledge_representation():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-6", name="Test Person 6")
        caregiver = Caregiver(
            id="caregiver-6",
            name="Test Caregiver 6",
            email="test6@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-6")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-6",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "medication_details",
                "epistemic_state": "partially_known",
                "gap_reason": "missing_context",
                "known_value": "Metformin",
                "context": [
                    {"field": "dose", "state": "unknown"},
                    {"field": "frequency", "state": "unknown"},
                    {"field": "current_status", "state": "unknown"},
                    {"field": "adherence", "state": "unknown"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "partially_known"
        assert data["known_value"] == "Metformin"
        assert len(data["context"]) == 4
        assert data["context"][0]["field"] == "dose"
    finally:
        teardown_db()


def test_epistemic_view_summary():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-7", name="Test Person 7")
        caregiver = Caregiver(
            id="caregiver-7",
            name="Test Caregiver 7",
            email="test7@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add(Evidence(
            id="evidence-person-7-med-name",
            person_id=person.id,
            type="caregiver_report",
            source_text="Caregiver reported the medication name as Metformin.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        ))
        db.commit()
        db.close()

        client = authorized_client("person-7")
        
        client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "name",
                "epistemic_state": "known",
                "known_value": "Metformin",
                "evidence_ids": ["evidence-person-7-med-name"],
            },
        )
        
        client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "dose",
                "epistemic_state": "unknown",
                "gap_reason": "not_documented",
                "priority": "high",
            },
        )
        
        client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-7",
                "subject_type": "medication",
                "subject_id": "med-1",
                "field": "dose",
                "epistemic_state": "unknown",
                "gap_reason": "not_documented",
            },
        )
        
        client.post(
            "/api/v1/uncertainty/questions",
            json={
                "person_id": "person-7",
                "question": "Is the patient still taking this medication?",
                "subject_type": "medication",
                "subject_id": "med-1",
                "priority": "medium",
                "necessity": "necessary",
                "necessity_rationale": "The medication status is needed for the next documented handoff.",
                "evidence_ids": ["evidence-person-7-med-name"],
                "context": [{"decision_dependency": "The handoff cannot describe the current medication status without this answer."}],
            },
        )
        
        response = client.get(
            "/api/v1/uncertainty/summary/person-7/medication/med-1"
        )
        assert response.status_code == 200
        summary = response.json()
        assert summary["known_count"] == 1
        assert summary["unknown_count"] == 1
        assert summary["open_gaps_count"] == 1
        assert summary["open_questions_count"] == 1
        assert summary["total_fields"] == 2
    finally:
        teardown_db()


def test_case_b_explicit_negative_not_unknown():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-b", name="Test Person B")
        caregiver = Caregiver(
            id="caregiver-b",
            name="Test Caregiver B",
            email="testb@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add(Evidence(
            id="evidence-person-b-falls",
            person_id=person.id,
            type="caregiver_report",
            source_text="Caregiver reported no falls in the past six months.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        ))
        db.commit()
        db.close()

        client = authorized_client("person-b")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-b",
                "subject_type": "fall_history",
                "subject_id": "fall-1",
                "field": "recent_falls",
                "epistemic_state": "known",
                "known_value": "No falls in the past six months",
                "gap_reason": "absent",
                "evidence_ids": ["evidence-person-b-falls"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "known"
        assert data["known_value"] == "No falls in the past six months"
    finally:
        teardown_db()


def test_case_f_missing_outcome():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-f", name="Test Person F")
        caregiver = Caregiver(
            id="caregiver-f",
            name="Test Caregiver F",
            email="testf@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-f")
        
        response = client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-f",
                "subject_type": "referral",
                "subject_id": "referral-1",
                "field": "outcome",
                "epistemic_state": "unknown",
                "gap_reason": "expected_but_unavailable",
                "description": "Cardiology referral made six months ago, no information about whether appointment occurred",
                "priority": "high",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "unknown"
        assert data["gap_reason"] == "expected_but_unavailable"
    finally:
        teardown_db()


def test_case_g_missing_date_with_partial_temporal():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-g", name="Test Person G")
        caregiver = Caregiver(
            id="caregiver-g",
            name="Test Caregiver G",
            email="testg@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-g")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-g",
                "subject_type": "diagnosis",
                "subject_id": "dx-1",
                "field": "onset_date",
                "epistemic_state": "partially_known",
                "gap_reason": "missing_context",
                "known_value": "Diagnosed before June 2024",
                "temporal_precision": "before_June_2024",
                "context": [{"note": "Exact onset date unknown, but predates June 2024"}],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "partially_known"
        assert data["temporal_precision"] == "before_June_2024"
    finally:
        teardown_db()


def test_case_h_missing_context():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-h", name="Test Person H")
        caregiver = Caregiver(
            id="caregiver-h",
            name="Test Caregiver H",
            email="testh@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-h")
        
        response = client.post(
            "/api/v1/uncertainty/assessments",
            json={
                "person_id": "person-h",
                "subject_type": "observation",
                "subject_id": "obs-1",
                "field": "blood_pressure",
                "epistemic_state": "partially_known",
                "gap_reason": "missing_context",
                "known_value": "150/95",
                "context": [
                    {"missing": "date"},
                    {"missing": "patient_context"},
                    {"missing": "measurement_circumstances"},
                    {"missing": "source"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "partially_known"
        assert data["known_value"] == "150/95"
        assert len(data["context"]) == 4
    finally:
        teardown_db()


def test_case_i_incomplete_record():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-i", name="Test Person I")
        caregiver = Caregiver(
            id="caregiver-i",
            name="Test Caregiver I",
            email="testi@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("person-i")
        
        response = client.post(
            "/api/v1/uncertainty/gaps",
            json={
                "person_id": "person-i",
                "subject_type": "record",
                "subject_id": "hospitalization-1",
                "field": "completeness",
                "epistemic_state": "unknown",
                "gap_reason": "insufficient_evidence",
                "description": "Only discharge summary received; cannot conclude this represents complete care history",
                "priority": "high",
                "context": [
                    {"known": "discharge_summary"},
                    {"missing": "full_medical_record"},
                    {"missing": "prior_care_history"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["epistemic_state"] == "unknown"
        assert data["gap_reason"] == "insufficient_evidence"
    finally:
        teardown_db()


def test_uncertainty_api_enforces_session_scope_and_ignores_body_identity():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person_a = Person(id="scope-person-a", name="Scoped Person A")
        caregiver_a = Caregiver(id="scope-caregiver-a", name="Caregiver A", email=None, relationship="Family", person_id=person_a.id)
        person_b = Person(id="scope-person-b", name="Scoped Person B")
        caregiver_b = Caregiver(id="scope-caregiver-b", name="Caregiver B", email=None, relationship="Family", person_id=person_b.id)
        db.add_all([person_a, caregiver_a, person_b, caregiver_b])
        db.commit()
        db.close()

        client = authorized_client("scope-person-a")
        denied = client.get("/api/v1/uncertainty/gaps/scope-person-b")
        assert denied.status_code == 403

        created = client.post("/api/v1/uncertainty/gaps", json={
            "person_id": "scope-person-b",
            "subject_type": "medication",
            "subject_id": "med-scope",
            "field": "dose",
            "epistemic_state": "unknown",
            "gap_reason": "not_documented",
            "description": "Dose has not been documented.",
        })
        assert created.status_code == 200
        assert created.json()["person_id"] == "scope-person-a"
        assert client.get("/api/v1/uncertainty/gaps/scope-person-b").status_code == 403
    finally:
        teardown_db()


def test_uncertainty_api_rejects_missing_bearer_session():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        response = client.get("/api/v1/uncertainty/gaps/person-not-authorized")
        assert response.status_code == 401
    finally:
        teardown_db()



def test_conflict_resolution_requires_rationale_and_preserves_reopen_history():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="resolution-person", name="Resolution Person")
        caregiver = Caregiver(
            id="resolution-caregiver",
            name="Resolution Caregiver",
            email="resolution@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="resolution-other-person", name="Other Person")
        other_caregiver = Caregiver(
            id="resolution-other-caregiver",
            name="Other Caregiver",
            email="resolution-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.add(Evidence(
            id="resolution-evidence",
            person_id=person.id,
            type="document",
            source_text="The source resolves the reported date only.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        ))
        db.commit()
        db.close()

        client = authorized_client("resolution-person")
        other_client = authorized_client("resolution-other-person")
        created = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "resolution-person",
            "subject_type": "medication",
            "subject_id": "medication-1",
            "field": "status",
            "description": "Two records disagree about current status",
            "evidence_ids": [],
        })
        assert created.status_code == 200, created.text
        conflict_id = created.json()["id"]

        denied = other_client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "resolved", "resolution_notes": "Reviewed"},
        )
        assert denied.status_code == 404

        missing_rationale = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "resolved"},
        )
        assert missing_rationale.status_code == 422

        missing_partial_remainder = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_notes": "Attempted partial resolution without describing what remains unresolved.",
                "resolution_scope": "Resolve only the date.",
                "evidence_ids": ["resolution-evidence"],
            },
        )
        assert missing_partial_remainder.status_code == 422, missing_partial_remainder.text

        missing_resolution_evidence = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "resolved",
                "resolution_notes": "Attempted to resolve without linking any source evidence.",
            },
        )
        assert missing_resolution_evidence.status_code == 422, missing_resolution_evidence.text

        missing_scope = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_notes": "Reviewed the source.",
                "evidence_ids": ["resolution-evidence"],
            },
        )
        assert missing_scope.status_code == 422, missing_scope.text

        partial = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_notes": "The date detail is established, but the medication status remains disputed.",
                "resolution_scope": "Resolve only the reported date.",
                "unresolved_remainder": "Medication status remains disputed; no medication state is established.",
                "evidence_ids": ["resolution-evidence"],
            },
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["resolution_status"] == "partially_resolved"
        assert partial.json()["resolution_scope"] == "Resolve only the reported date."
        assert partial.json()["unresolved_remainder"] == "Medication status remains disputed; no medication state is established."

        resolved = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "resolved", "resolution_notes": "Reviewed the two source records; discrepancy remains documented."},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolution_status"] == "resolved"

        reopened = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={"resolution_status": "reopened", "resolution_notes": "New conflicting evidence was added; reassessment required."},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["resolution_status"] == "reopened"

        history = client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history")
        assert history.status_code == 200, history.text
        events = history.json()
        assert [event["new_status"] for event in events] == ["partially_resolved", "resolved", "reopened"]
        assert events[0]["resolution_scope"] == "Resolve only the reported date."
        assert events[0]["unresolved_remainder"] == "Medication status remains disputed; no medication state is established."
        assert all(event["rationale"] for event in events)
        assert all(event["person_id"] == "resolution-person" for event in events)

        from app.models.uncertainty import ContradictionResolutionEvent
        audit_db = TestingSessionLocal()
        try:
            audit_event = audit_db.query(ContradictionResolutionEvent).first()
            audit_event.rationale = "Attempted audit rewrite"
            with pytest.raises(ValueError, match="append-only"):
                audit_db.commit()
            audit_db.rollback()
        finally:
            audit_db.close()

        assert other_client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history").status_code == 404
    finally:
        teardown_db()



def test_evidence_review_history_persists_attribution_and_is_person_scoped():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="review-person", name="Review Person")
        caregiver = Caregiver(
            id="review-caregiver", name="Review Caregiver",
            email="review@example.com", relationship="Family", person_id=person.id,
        )
        other_person = Person(id="review-other-person", name="Other Review Person")
        other_caregiver = Caregiver(
            id="review-other-caregiver", name="Other Review Caregiver",
            email="review-other@example.com", relationship="Family", person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.flush()
        evidence = Evidence(
            id="review-evidence-1", person_id=person.id, type="caregiver_report",
            source_text="Caregiver reported a missed appointment.", uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report", time_provenance="reported as occurring last Tuesday",
        )
        db.add(evidence)
        db.commit()
        db.close()

        client = authorized_client("review-person")
        other_client = authorized_client("review-other-person")
        payload = {
            "review_type": "reliability",
            "outcome": "unverified",
            "rationale": "The source is attributed, but no independent corroborating record is linked.",
            "policy_version": "evidence-review-v1",
            "criteria": [
                {"criterion": "source attribution", "result": "present", "rationale": "Uploader and source type are persisted."},
                {"criterion": "independent corroboration", "result": "none_linked", "rationale": "No independent source is linked."},
                {"criterion": "temporal context", "result": "partial", "rationale": "The report says last Tuesday without an absolute date."},
                {"criterion": "evidence freshness", "result": "undetermined", "rationale": "The event date is not absolute enough to assess current relevance."},
                {"criterion": "record completeness", "result": "partial", "rationale": "The report does not include a source document or follow-up outcome."},
            ],
        }
        created = client.post("/api/v1/care-continuity/evidence/review-evidence-1/reviews", json=payload)
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["evidence_id"] == "review-evidence-1"
        assert row["source_attribution"]["source_type"] == "caregiver_report"
        assert row["reviewed_by_caregiver_id"] == "review-caregiver"

        listed = client.get("/api/v1/care-continuity/evidence/review-evidence-1/reviews")
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        assert listed.json()[0]["policy_version"] == "evidence-review-v1"
        assert other_client.get("/api/v1/care-continuity/evidence/review-evidence-1/reviews").status_code == 404
        invalid = client.post(
            "/api/v1/care-continuity/evidence/review-evidence-1/reviews",
            json={**payload, "outcome": "high_confidence"},
        )
        assert invalid.status_code == 422
    finally:
        teardown_db()



def test_conflict_candidates_match_context_and_overlapping_time_without_auto_resolution():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="match-person", name="Match Person")
        caregiver = Caregiver(
            id="match-caregiver", name="Match Caregiver",
            email="match@example.com", relationship="Family", person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client("match-person")
        base = {
            "person_id": "match-person",
            "subject_type": "daily_care",
            "subject_id": "sleep-observation",
            "field": "sleep_pattern",
            "description": "Care reports differ about sleep timing.",
            "context_key": "overnight-at-home",
        }
        first = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-01T00:00:00Z", "effective_to": "2026-10-03T00:00:00Z",
        })
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]
        assert first.json()["resolution_status"] == "unresolved"

        overlapping = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-02T00:00:00Z", "effective_to": "2026-10-04T00:00:00Z",
        })
        assert overlapping.status_code == 200, overlapping.text
        assert first_id in overlapping.json()["matching_candidate_ids"]
        assert overlapping.json()["resolution_status"] == "unresolved"

        different_context = client.post("/api/v1/uncertainty/contradictions", json={
            **{**base, "context_key": "overnight-at-facility"},
            "effective_from": "2026-10-02T00:00:00Z", "effective_to": "2026-10-04T00:00:00Z",
        })
        assert different_context.status_code == 200, different_context.text
        assert different_context.json()["matching_candidate_ids"] == []

        disjoint = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-05T00:00:00Z", "effective_to": "2026-10-06T00:00:00Z",
        })
        assert disjoint.status_code == 200, disjoint.text
        assert disjoint.json()["matching_candidate_ids"] == []

        invalid_interval = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "effective_from": "2026-10-06T00:00:00Z", "effective_to": "2026-10-05T00:00:00Z",
        })
        assert invalid_interval.status_code == 422

        unaudited_resolution = client.post("/api/v1/uncertainty/contradictions", json={
            **base, "resolution_status": "resolved", "resolution_notes": "Client attempted to create an already resolved conflict.",
        })
        assert unaudited_resolution.status_code == 422
    finally:
        teardown_db()



def test_conflict_matching_respects_context_and_explicit_time_intervals():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="conflict-scope-person", name="Conflict Scope Person")
        caregiver = Caregiver(
            id="conflict-scope-caregiver",
            name="Conflict Scope Caregiver",
            email="conflict-scope@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()
        client = authorized_client("conflict-scope-person")

        first = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "conflict-scope-person",
            "subject_type": "daily_care",
            "subject_id": "meal-1",
            "field": "amount_eaten",
            "description": "Report A",
            "context_key": "breakfast",
            "effective_from": "2026-10-01T08:00:00Z",
            "effective_to": "2026-10-01T09:00:00Z",
            "evidence_ids": [],
        })
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]

        different_context_and_disjoint_time = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "conflict-scope-person",
            "subject_type": "daily_care",
            "subject_id": "meal-1",
            "field": "amount_eaten",
            "description": "Report B",
            "context_key": "dinner",
            "effective_from": "2026-10-01T18:00:00Z",
            "effective_to": "2026-10-01T19:00:00Z",
            "evidence_ids": [],
        })
        assert different_context_and_disjoint_time.status_code == 200, different_context_and_disjoint_time.text
        assert first_id not in different_context_and_disjoint_time.json()["matching_candidate_ids"]

        same_context_overlapping_time = client.post("/api/v1/uncertainty/contradictions", json={
            "person_id": "conflict-scope-person",
            "subject_type": "daily_care",
            "subject_id": "meal-1",
            "field": "amount_eaten",
            "description": "Report C",
            "context_key": "breakfast",
            "effective_from": "2026-10-01T08:30:00Z",
            "effective_to": "2026-10-01T09:30:00Z",
            "evidence_ids": [],
        })
        assert same_context_overlapping_time.status_code == 200, same_context_overlapping_time.text
        assert first_id in same_context_overlapping_time.json()["matching_candidate_ids"]
    finally:
        teardown_db()


def test_analyze_endpoint_does_not_accept_assumption_from_unverified_persisted_evidence():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-analysis", name="Analysis Test Person")
        caregiver = Caregiver(
            id="caregiver-analysis",
            name="Analysis Test Caregiver",
            email="analysis@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="evidence-analysis-unverified",
            person_id=person.id,
            type="caregiver_report",
            source_text="A care instruction was reported but not independently checked.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
            evidence_status="unverified",
        )
        db.add_all([person, caregiver, evidence])
        db.commit()
        db.close()

        client = authorized_client("person-analysis")
        payload = {
            "person_id": "person-analysis",
            "subject_type": "care_instruction",
            "subject_id": "instruction-1",
            "field": "current_instruction",
            "claim": "The current care instruction is confirmed.",
            "task": "recommendation",
            "evidence_ids": ["evidence-analysis-unverified"],
            "assumptions": [{
                "statement": "The report reflects the current instruction.",
                "status": "accepted",
                "supporting_evidence_ids": ["evidence-analysis-unverified"],
                "reviewed_by_caregiver_id": "different-caregiver",
                "resolution_rationale": "Reviewed against the supplied report.",
            }],
        }
        unauthorized = client.post("/api/v1/uncertainty/analyze", json=payload)
        assert unauthorized.status_code == 403, unauthorized.text

        payload["assumptions"][0]["reviewed_by_caregiver_id"] = "caregiver-analysis"
        response = client.post("/api/v1/uncertainty/analyze", json=payload)
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["assumptions"][0]["status"] == "unresolved"
        assert analysis["assumptions"][0]["has_verified_supporting_evidence"] is False
        assert "unresolved_assumptions" in analysis["classifications"]
        assert analysis["sufficiency"] == "insufficient_pending_resolution"
        assert analysis["conclusion_confidence"] is None
    finally:
        teardown_db()



def test_source_lineage_rejects_false_independence_and_cross_record_links():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="lineage-person", name="Lineage Person")
        other_person = Person(id="lineage-other-person", name="Other Person")
        caregiver = Caregiver(id="lineage-caregiver", name="Lineage Caregiver", email="lineage@example.com", relationship="Family", person_id=person.id)
        other_caregiver = Caregiver(id="lineage-other-caregiver", name="Other Caregiver", email="lineage-other@example.com", relationship="Family", person_id=other_person.id)
        db.add_all([person, other_person, caregiver, other_caregiver])
        db.flush()
        db.add_all([
            Evidence(id="lineage-origin", person_id=person.id, type="message", source_text="Original report", uploaded_by_caregiver_id=caregiver.id),
            Evidence(id="lineage-forward", person_id=person.id, type="message", source_text="Forwarded report", uploaded_by_caregiver_id=caregiver.id),
            Evidence(id="lineage-cross-record", person_id=other_person.id, type="message", source_text="Other record report", uploaded_by_caregiver_id=other_caregiver.id),
        ])
        person_id = person.id
        caregiver_id = caregiver.id
        db.commit()
        db.close()

        client = authorized_client(person_id)
        dependent = client.post("/api/v1/care-continuity/evidence/lineage-forward/relationships", json={
            "related_evidence_id": "lineage-origin",
            "relationship_type": "forwarded_from",
            "source_independence_status": "shared_origin",
            "origin_group_key": "message-thread-1",
            "rationale": "Forwarded from the original message.",
        })
        assert dependent.status_code == 201, dependent.text
        assert dependent.json()["source_independence_status"] == "shared_origin"
        assert dependent.json()["reviewed_by_caregiver_id"] == caregiver_id

        false_independence = client.post("/api/v1/care-continuity/evidence/lineage-forward/relationships", json={
            "related_evidence_id": "lineage-origin",
            "relationship_type": "copied_from",
            "source_independence_status": "independent",
            "rationale": "Attempt to mark a copy independent.",
        })
        assert false_independence.status_code == 422

        cross_record = client.post("/api/v1/care-continuity/evidence/lineage-forward/relationships", json={
            "related_evidence_id": "lineage-cross-record",
            "relationship_type": "related_to",
            "source_independence_status": "unknown",
            "rationale": "Should not cross care records.",
        })
        assert cross_record.status_code == 404

        listed = client.get("/api/v1/care-continuity/evidence/lineage-origin/relationships")
        assert listed.status_code == 200, listed.text
        assert len(listed.json()) == 1
        assert listed.json()[0]["source_evidence_id"] == "lineage-forward"

        # Exercise the real authenticated uncertainty API, not just the lineage
        # helper: two selected records with a persisted shared-origin edge must
        # not be counted as independent corroboration.
        analysis_response = client.post("/api/v1/uncertainty/analyze", json={
            "person_id": person_id,
            "subject_type": "care_event",
            "subject_id": "event-lineage-1",
            "field": "reported_event",
            "claim": "Two independent sources confirm the event.",
            "task": "summarize",
            "evidence_ids": ["lineage-origin", "lineage-forward"],
        })
        assert analysis_response.status_code == 200, analysis_response.text
        analysis = analysis_response.json()["analysis"]
        assert analysis["independent_evidence_record_count"] == 1
        assert any(
            group["reason"] == "shared_source_lineage"
            and group["independent_corroboration"] is False
            for group in analysis["duplicate_evidence_groups"]
        )
        assert analysis["source_lineage_scope"]["status"] == "bounded_closure_loaded"
    finally:
        teardown_db()


def test_structured_observation_requires_exact_source_and_preserves_correction_history():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import EvidenceObservation
        from app.models.privacy_delivery import AuditLogEvent
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="observation-person", name="Observation Person")
        caregiver = Caregiver(id="observation-caregiver", name="Observation Caregiver", email="observation@example.com", relationship="Family", person_id=person.id)
        db.add_all([person, caregiver])
        db.flush()
        db.add(Evidence(
            id="observation-evidence", person_id=person.id, type="caregiver_report",
            source_text="Caregiver reports three awakenings last night.",
            uploaded_by_caregiver_id=caregiver.id, source_type="caregiver_report",
        ))
        person_id = person.id
        db.commit()
        db.close()

        client = authorized_client(person_id)
        source_text = "Caregiver reports three awakenings last night."
        source_passage = "three awakenings last night"
        source_span_start = source_text.index(source_passage)
        payload = {
            "observation_kind": "caregiver_report",
            "epistemic_status": "reported",
            "subject_label": "person receiving care",
            "attribute": "sleep_awakenings",
            "value_text": "three",
            "source_passage": source_passage,
            "source_span_start": source_span_start,
            "source_span_end": source_span_start + len(source_passage),
            "extraction_method": "human_entered",
            "extraction_version": "test-v1",
        }
        invalid = client.post("/api/v1/care-continuity/evidence/observation-evidence/observations", json={
            **payload, "source_passage": "four awakenings last night",
        })
        assert invalid.status_code == 422

        created = client.post("/api/v1/care-continuity/evidence/observation-evidence/observations", json=payload)
        assert created.status_code == 201, created.text
        first = created.json()
        assert first["source_passage"] == payload["source_passage"]
        assert first["epistemic_status"] == "reported"
        assert first["source_span_end"] - first["source_span_start"] == len(payload["source_passage"])

        correction = client.post("/api/v1/care-continuity/evidence/observation-evidence/observations", json={
            **payload,
            "value_text": "two",
            "supersedes_observation_id": first["id"],
            "correction_rationale": "Caregiver corrected the count.",
        })
        assert correction.status_code == 201, correction.text
        second = correction.json()
        assert second["supersedes_observation_id"] == first["id"]
        assert second["correction_rationale"] == "Caregiver corrected the count."
        assert second["care_continuity_execution"]["stages"]["reconciliation"] == "committed"
        assert "Related care summaries were refreshed" in second["reassessment_notice"]
        assert second["reassessment_receipt"]["source_observation_id"] == second["id"]
        assert second["reassessment_receipt"]["supersedes_observation_id"] == first["id"]
        assert second["reassessment_receipt"]["affected_record_views"] == [
            "living_care_record", "change_and_pattern_summaries", "open_questions", "claim_assessments",
        ]

        db = TestingSessionLocal()
        audit = db.query(AuditLogEvent).filter(
            AuditLogEvent.target_id == second["id"],
            AuditLogEvent.action == "evidence_observation.correct",
        ).one()
        assert audit.outcome == "success"
        assert audit.record_metadata["source_evidence_id"] == "observation-evidence"
        assert audit.record_metadata["affected_record_views"] == second["reassessment_receipt"]["affected_record_views"]
        original = db.query(EvidenceObservation).filter(EvidenceObservation.id == first["id"]).one()
        with pytest.raises(ValueError, match="append-only"):
            original.value_text = "two"
            db.commit()
        db.rollback()
        assert db.query(EvidenceObservation).filter(EvidenceObservation.id == first["id"]).one().value_text == "three"
        db.close()
    finally:
        teardown_db()


def test_analyze_endpoint_uses_persisted_shared_origin_for_independence_count():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import EvidenceSourceRelationship

        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="person-lineage-analysis", name="Lineage Analysis Person")
        caregiver = Caregiver(
            id="caregiver-lineage-analysis",
            name="Lineage Analysis Caregiver",
            email="lineage-analysis@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = [
            Evidence(
                id="evidence-derived-a",
                person_id=person.id,
                type="derived_summary",
                source_text="Breakfast intake was low.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="derived_summary",
                evidence_status="verified",
            ),
            Evidence(
                id="evidence-derived-b",
                person_id=person.id,
                type="derived_summary",
                source_text="Breakfast consumption was limited.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="derived_summary",
                evidence_status="verified",
            ),
            Evidence(
                id="evidence-origin-root",
                person_id=person.id,
                type="caregiver_report",
                source_text="Original caregiver report about breakfast intake.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="caregiver_report",
                evidence_status="verified",
            ),
        ]
        db.add_all([person, caregiver, *evidence])
        db.flush()
        db.add_all([
            EvidenceSourceRelationship(
                person_id=person.id,
                source_evidence_id="evidence-derived-a",
                related_evidence_id="evidence-origin-root",
                relationship_type="derived_from",
                source_independence_status="shared_origin",
                origin_group_key="breakfast-origin-group",
                rationale="Derived summary references the original caregiver report.",
                reviewed_by_caregiver_id=caregiver.id,
            ),
            EvidenceSourceRelationship(
                person_id=person.id,
                source_evidence_id="evidence-derived-b",
                related_evidence_id="evidence-origin-root",
                relationship_type="derived_from",
                source_independence_status="shared_origin",
                origin_group_key="breakfast-origin-group",
                rationale="Second derived summary references the same original report.",
                reviewed_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client("person-lineage-analysis")
        response = client.post(
            "/api/v1/uncertainty/analyze",
            json={
                "person_id": "person-lineage-analysis",
                "subject_type": "daily_care",
                "subject_id": "breakfast",
                "field": "intake",
                "claim": "Two independent sources corroborate low breakfast intake.",
                "task": "summarize",
                "evidence_ids": ["evidence-derived-a", "evidence-derived-b"],
            },
        )
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["independent_evidence_record_count"] == 1
        assert analysis["duplicate_evidence_groups"][0]["reason"] == "shared_source_lineage"
        assert analysis["duplicate_evidence_groups"][0]["independent_corroboration"] is False
    finally:
        teardown_db()



def test_claim_dependency_graph_is_persisted_person_scoped_cycle_safe_and_append_only():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import CareAssertion, EvidenceClaimDependency
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="claim-dependency-person", name="Claim Dependency Person")
        caregiver = Caregiver(
            id="claim-dependency-caregiver", name="Claim Dependency Caregiver",
            email="claim-dependency@example.com", relationship="Family", person_id=person.id,
        )
        other_person = Person(id="claim-dependency-other", name="Other Person")
        other_caregiver = Caregiver(
            id="claim-dependency-other-caregiver", name="Other Caregiver",
            email="claim-dependency-other@example.com", relationship="Family", person_id=other_person.id,
        )
        claim_a = CareAssertion(
            id="claim-dependency-a", person_id=person.id, subject_type="sleep",
            subject_id="sleep-1", field="daytime_function", assertion_kind="inference",
            asserted_value="Daytime function may be affected", source_label="caregiver record",
            source_text="Daytime function may be affected", evidence_ids=[],
            assertion_status="unverified", created_by_caregiver_id=caregiver.id,
        )
        claim_b = CareAssertion(
            id="claim-dependency-b", person_id=person.id, subject_type="sleep",
            subject_id="sleep-1", field="night_waking", assertion_kind="report",
            asserted_value="Caregiver reported two awakenings", source_label="caregiver report",
            source_text="Caregiver reported two awakenings", evidence_ids=[],
            assertion_status="reported", created_by_caregiver_id=caregiver.id,
        )
        other_claim = CareAssertion(
            id="claim-dependency-private", person_id=other_person.id, subject_type="sleep",
            subject_id="sleep-1", field="night_waking", assertion_kind="report",
            asserted_value="Private report", source_label="other care record",
            source_text="Private report", evidence_ids=[],
            assertion_status="reported", created_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver, claim_a, claim_b, other_claim])
        db.commit()
        db.close()

        client = authorized_client("claim-dependency-person")
        created = client.post("/api/v1/care-continuity/claims/claim-dependency-a/dependencies", json={
            "depends_on_claim_id": "claim-dependency-b",
            "dependency_type": "derived_from",
            "rationale": "The interpretation depends on the reported night-waking observation.",
        })
        assert created.status_code == 201, created.text
        edge = created.json()
        assert edge["person_id"] == "claim-dependency-person"
        assert edge["created_by_caregiver_id"] == "claim-dependency-caregiver"

        outgoing = client.get("/api/v1/care-continuity/claims/claim-dependency-a/dependencies")
        incoming = client.get("/api/v1/care-continuity/claims/claim-dependency-b/dependencies")
        assert outgoing.status_code == incoming.status_code == 200
        assert any(item["id"] == edge["id"] and item["direction"] == "outgoing" for item in outgoing.json())
        assert any(item["id"] == edge["id"] and item["direction"] == "incoming" for item in incoming.json())

        duplicate = client.post("/api/v1/care-continuity/claims/claim-dependency-a/dependencies", json={
            "depends_on_claim_id": "claim-dependency-b",
            "dependency_type": "derived_from",
            "rationale": "Duplicate edge must not be silently added.",
        })
        assert duplicate.status_code == 409

        cycle = client.post("/api/v1/care-continuity/claims/claim-dependency-b/dependencies", json={
            "depends_on_claim_id": "claim-dependency-a",
            "dependency_type": "requires",
            "rationale": "This edge would close a dependency cycle.",
        })
        assert cycle.status_code == 422
        cross_person = client.post("/api/v1/care-continuity/claims/claim-dependency-a/dependencies", json={
            "depends_on_claim_id": "claim-dependency-private",
            "dependency_type": "derived_from",
            "rationale": "Cross-person claims must be rejected.",
        })
        assert cross_person.status_code == 404

        audit_db = TestingSessionLocal()
        try:
            persisted = audit_db.query(EvidenceClaimDependency).filter_by(id=edge["id"]).one()
            persisted.rationale = "attempted rewrite"
            with pytest.raises(ValueError, match="append-only"):
                audit_db.commit()
            audit_db.rollback()
        finally:
            audit_db.close()
    finally:
        teardown_db()


def test_safety_response_validation_rejects_unpersisted_evidence_ids():
    """Evidence IDs supplied to safety validation must resolve inside the bearer-scoped record."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="safety-person-1", name="Safety Person")
        caregiver = Caregiver(
            id="safety-caregiver-1",
            name="Safety Caregiver",
            email="safety-caregiver@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        response = client.post(
            "/api/v1/validate-response",
            params={
                "candidate_response": "The record confirms the medication was changed.",
                "risk_class": "informational",
            },
            json={
                "claims": ["The medication was changed."],
                "evidence_ids": ["not-a-persisted-evidence-id"],
            },
        )
        assert response.status_code == 404, response.text
        assert "not found in this care record" in response.json()["detail"]
    finally:
        teardown_db()


def test_safety_audit_history_rejects_cross_record_person_id():
    """Audit history cannot be read by swapping a person ID in the URL."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="safety-person-own", name="Own Person")
        other_person = Person(id="safety-person-other", name="Other Person")
        caregiver = Caregiver(
            id="safety-caregiver-own",
            name="Own Caregiver",
            email="safety-caregiver-own@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, other_person, caregiver])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        response = client.get("/api/v1/safety/audit/safety-person-other")
        assert response.status_code == 404
    finally:
        teardown_db()



def test_claim_assessment_impact_requires_review_when_unassessed():
    """A claim without a persisted assessment must not be reported as current."""
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import CareAssertion
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="impact-person-1", name="Impact Person")
        caregiver = Caregiver(
            id="impact-caregiver-1",
            name="Impact Caregiver",
            email="impact-caregiver@example.com",
            relationship="Family",
            person_id=person.id,
        )
        claim = CareAssertion(
            id="impact-claim-1",
            person_id=person.id,
            subject_type="sleep",
            subject_id="sleep-1",
            field="night_waking",
            assertion_kind="report",
            asserted_value="Caregiver reported two awakenings",
            source_label="caregiver report",
            source_text="Caregiver reported two awakenings",
            evidence_ids=[],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, claim])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        response = client.get(
            "/api/v1/care-continuity/claims/impact-claim-1/assessment-impact"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["claim_id"] == claim.id
        assert body["status"] == "not_assessed"
        assert body["review_required"] is True
        assert body["changed_observation_ids"] == []
        assert "no_persisted_claim_assessment" in body["reasons"]
        assert "not a claim-truth judgment" in body["interpretation_limit"]

        # The endpoint must not leak another care record's claim.
        other = CareAssertion(
            id="impact-claim-private",
            person_id="impact-person-private",
            subject_type="sleep",
            subject_id="sleep-private",
            field="night_waking",
            assertion_kind="report",
            asserted_value="Private report",
            source_label="private source",
            source_text="Private report",
            evidence_ids=[],
            assertion_status="reported",
        )
        db = TestingSessionLocal()
        db.add(Person(id="impact-person-private", name="Private Person"))
        db.add(other)
        db.commit()
        db.close()
        denied = client.get(
            "/api/v1/care-continuity/claims/impact-claim-private/assessment-impact"
        )
        assert denied.status_code == 404
    finally:
        teardown_db()



def test_exact_duplicate_source_text_does_not_inflate_independent_corroboration():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="exact-duplicate-person", name="Exact Duplicate Person")
        caregiver = Caregiver(
            id="exact-duplicate-caregiver",
            name="Exact Duplicate Caregiver",
            email="exact-duplicate@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        identical_text = "Caregiver reports three awakenings overnight."
        db.add_all([
            Evidence(
                id="exact-duplicate-a", person_id=person.id, type="caregiver_report",
                source_text=identical_text, uploaded_by_caregiver_id=caregiver.id,
            ),
            Evidence(
                id="exact-duplicate-b", person_id=person.id, type="forwarded_message",
                source_text=identical_text, uploaded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client("exact-duplicate-person")
        response = client.post(
            "/api/v1/uncertainty/analyze",
            json={
                "person_id": "exact-duplicate-person",
                "subject_type": "sleep",
                "subject_id": "overnight",
                "field": "awakenings",
                "claim": "Two independent sources confirm three awakenings.",
                "task": "summarize",
                "evidence_ids": ["exact-duplicate-a", "exact-duplicate-b"],
            },
        )
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["independent_evidence_record_count"] is None
        assert analysis["independent_evidence_count_status"] == "requires_source_independence_review"
        assert analysis["review_required"] is True
        candidates = analysis["exact_content_duplicate_candidates"]
        assert len(candidates) == 1
        assert candidates[0]["evidence_ids"] == ["exact-duplicate-a", "exact-duplicate-b"]
        assert candidates[0]["common_origin_established"] is False
        assert "exact_content_duplicate_candidate_review" in analysis["classifications"]

        relationship_response = client.post(
            "/api/v1/care-continuity/evidence/exact-duplicate-a/relationships",
            json={
                "related_evidence_id": "exact-duplicate-b",
                "relationship_type": "corroboration_attempt_failed",
                "source_independence_status": "unknown",
                "rationale": "The reports were compared but did not independently establish the claimed detail.",
            },
        )
        assert relationship_response.status_code == 201, relationship_response.text
        reassessed = client.post(
            "/api/v1/uncertainty/analyze",
            json={
                "person_id": "exact-duplicate-person",
                "subject_type": "sleep",
                "subject_id": "overnight",
                "field": "awakenings",
                "claim": "Two independent sources confirm three awakenings.",
                "task": "summarize",
                "evidence_ids": ["exact-duplicate-a", "exact-duplicate-b"],
            },
        )
        assert reassessed.status_code == 200, reassessed.text
        corroboration = reassessed.json()["analysis"]["corroboration_assessment"]
        assert corroboration["status"] == "attempted_but_not_corroborated"
        assert corroboration["independent_corroboration_established"] is False
        assert corroboration["failed_corroboration_pairs"] == [["exact-duplicate-a", "exact-duplicate-b"]]
    finally:
        teardown_db()


def test_safety_escalation_workflows_are_authenticated_and_person_scoped():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="safety-scope-person", name="Safety Scope Person")
        caregiver = Caregiver(
            id="safety-scope-caregiver",
            name="Safety Scope Caregiver",
            email="safety-scope@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="safety-scope-other", name="Safety Scope Other")
        other_caregiver = Caregiver(
            id="safety-scope-other-caregiver",
            name="Safety Scope Other Caregiver",
            email="safety-scope-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.add(Evidence(
            id="safety-scope-evidence",
            person_id=person.id,
            type="document",
            source_text="A documented event requiring review.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        ))
        db.add(Evidence(
            id="safety-scope-other-evidence",
            person_id=other_person.id,
            type="document",
            source_text="Evidence from another care record.",
            uploaded_by_caregiver_id=other_caregiver.id,
            source_type="document",
        ))
        db.commit()
        db.close()

        client = authorized_client(person.id)
        other_client = authorized_client(other_person.id)
        params = {
            "person_id": person.id,
            "affected_person_id": person.id,
            "trigger": "Documented event requires review",
            "risk_category": "moderate_risk",
            "recommended_reviewer": "caregiver",
            "evidence_ids": ["safety-scope-evidence"],
        }
        created = client.post("/api/v1/escalations", params=params)
        assert created.status_code == 200, created.text
        escalation_id = created.json()["id"]
        assert created.json()["person_id"] == person.id
        assert created.json()["evidence_ids"] == ["safety-scope-evidence"]

        forged_scope = client.post(
            "/api/v1/escalations",
            params={**params, "person_id": other_person.id, "affected_person_id": other_person.id},
        )
        assert forged_scope.status_code == 403, forged_scope.text

        cross_record_evidence = client.post(
            "/api/v1/escalations",
            params={**params, "evidence_ids": ["safety-scope-other-evidence"]},
        )
        assert cross_record_evidence.status_code == 404, cross_record_evidence.text

        listed = client.get(f"/api/v1/escalations/{person.id}")
        assert listed.status_code == 200, listed.text
        assert all(row["person_id"] == person.id for row in listed.json())
        assert other_client.get(f"/api/v1/escalations/{person.id}").status_code == 403
        assert other_client.patch(
            f"/api/v1/escalations/{escalation_id}",
            params={"status": "resolved", "reviewer_identity": "forged-reviewer"},
        ).status_code == 404
        owner_update = client.patch(
            f"/api/v1/escalations/{escalation_id}",
            params={"status": "resolved", "reviewer_identity": "forged-reviewer"},
        )
        assert owner_update.status_code == 200, owner_update.text
        assert owner_update.json()["reviewer_identity"] == caregiver.id
        assert client.get(f"/api/v1/escalations/{other_person.id}").status_code == 403

        unauthenticated = TestClient(app)
        denied = unauthenticated.get(f"/api/v1/escalations/{person.id}")
        assert denied.status_code in (401, 403), denied.text
    finally:
        teardown_db()
        Base.metadata.drop_all(bind=engine)


def test_claim_assessment_all_statuses_require_explicit_evidence_relationships_and_preserve_history():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="claim-status-person", name="Claim Status Person")
        caregiver = Caregiver(
            id="claim-status-caregiver",
            name="Claim Status Caregiver",
            email="claim-status@example.com",
            relationship="Family",
            person_id=person.id,
        )
        claim = CareAssertion(
            id="claim-status-main",
            person_id=person.id,
            subject_type="sleep",
            subject_id="sleep-1",
            field="night_waking",
            assertion_kind="report",
            asserted_value="Three awakenings were reported overnight.",
            source_label="caregiver report",
            source_text="Three awakenings were reported overnight.",
            evidence_ids=[],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
        )
        replacement_claim = CareAssertion(
            id="claim-status-replacement",
            person_id=person.id,
            subject_type="sleep",
            subject_id="sleep-1",
            field="night_waking",
            assertion_kind="report",
            asserted_value="A later report records a different count.",
            source_label="caregiver report",
            source_text="A later report records a different count.",
            evidence_ids=[],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, claim, replacement_claim])
        db.add_all([
            Evidence(
                id="claim-status-evidence-a",
                person_id=person.id,
                type="document",
                source_text="Three awakenings were reported overnight.",
                source_type="document",
                uploaded_by_caregiver_id=caregiver.id,
            ),
            Evidence(
                id="claim-status-evidence-b",
                person_id=person.id,
                type="document",
                source_text="A later source records one awakening.",
                source_type="document",
                uploaded_by_caregiver_id=caregiver.id,
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        endpoint = f"/api/v1/care-continuity/claims/{claim.id}/assessments"
        statuses = [
            ("supported", [{"evidence_id": "claim-status-evidence-a", "relationship": "supports", "source_passage": "Three awakenings were reported overnight."}], None),
            ("partially_supported", [{"evidence_id": "claim-status-evidence-a", "relationship": "partial_support", "source_passage": "Three awakenings were reported overnight."}], None),
            ("contradicted", [{"evidence_id": "claim-status-evidence-b", "relationship": "contradicts", "source_passage": "A later source records one awakening."}], None),
            ("unverified", [], None),
            ("uncertain", [{"evidence_id": "claim-status-evidence-a", "relationship": "neutral", "source_passage": None}], None),
            ("conflicting", [
                {"evidence_id": "claim-status-evidence-a", "relationship": "supports", "source_passage": "Three awakenings were reported overnight."},
                {"evidence_id": "claim-status-evidence-b", "relationship": "contradicts", "source_passage": "A later source records one awakening."},
            ], None),
            ("missing_evidence", [], None),
            ("stale", [{"evidence_id": "claim-status-evidence-a", "relationship": "neutral", "source_passage": None}], None),
            ("superseded", [], replacement_claim.id),
            ("withdrawn", [], None),
        ]
        created_versions = []
        for status, evidence_links, superseded_by in statuses:
            payload = {
                "status": status,
                "rationale": f"Explicit review for status {status}; this is a recorded assessment, not proof of clinical truth.",
                "policy_version": "claim-assessment-v1",
                "evidence_links": evidence_links,
            }
            if superseded_by:
                payload["superseded_by_claim_id"] = superseded_by
            response = client.post(endpoint, json=payload)
            assert response.status_code == 201, f"{status}: {response.status_code} {response.text}"
            created_versions.append(response.json())
        assert [row["status"] for row in created_versions] == [item[0] for item in statuses]
        assert [row["claim_version"] for row in created_versions] == list(range(1, 11))
        assert created_versions[-2]["superseded_by_claim_id"] == replacement_claim.id

        history = client.get(endpoint)
        assert history.status_code == 200, history.text
        assert len(history.json()) == 10
        assert all(row["person_id"] == person.id for row in history.json())

        # The backend must reject a paraphrase as a source quote and must not
        # allow a substantive relationship with no exact source passage.
        wrong_quote = client.post(endpoint, json={
            "status": "supported",
            "rationale": "Attempted to attach a paraphrase as source evidence.",
            "policy_version": "claim-assessment-v1",
            "evidence_links": [{
                "evidence_id": "claim-status-evidence-a",
                "relationship": "supports",
                "source_passage": "Three or more awakenings happened overnight.",
            }],
        })
        assert wrong_quote.status_code == 422, wrong_quote.text

        missing_quote = client.post(endpoint, json={
            "status": "supported",
            "rationale": "Attempted support without a source passage.",
            "policy_version": "claim-assessment-v1",
            "evidence_links": [{
                "evidence_id": "claim-status-evidence-a",
                "relationship": "supports",
                "source_passage": None,
            }],
        })
        assert missing_quote.status_code == 422, missing_quote.text
    finally:
        teardown_db()
        Base.metadata.drop_all(bind=engine)


def test_duplicate_open_clarification_question_is_suppressed_but_answered_question_can_be_reasked():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="question-dedupe-person", name="Question Dedupe Person")
        caregiver = Caregiver(
            id="question-dedupe-caregiver",
            name="Question Dedupe Caregiver",
            email="question-dedupe@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        payload = {
            "person_id": person.id,
            "question": "Was the appointment attended?",
            "subject_type": "appointment",
            "subject_id": "appointment-1",
            "status": "open",
            "priority": "informational",
            "necessity": "not_assessed",
            "evidence_ids": [],
            "context": [{"purpose": "clarify an unestablished event"}],
        }
        created = client.post("/api/v1/uncertainty/questions", json=payload)
        assert created.status_code == 200, created.text
        question_id = created.json()["id"]

        skipped = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"status": "unresolved", "closed_reason": "The caregiver does not know the answer yet."},
        )
        assert skipped.status_code == 200, skipped.text
        assert skipped.json()["status"] == "unresolved"

        duplicate_while_unresolved = client.post(
            "/api/v1/uncertainty/questions",
            json={**payload, "question": " WAS THE appointment attended? "},
        )
        assert duplicate_while_unresolved.status_code == 422, duplicate_while_unresolved.text
        assert "equivalent unanswered clarification question already exists" in duplicate_while_unresolved.text

        reopened = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={"status": "open"},
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["status"] == "open"

        duplicate = client.post(
            "/api/v1/uncertainty/questions",
            json={**payload, "question": " WAS THE appointment attended? "},
        )
        assert duplicate.status_code == 422, duplicate.text
        assert "equivalent unanswered clarification question already exists" in duplicate.text

        answer_source = client.post(
            "/api/v1/evidence",
            json={
                "person_id": person.id,
                "uploaded_by_caregiver_id": caregiver.id,
                "type": "note",
                "evidence_kind": "caregiver_report",
                "epistemic_status": "unverified",
                "source_text": "The reporting caregiver confirms the appointment was attended.",
            },
        )
        assert answer_source.status_code in {200, 201}, answer_source.text
        answer_evidence_id = answer_source.json()["id"]
        answered = client.patch(
            f"/api/v1/uncertainty/questions/{question_id}",
            json={
                "status": "answered",
                "answer": "The caregiver reports that it was attended.",
                "answer_provenance": "Answered by the reporting caregiver during follow-up.",
                "evidence_ids": [answer_evidence_id],
            },
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["status"] == "answered"
        answer_history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert answer_history.status_code == 200, answer_history.text
        assert answer_history.json()[0]["evidence_ids"] == [answer_evidence_id]

        repeated_after_answer = client.post("/api/v1/uncertainty/questions", json=payload)
        assert repeated_after_answer.status_code == 200, repeated_after_answer.text
        assert repeated_after_answer.json()["id"] != question_id
    finally:
        teardown_db()
        Base.metadata.drop_all(bind=engine)


def test_conflict_partial_resolution_reopen_and_history_preserve_unresolved_remainder():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-conflict-lifecycle", name="Conflict lifecycle test")
        caregiver = Caregiver(
            id="caregiver-conflict-lifecycle",
            name="Conflict lifecycle caregiver",
            email="conflict-lifecycle@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.flush()
        db.add_all([
            Evidence(
                id="conflict-evidence-a",
                person_id=person.id,
                type="document",
                source_text="Discharge summary lists the medicine as discontinued.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="document",
            ),
            Evidence(
                id="conflict-evidence-b",
                person_id=person.id,
                type="document",
                source_text="The current medication list still includes the medicine.",
                uploaded_by_caregiver_id=caregiver.id,
                source_type="document",
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        created = client.post(
            "/api/v1/uncertainty/contradictions",
            json={
                "person_id": person.id,
                "subject_type": "medication",
                "subject_id": "medicine-1",
                "field": "status",
                "description": "One source says discontinued while another lists the medicine as current.",
                "evidence_ids": ["conflict-evidence-a", "conflict-evidence-b"],
            },
        )
        assert created.status_code == 200, created.text
        conflict_id = created.json()["id"]
        assert created.json()["resolution_status"] == "unresolved"

        # A partial resolution must name both the resolved scope and what remains
        # unresolved; rejected transitions must not append history or mutate state.
        rejected = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_scope": "The discharge summary date was confirmed.",
                "resolution_notes": "Attempted partial resolution without preserving the remaining question.",
                "evidence_ids": ["conflict-evidence-a"],
            },
        )
        assert rejected.status_code == 422, rejected.text
        unchanged = client.get(
            f"/api/v1/uncertainty/contradictions/person-conflict-lifecycle"
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()[0]["resolution_status"] == "unresolved"
        history = client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history")
        assert history.status_code == 200, history.text
        assert history.json() == []

        partial = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "partially_resolved",
                "resolution_scope": "The discharge summary date was confirmed.",
                "unresolved_remainder": "Whether the medicine is currently prescribed remains unconfirmed.",
                "resolution_notes": "Only the document date was reconciled; medication status remains disputed.",
                "evidence_ids": ["conflict-evidence-a"],
            },
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["resolution_status"] == "partially_resolved"
        assert partial.json()["resolution_scope"] == "The discharge summary date was confirmed."
        assert partial.json()["unresolved_remainder"] == "Whether the medicine is currently prescribed remains unconfirmed."

        history = client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history")
        assert history.status_code == 200, history.text
        assert len(history.json()) == 1
        assert history.json()[0]["previous_status"] == "unresolved"
        assert history.json()[0]["new_status"] == "partially_resolved"
        assert history.json()[0]["resolution_scope"] == "The discharge summary date was confirmed."
        assert history.json()[0]["unresolved_remainder"] == "Whether the medicine is currently prescribed remains unconfirmed."

        reopened = client.patch(
            f"/api/v1/uncertainty/contradictions/{conflict_id}",
            json={
                "resolution_status": "reopened",
                "resolution_notes": "A new caregiver report disputes the partial interpretation; preserve the prior audit trail.",
            },
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["resolution_status"] == "reopened"
        assert reopened.json()["resolution_scope"] is None
        assert reopened.json()["unresolved_remainder"] is None

        history = client.get(f"/api/v1/uncertainty/contradictions/{conflict_id}/history")
        assert history.status_code == 200, history.text
        assert len(history.json()) == 2
        assert [(event["previous_status"], event["new_status"]) for event in history.json()] == [
            ("unresolved", "partially_resolved"),
            ("partially_resolved", "reopened"),
        ]
        assert history.json()[0]["unresolved_remainder"] == "Whether the medicine is currently prescribed remains unconfirmed."
    finally:
        teardown_db()


def test_companion_conversation_is_private_to_its_authenticated_caregiver():
    """Care-record membership does not grant access to another caregiver's private Companion thread."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="companion-private-person", name="Shared Care Record")
        caregiver_a = Caregiver(
            id="companion-private-a", name="Caregiver A",
            email="companion-private-a@example.com", relationship="Family",
            person_id=person.id,
        )
        caregiver_b = Caregiver(
            id="companion-private-b", name="Caregiver B",
            email="companion-private-b@example.com", relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="companion-private-other", name="Other Care Record")
        other_caregiver = Caregiver(
            id="companion-private-other-caregiver", name="Other Caregiver",
            email="companion-private-other@example.com", relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver_a, caregiver_b, other_person, other_caregiver])
        db.flush()

        session = ConversationSession(
            id="private-companion-session", person_id=person.id,
            caregiver_id=caregiver_a.id, title="Private caregiver conversation",
            status="active",
        )
        db.add(session)
        db.flush()
        db.add(ConversationMessage(
            id="private-companion-message", session_id=session.id,
            role="user", content="Private caregiver context.",
            evidence_ids_json="[]", source_record_refs_json="[]",
        ))

        tokens = {}
        for caregiver in (caregiver_a, caregiver_b, other_caregiver):
            token = f"private-conversation-token-{caregiver.id}"
            tokens[caregiver.id] = token
            db.add(AnonymousSession(
                person_id=caregiver.person_id,
                caregiver_id=caregiver.id,
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            ))
        db.commit()
        db.close()

        client = TestClient(app)
        owner = client.get(
            "/api/v1/conversation/private-companion-session",
            headers={"Authorization": "Bearer " + tokens["companion-private-a"]},
        )
        assert owner.status_code == 200, owner.text
        assert owner.json()["messages"][0]["text"] == "Private caregiver context."

        same_record_other_caregiver = client.get(
            "/api/v1/conversation/private-companion-session",
            headers={"Authorization": "Bearer " + tokens["companion-private-b"]},
        )
        assert same_record_other_caregiver.status_code == 404

        different_person = client.get(
            "/api/v1/conversation/private-companion-session",
            headers={"Authorization": "Bearer " + tokens["companion-private-other-caregiver"]},
        )
        assert different_person.status_code == 404
    finally:
        teardown_db()



def test_historical_unresolved_questions_remain_retrievable_outside_recent_windows():
    """An old unanswered care question must not disappear behind recent-history limits."""
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="historical-question-person", name="Historical Question Person")
        caregiver = Caregiver(
            id="historical-question-caregiver", name="Historical Question Caregiver",
            email="historical-question@example.com", relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="historical-question-other", name="Other Question Person")
        other_caregiver = Caregiver(
            id="historical-question-other-caregiver", name="Other Question Caregiver",
            email="historical-question-other@example.com", relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([person, caregiver, other_person, other_caregiver])
        db.flush()
        historical_evidence = Evidence(
            id="historical-question-evidence",
            person_id=person.id,
            type="caregiver_report",
            source_text="The mobility assessment outcome is still needed for the current care handoff.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        )
        db.add(historical_evidence)
        db.flush()
        historical_question_context = [{
            "decision_dependency": "The care handoff cannot state the assessment outcome without this answer."
        }]
        old_asked_at = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
        old_question = OpenQuestion(
            id="historical-open-question",
            person_id=person.id,
            question="Was the mobility assessment ever completed?",
            subject_type="appointment",
            subject_id="mobility-assessment",
            status="open",
            necessity="necessary",
            necessity_rationale="The mobility assessment outcome is needed for the current care handoff.",
            evidence_ids=[historical_evidence.id],
            context=historical_question_context,
            asked_at=old_asked_at,
            created_by_caregiver_id=caregiver.id,
        )
        recent_question = OpenQuestion(
            id="recent-open-question",
            person_id=person.id,
            question="Is the follow-up appointment confirmed?",
            subject_type="appointment",
            subject_id="follow-up",
            status="open",
            necessity="necessary",
            necessity_rationale="The follow-up appointment status is needed for the current care handoff.",
            evidence_ids=[historical_evidence.id],
            context=historical_question_context,
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([old_question, recent_question])
        db.commit()
        db.close()

        client = authorized_client("historical-question-person")
        other_client = authorized_client("historical-question-other")
        response = client.get(f"/api/v1/uncertainty/questions/{person.id}?status=open")
        assert response.status_code == 200, response.text
        by_id = {item["id"]: item for item in response.json()}
        assert set(by_id) == {"historical-open-question", "recent-open-question"}
        assert by_id["historical-open-question"]["status"] == "open"
        assert by_id["historical-open-question"]["asked_at"].startswith("2024-01-15T09:30:00")

        foreign = other_client.get(
            f"/api/v1/uncertainty/questions/{person.id}?status=open"
        )
        assert foreign.status_code == 403
    finally:
        teardown_db()


def test_clarification_review_accepts_report_without_marking_it_verified():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import EvidenceClaimAssessment, EvidenceClaimDependency
        from app.models.uncertainty import OpenQuestionReviewEvent
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="review-person-1", name="Review Person")
        caregiver = Caregiver(
            id="review-caregiver-1", name="Review Caregiver",
            email="review-caregiver-1@example.com", relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="review-evidence-1", person_id=person.id, type="caregiver_report",
            source_text="Caregiver reports the appointment took place on 12 October.",
            uploaded_by_caregiver_id=caregiver.id,
        )
        source_claim = CareAssertion(
            id="review-source-claim-1", person_id=person.id,
            subject_type="appointment", subject_id="review-appointment-1",
            field="attendance", assertion_kind="current_state",
            asserted_value="Attendance not yet established",
            source_label="Existing care record", source_text="Attendance remains unclear.",
            evidence_ids=[], assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, evidence, source_claim])
        db.commit()
        db.close()

        client = authorized_client("review-person-1")
        other_person = Person(id="review-person-2", name="Other Review Person")
        db = TestingSessionLocal()
        other_caregiver = Caregiver(
            id="review-caregiver-2", name="Other Review Caregiver",
            email="review-caregiver-2@example.com", relationship="Family",
            person_id=other_person.id,
        )
        db.add_all([other_person, other_caregiver])
        db.commit()
        db.close()
        other_client = authorized_client("review-person-2")

        created = client.post("/api/v1/uncertainty/questions", json={
            "person_id": "review-person-1",
            "question": "Was the appointment attended?",
            "subject_type": "appointment",
            "subject_id": "review-appointment-1",
            "claim_id": "review-source-claim-1",
            "priority": "high",
            "necessity": "necessary",
            "necessity_rationale": "Attendance changes the follow-up context.",
            "evidence_ids": ["review-evidence-1"],
            "context": [{
                "decision_dependency": "The follow-up plan depends on whether the appointment occurred."
            }],
        })
        assert created.status_code == 200, created.text
        question_id = created.json()["id"]

        answered = client.patch(f"/api/v1/uncertainty/questions/{question_id}", json={
            "status": "answered",
            "answer": "The caregiver reports that the appointment was attended on 12 October.",
            "answer_provenance": "Direct report from the family caregiver.",
            "evidence_ids": ["review-evidence-1"],
        })
        assert answered.status_code == 200, answered.text

        missing_review_window = client.post(f"/api/v1/uncertainty/questions/{question_id}/reviews", json={
            "decision": "accepted",
            "rationale": "The attributed report and linked source were reviewed and admitted to the care record as an unverified report.",
        })
        assert missing_review_window.status_code == 422, missing_review_window.text

        accepted = client.post(f"/api/v1/uncertainty/questions/{question_id}/reviews", json={
            "decision": "accepted",
            "rationale": "The attributed report and linked source were reviewed and admitted to the care record as an unverified report.",
            "review_after_days": 30,
        })
        assert accepted.status_code == 200, accepted.text
        review = accepted.json()
        assert review["decision"] == "accepted"
        assert review["reviewer_caregiver_id"] == "review-caregiver-1"
        assert review["resulting_claim_id"]
        assert review["claim_assessment_id"]

        repeated = client.post(f"/api/v1/uncertainty/questions/{question_id}/reviews", json={
            "decision": "accepted",
            "rationale": "The attributed report and linked source were reviewed and admitted to the care record as an unverified report.",
            "review_after_days": 30,
        })
        assert repeated.status_code == 200
        assert repeated.json()["id"] == review["id"]

        history = client.get(f"/api/v1/uncertainty/questions/{question_id}/review-history")
        assert history.status_code == 200
        assert len(history.json()) == 1
        assert other_client.get(f"/api/v1/uncertainty/questions/{question_id}/review-history").status_code == 404

        # The accepted clarification must be retrievable through the existing
        # person-scoped care-continuity assertion source used by LCR projections.
        assertions = client.get("/api/v1/care-continuity/assertions/review-person-1")
        assert assertions.status_code == 200, assertions.text
        assert any(item["id"] == review["resulting_claim_id"] for item in assertions.json())
        lcr_context = client.get("/api/v1/care-continuity/context")
        assert lcr_context.status_code == 200, lcr_context.text
        assert any(
            item["id"] == review["resulting_claim_id"]
            for item in lcr_context.json()["sources"]["assertions"]["items"]
        )

        db = TestingSessionLocal()
        try:
            resulting = db.query(CareAssertion).filter(CareAssertion.id == review["resulting_claim_id"]).one()
            assert resulting.person_id == "review-person-1"
            assert resulting.asserted_value == "The caregiver reports that the appointment was attended on 12 October."
            assert resulting.assertion_status == "reported"
            assert resulting.review_after_days == 30
            assert resulting.created_by_caregiver_id == "review-caregiver-1"
            assert "Direct report from the family caregiver." in resulting.source_text

            assessment = db.query(EvidenceClaimAssessment).filter(EvidenceClaimAssessment.id == review["claim_assessment_id"]).one()
            assert assessment.status == "unverified"
            assert assessment.reviewed_by_caregiver_id == "review-caregiver-1"
            assert assessment.evidence_links[0]["evidence_id"] == "review-evidence-1"
            assert assessment.evidence_links[0]["relationship"] == "neutral"

            dependency = db.query(EvidenceClaimDependency).filter(
                EvidenceClaimDependency.claim_id == resulting.id,
                EvidenceClaimDependency.depends_on_claim_id == "review-source-claim-1",
                EvidenceClaimDependency.dependency_type == "qualifies",
            ).one()
            assert dependency.created_by_caregiver_id == "review-caregiver-1"

            review_event = db.query(OpenQuestionReviewEvent).filter(OpenQuestionReviewEvent.id == review["id"]).one()
            with pytest.raises(ValueError, match="append-only"):
                review_event.rationale = "Attempted review history rewrite"
                db.commit()
            db.rollback()
        finally:
            db.close()

        rejected_without_answer_scope = other_client.post(
            f"/api/v1/uncertainty/questions/{question_id}/reviews",
            json={"decision": "rejected", "rationale": "Not authorized."},
        )
        assert rejected_without_answer_scope.status_code == 404

        # A second question with a persisted answer can be rejected without
        # creating a care assertion or claim assessment.
        second_question = client.post("/api/v1/uncertainty/questions", json={
            "person_id": "review-person-1",
            "question": "Was transport arranged?",
            "subject_type": "appointment",
            "subject_id": "review-appointment-1",
            "claim_id": "review-source-claim-1",
        })
        assert second_question.status_code == 200, second_question.text
        second_id = second_question.json()["id"]
        second_answer = client.patch(f"/api/v1/uncertainty/questions/{second_id}", json={
            "status": "answered",
            "answer": "Transport details are not available.",
            "answer_provenance": "Caregiver could not confirm transport arrangements.",
            "evidence_ids": ["review-evidence-1"],
        })
        assert second_answer.status_code == 200, second_answer.text
        rejected = client.post(f"/api/v1/uncertainty/questions/{second_id}/reviews", json={
            "decision": "rejected",
            "rationale": "The linked source does not support the submitted transport statement.",
        })
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["resulting_claim_id"] is None
        assert rejected.json()["claim_assessment_id"] is None
        conflict = client.post(f"/api/v1/uncertainty/questions/{second_id}/reviews", json={
            "decision": "needs_clarification",
            "rationale": "A different decision for the same answer version must conflict.",
        })
        assert conflict.status_code == 409
        assert client.get(f"/api/v1/uncertainty/questions/{second_id}/review-history").json()[0]["decision"] == "rejected"
    finally:
        teardown_db()


def test_answer_without_upload_saves_the_caregiver_report_as_source_evidence():
    setup_db()
    try:
        from app.core.database import get_db
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-answer-source", name="Answer Source Person")
        caregiver = Caregiver(
            id="caregiver-answer-source",
            name="Answer Source Caregiver",
            email="answer-source@example.com",
            relationship="Family",
            person_id=person.id,
        )
        db.add_all([person, caregiver])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        created = client.post("/api/v1/uncertainty/questions", json={
            "person_id": person.id,
            "question": "Has her weight been measured recently?",
            "subject_type": "person",
            "subject_id": person.id,
        })
        assert created.status_code == 200, created.text
        question_id = created.json()["id"]

        answer_text = "Her weight was measured at the clinic this morning."
        answer_source = "I was present when the nurse measured it."
        answered = client.patch(f"/api/v1/uncertainty/questions/{question_id}", json={
            "status": "answered",
            "answer": answer_text,
            "answer_provenance": answer_source,
        })
        assert answered.status_code == 200, answered.text
        assert answered.json()["status"] == "answered"

        history = client.get(f"/api/v1/uncertainty/questions/{question_id}/answer-history")
        assert history.status_code == 200, history.text
        answer_event = history.json()[0]
        assert answer_event["answer"] == answer_text
        assert answer_event["answer_provenance"] == answer_source
        assert len(answer_event["evidence_ids"]) == 1

        db = TestingSessionLocal()
        try:
            evidence = db.query(Evidence).filter(
                Evidence.id == answer_event["evidence_ids"][0],
                Evidence.person_id == person.id,
            ).one()
            assert evidence.evidence_kind == "caregiver_report"
            assert evidence.epistemic_status == "caregiver_reported"
            assert evidence.uploaded_by_caregiver_id == caregiver.id
            assert evidence.source_text == f"Answer: {answer_text}\n\nProvided by: {answer_source}"
        finally:
            db.close()
    finally:
        teardown_db()

def test_source_correction_requires_review_for_direct_and_dependent_claims():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import (
            CareAssertion,
            EvidenceClaimAssessment,
            EvidenceClaimDependency,
            EvidenceObservation,
        )
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="impact-correction-person", name="Correction Person")
        caregiver = Caregiver(
            id="impact-correction-caregiver",
            name="Correction Caregiver",
            email="impact-correction@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="impact-correction-evidence",
            person_id=person.id,
            type="document",
            source_text="She felt unsteady near the kitchen.",
            uploaded_by_caregiver_id=caregiver.id,
        )
        source_claim = CareAssertion(
            id="impact-correction-source-claim",
            person_id=person.id,
            subject_type="mobility",
            subject_id="mobility-1",
            field="unsteadiness",
            assertion_kind="report",
            asserted_value="She felt unsteady near the kitchen.",
            source_label="caregiver report",
            source_text="She felt unsteady near the kitchen.",
            evidence_ids=[evidence.id],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
        )
        dependent_claim = CareAssertion(
            id="impact-correction-dependent-claim",
            person_id=person.id,
            subject_type="mobility",
            subject_id="mobility-1",
            field="monthly_unsteadiness_count",
            assertion_kind="derived",
            asserted_value="Three incidents this month",
            source_label="derived from source claim",
            source_text="Three incidents this month",
            evidence_ids=[],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
        )
        assessed_at = datetime.now(timezone.utc) - timedelta(days=2)
        source_assessment = EvidenceClaimAssessment(
            person_id=person.id,
            claim_id=source_claim.id,
            claim_version=1,
            status="supported",
            rationale="Earlier review before the source correction.",
            policy_version="claim-assessment-v1",
            evidence_links=[{
                "evidence_id": evidence.id,
                "relationship": "supports",
                "source_passage": "She felt unsteady near the kitchen.",
            }],
            reviewed_by_caregiver_id=caregiver.id,
            created_at=assessed_at,
        )
        dependent_assessment = EvidenceClaimAssessment(
            person_id=person.id,
            claim_id=dependent_claim.id,
            claim_version=1,
            status="supported",
            rationale="Earlier dependent review.",
            policy_version="claim-assessment-v1",
            evidence_links=[],
            reviewed_by_caregiver_id=caregiver.id,
            created_at=assessed_at,
        )
        original_observation = EvidenceObservation(
            id="impact-correction-observation-original",
            person_id=person.id,
            evidence_id=evidence.id,
            observation_kind="caregiver_report",
            epistemic_status="reported",
            subject_label="person receiving care",
            attribute="unsteadiness",
            value_text="unsteady",
            source_passage="She felt unsteady near the kitchen.",
            source_span_start=0,
            source_span_end=len("She felt unsteady near the kitchen."),
            extraction_method="human_entered",
            extraction_version="test-v1",
            created_by_caregiver_id=caregiver.id,
            recorded_at=assessed_at - timedelta(days=1),
        )
        corrected_observation = EvidenceObservation(
            id="impact-correction-observation-corrected",
            person_id=person.id,
            evidence_id=evidence.id,
            supersedes_observation_id=original_observation.id,
            correction_rationale="The caregiver corrected the original interpretation.",
            observation_kind="caregiver_report",
            epistemic_status="reported",
            subject_label="person receiving care",
            attribute="unsteadiness",
            value_text="steady",
            source_passage="She felt unsteady near the kitchen.",
            source_span_start=0,
            source_span_end=len("She felt unsteady near the kitchen."),
            extraction_method="human_entered",
            extraction_version="test-v1",
            created_by_caregiver_id=caregiver.id,
            recorded_at=datetime.now(timezone.utc),
        )
        dependency = EvidenceClaimDependency(
            person_id=person.id,
            claim_id=dependent_claim.id,
            depends_on_claim_id=source_claim.id,
            dependency_type="derived_from",
            rationale="The monthly count depends on the source observation.",
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([
            person, caregiver, evidence, source_claim, dependent_claim,
            source_assessment, dependent_assessment,
            original_observation, corrected_observation, dependency,
        ])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        direct = client.get(
            f"/api/v1/care-continuity/claims/{source_claim.id}/assessment-impact"
        )
        assert direct.status_code == 200, direct.text
        direct_body = direct.json()
        assert direct_body["review_required"] is True
        assert "source_observation_added_or_corrected_after_assessment" in direct_body["reasons"]
        assert "impact-correction-observation-corrected" in direct_body["changed_observation_ids"]

        dependent = client.get(
            f"/api/v1/care-continuity/claims/{dependent_claim.id}/assessment-impact"
        )
        assert dependent.status_code == 200, dependent.text
        dependent_body = dependent.json()
        assert dependent_body["review_required"] is True
        assert "impact-correction-source-claim" in dependent_body["changed_prerequisite_claim_ids"]
        assert "prerequisite_claim_source_or_assessment_changed" in dependent_body["reasons"]
    finally:
        teardown_db()


def test_care_event_correction_reopens_direct_and_dependent_claim_assessments():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care import Correction
        from app.models.care_continuity import (
            CareAssertion,
            EvidenceClaimAssessment,
            EvidenceClaimDependency,
        )
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="event-correction-person", name="Event Correction Person")
        caregiver = Caregiver(
            id="event-correction-caregiver",
            name="Event Correction Caregiver",
            email="event-correction@example.com",
            relationship="Family",
            person_id=person.id,
        )
        assessed_at = datetime.now(timezone.utc) - timedelta(days=2)
        event = CareEvent(
            id="event-correction-source",
            person_id=person.id,
            event_type="observation",
            status="recorded",
            occurred_at=assessed_at - timedelta(days=1),
            title="Caregiver observation",
            description="Caregiver reported unsteadiness.",
            created_by_caregiver_id=caregiver.id,
        )
        source_claim = CareAssertion(
            id="event-correction-source-claim",
            person_id=person.id,
            subject_type="mobility",
            subject_id="mobility-event-correction",
            field="unsteadiness",
            assertion_kind="time_bound_observation",
            asserted_value="Caregiver reported unsteadiness.",
            source_label="caregiver report",
            source_text="Caregiver reported unsteadiness.",
            source_event_id=event.id,
            evidence_ids=[],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
            recorded_at=assessed_at - timedelta(days=1),
        )
        dependent_claim = CareAssertion(
            id="event-correction-dependent-claim",
            person_id=person.id,
            subject_type="mobility",
            subject_id="mobility-event-correction",
            field="monthly_unsteadiness_count",
            assertion_kind="derived",
            asserted_value="Three incidents this month",
            source_label="derived from source claim",
            source_text="Three incidents this month",
            evidence_ids=[],
            assertion_status="reported",
            created_by_caregiver_id=caregiver.id,
            recorded_at=assessed_at - timedelta(days=1),
        )
        source_assessment = EvidenceClaimAssessment(
            person_id=person.id,
            claim_id=source_claim.id,
            claim_version=1,
            status="supported",
            rationale="Reviewed before the event correction.",
            policy_version="claim-assessment-v1",
            evidence_links=[],
            reviewed_by_caregiver_id=caregiver.id,
            created_at=assessed_at,
        )
        dependent_assessment = EvidenceClaimAssessment(
            person_id=person.id,
            claim_id=dependent_claim.id,
            claim_version=1,
            status="supported",
            rationale="Reviewed before the source correction.",
            policy_version="claim-assessment-v1",
            evidence_links=[],
            reviewed_by_caregiver_id=caregiver.id,
            created_at=assessed_at,
        )
        dependency = EvidenceClaimDependency(
            person_id=person.id,
            claim_id=dependent_claim.id,
            depends_on_claim_id=source_claim.id,
            dependency_type="derived_from",
            rationale="The derived count depends on the corrected event.",
            created_by_caregiver_id=caregiver.id,
        )
        correction = Correction(
            id="event-correction-record",
            person_id=person.id,
            target_type="care_event",
            target_id=event.id,
            original_text="Caregiver reported unsteadiness.",
            corrected_text="Caregiver clarified the event was not unsteadiness.",
            reason="The original event wording was inaccurate.",
            caregiver_id=caregiver.id,
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([
            person, caregiver, event, source_claim, dependent_claim,
            source_assessment, dependent_assessment, dependency, correction,
        ])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        direct = client.get(
            f"/api/v1/care-continuity/claims/{source_claim.id}/assessment-impact"
        )
        assert direct.status_code == 200, direct.text
        direct_body = direct.json()
        assert direct_body["review_required"] is True
        assert "source_event_corrected_after_assessment" in direct_body["reasons"]

        dependent = client.get(
            f"/api/v1/care-continuity/claims/{dependent_claim.id}/assessment-impact"
        )
        assert dependent.status_code == 200, dependent.text
        dependent_body = dependent.json()
        assert dependent_body["review_required"] is True
        assert source_claim.id in dependent_body["changed_prerequisite_claim_ids"]
        assert "prerequisite_claim_source_or_assessment_changed" in dependent_body["reasons"]
    finally:
        teardown_db()


def test_normal_reconciliation_keeps_unassessed_candidates_internal():
    setup_db()
    try:
        from app.core.database import get_db
        from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence
        from app.services.care_continuity import next_best_questions
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        now = datetime.now(timezone.utc)
        person = Person(id="person-question-reconcile", name="Question Reconciliation Person")
        caregiver = Caregiver(
            id="caregiver-question-reconcile",
            name="Question Reconciliation Caregiver",
            email="question-reconcile@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="evidence-question-reconcile",
            person_id=person.id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="The caregiver reported that her appetite has changed recently.",
            uploaded_by_caregiver_id=caregiver.id,
            reported_by_caregiver_id=caregiver.id,
        )
        reported = CareAssertion(
            id="assertion-question-reconcile-reported",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="appetite",
            assertion_kind="current_state",
            asserted_value="Appetite has changed recently",
            source_label="Caregiver report",
            source_text=evidence.source_text,
            evidence_ids=[evidence.id],
            assertion_status="reported",
            observed_at=now,
            review_after_days=30,
            created_by_caregiver_id=caregiver.id,
        )
        accepted = CareAssertion(
            id="assertion-question-reconcile-accepted",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="usual_bedtime",
            assertion_kind="current_state",
            asserted_value="Usually goes to bed at 9 PM",
            source_label="Caregiver-reviewed report",
            source_text="Caregiver confirmed usual bedtime.",
            evidence_ids=[],
            assertion_status="accepted",
            observed_at=now,
            review_after_days=30,
            created_by_caregiver_id=caregiver.id,
        )
        time_bound = CareAssertion(
            id="assertion-question-reconcile-time-bound",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="sleep_observation",
            assertion_kind="time_bound_observation",
            asserted_value="Slept poorly last night",
            source_label="Caregiver report",
            source_text="Slept poorly last night.",
            evidence_ids=[evidence.id],
            assertion_status="caregiver_reviewed",
            observed_at=now,
            created_by_caregiver_id=caregiver.id,
        )
        gap = InformationGap(
            id="gap-question-reconcile",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="recent_weight",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.ABSENT,
            priority=Priority.MEDIUM,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="No recent weight measurement is recorded.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, evidence, reported, accepted, time_bound, gap])
        db.commit()

        # The ordinary shared reconciliation path is responsible for refreshing
        # source-linked questions; no separate caregiver action is required.
        reconcile_person_change_intelligence(db, person.id)
        db.commit()
        first = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).all()
        # A contradiction and a missing measurement remain in internal care
        # intelligence; neither automatically becomes caregiver work.
        assert first == []
        candidates = next_best_questions(db, person.id, persist=False)
        assert any(item["source_kind"] == "information_gap" and item["necessity"] == "not_assessed" for item in candidates)
        assert db.query(InformationGap).filter_by(id=gap.id).one().lifecycle_status == GapLifecycleStatus.OPEN

        # Reconciliation remains idempotent and does not create caregiver questions.
        reconcile_person_change_intelligence(db, person.id)
        db.commit()
        second_count = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).count()
        assert second_count == 0
        assert not any("usual_bedtime" in row.question for row in db.query(OpenQuestion).filter_by(person_id=person.id).all())
        assert not any("sleep_observation" in row.question for row in db.query(OpenQuestion).filter_by(person_id=person.id).all())
    finally:
        teardown_db()

def test_qwen_cross_workflow_claim_sources_keep_events_documents_and_appointments_separate():
    setup_db()
    try:
        from app.core.database import get_db
        from app.api.collaboration import _enrich_qwen_claim_source_records
        from app.models.care import CareEvent
        from app.models.capture_analysis import CaptureAnalysis
        from app.models.time_location import Appointment
        from app.services.claim_provenance import context_lines_to_source_records
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="qwen-provenance-person", name="Qwen Provenance Person")
        caregiver = Caregiver(
            id="qwen-provenance-caregiver",
            name="Qwen Provenance Caregiver",
            email="qwen-provenance@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="qwen-provenance-evidence",
            person_id=person.id,
            type="document",
            source_text="She felt unsteady near the kitchen.",
            uploaded_by_caregiver_id=caregiver.id,
            reported_by_label="Daughter",
            original_author="Daughter",
            extra_metadata='{"filename":"family-note.pdf"}',
        )
        event = CareEvent(
            id="qwen-provenance-event",
            person_id=person.id,
            event_type="mobility",
            status="recorded",
            occurred_at=datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
            title="Unsteadiness near the kitchen",
            description="She felt unsteady near the kitchen.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        analysis = CaptureAnalysis(
            id="qwen-provenance-analysis",
            person_id=person.id,
            caregiver_id=caregiver.id,
            source_evidence_id=evidence.id,
            source_text="She felt unsteady near the kitchen.",
            extraction_status="available",
            candidates={"kind": "document_extraction_v1", "filename": "family-note.pdf"},
        )
        appointment = Appointment(
            id="qwen-provenance-appointment",
            person_id=person.id,
            title="Mobility review",
            scheduled_at=datetime(2026, 11, 18, 10, tzinfo=timezone.utc),
            status="scheduled",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, evidence, event, analysis, appointment])
        db.commit()

        records = context_lines_to_source_records([
            "Recorded care event [qwen-provenance-event]: Unsteadiness near the kitchen — She felt unsteady near the kitchen.",
            "Persisted extracted document [qwen-provenance-analysis] from evidence [qwen-provenance-evidence]: extracted_text=She felt unsteady near the kitchen.",
            "Recorded appointment [qwen-provenance-appointment]: Mobility review; status=scheduled; scheduled=2026-11-18T10:00:00+00:00",
        ])
        enriched = _enrich_qwen_claim_source_records(db, person.id, records)
        by_type = {item["record_type"]: item for item in enriched}

        event_source = by_type["care_event"]
        assert event_source["record_id"] == event.id
        assert event_source["entered_by"] == caregiver.name
        assert event_source["related_evidence"][0]["label"] == "family-note.pdf"
        assert event_source["related_evidence"][0]["reported_by"] == "Daughter"

        document_source = by_type["document_extraction"]
        assert document_source["record_id"] == analysis.id
        assert document_source["related_evidence"][0]["record_id"] == evidence.id

        appointment_source = by_type["appointment"]
        assert appointment_source["record_id"] == appointment.id
        assert appointment_source["entered_by"] == caregiver.name
        assert appointment_source["related_evidence"][0]["record_id"] == evidence.id
        db.close()
    finally:
        teardown_db()

def test_monthly_incident_aggregate_uses_person_scoped_events_and_blocks_corrected_sources():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care import CareEvent
        from app.models.care_continuity import EvidenceObservation
        from app.api.collaboration import _requested_event_aggregates
        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="monthly-aggregate-person", name="Monthly Aggregate Person")
        caregiver = Caregiver(
            id="monthly-aggregate-caregiver",
            name="Monthly Aggregate Caregiver",
            email="monthly-aggregate@example.com",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        evidence = Evidence(
            id="monthly-aggregate-evidence",
            person_id=person.id,
            type="caregiver_report",
            source_text="She felt unsteady near the kitchen.",
            uploaded_by_caregiver_id=caregiver.id,
        )
        now = datetime.now(timezone.utc)
        event_rows = [
            CareEvent(
                id="monthly-aggregate-event-1",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=now - timedelta(minutes=3),
                title="Unsteadiness near the kitchen",
                description="She felt unsteady.",
                evidence_ids=[evidence.id],
                created_by_caregiver_id=caregiver.id,
            ),
            CareEvent(
                id="monthly-aggregate-event-2",
                person_id=person.id,
                event_type="mobility",
                status="recorded",
                occurred_at=now - timedelta(minutes=2),
                title="Lost balance while standing",
                description="She lost balance.",
                created_by_caregiver_id=caregiver.id,
            ),
            CareEvent(
                id="monthly-aggregate-event-3",
                person_id=person.id,
                event_type="mobility",
                status="completed",
                occurred_at=now - timedelta(minutes=1),
                title="Stumbled near the bathroom",
                description="She stumbled.",
                created_by_caregiver_id=caregiver.id,
            ),
        ]
        db.add_all([person, caregiver, evidence, *event_rows])
        db.commit()

        question = "How many incidents of unsteadiness has my mother had this month?"
        initial = _requested_event_aggregates(db, person.id, caregiver.id, question)
        assert initial[0]["status"] == "validated"
        assert initial[0]["count"] == 3
        assert len(initial[0]["source_references"]) == 3

        original_text = evidence.source_text
        original_observation = EvidenceObservation(
            id="monthly-aggregate-observation-original",
            person_id=person.id,
            evidence_id=evidence.id,
            observation_kind="caregiver_report",
            epistemic_status="reported",
            subject_label="person receiving care",
            attribute="unsteadiness",
            value_text="unsteady",
            source_passage=original_text,
            source_span_start=0,
            source_span_end=len(original_text),
            extraction_method="human_entered",
            extraction_version="test-v1",
            created_by_caregiver_id=caregiver.id,
            recorded_at=now - timedelta(seconds=30),
        )
        corrected_observation = EvidenceObservation(
            id="monthly-aggregate-observation-corrected",
            person_id=person.id,
            evidence_id=evidence.id,
            supersedes_observation_id=original_observation.id,
            correction_rationale="The caregiver corrected the source interpretation.",
            observation_kind="caregiver_report",
            epistemic_status="reported",
            subject_label="person receiving care",
            attribute="unsteadiness",
            value_text="steady",
            source_passage=original_text,
            source_span_start=0,
            source_span_end=len(original_text),
            extraction_method="human_entered",
            extraction_version="test-v1",
            created_by_caregiver_id=caregiver.id,
            recorded_at=now,
        )
        db.add_all([original_observation, corrected_observation])
        db.commit()
        after_correction = _requested_event_aggregates(db, person.id, caregiver.id, question)
        assert after_correction[0]["status"] == "source_correction_requires_review"
        assert after_correction[0]["count"] is None
    finally:
        teardown_db()


def test_source_extraction_questions_are_saved_with_exact_person_scoped_source_links():
    setup_db()
    try:
        from app.core.database import get_db
        from app.services.uncertainty import persist_source_clarification_questions
        from app.models.care_continuity import CareStateRevision
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person = Person(id="person-extracted-question", name="Extracted Question Person")
        caregiver = Caregiver(
            id="caregiver-extracted-question",
            name="Extracted Question Caregiver",
            email="extracted-question@example.com",
            relationship="Family",
            person_id=person.id,
        )
        reviewing_caregiver = Caregiver(
            id="caregiver-document-question-reviewer",
            name="Document Question Reviewer",
            email="document-question-reviewer@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="evidence-extracted-question",
            person_id=person.id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="She has been waking several times overnight.",
            uploaded_by_caregiver_id=caregiver.id,
            reported_by_caregiver_id=caregiver.id,
        )
        event = CareEvent(
            id="event-extracted-question",
            person_id=person.id,
            event_type="daily_care",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Daily care report",
            description="She has been waking several times overnight.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, reviewing_caregiver, evidence, event])
        db.commit()

        questions = [
            "How many times did she wake overnight?",
            "Only the first 25 pages were processed; more pages need review.",
            "How long has this been happening?",
            "How many times did she wake overnight?",
        ]
        first = persist_source_clarification_questions(
            db,
            person_id=person.id,
            caregiver_id=caregiver.id,
            source_kind="daily_capture_extraction",
            source_record_type="care_event",
            source_record_id=event.id,
            evidence_ids=[evidence.id],
            questions=questions,
        )
        db.commit()
        assert len(first) == 2
        assert all(row.person_id == person.id for row in first)
        assert all(row.status == "open" for row in first)
        assert all(row.evidence_ids == [evidence.id] for row in first)
        assert all(row.context[0]["grounding_status"] == "source_record_linked; question wording not independently verified" for row in first)
        assert all(
            {"record_type": "care_event", "record_id": event.id} in row.context[0]["source_record_refs"]
            for row in first
        )
        # Creation of source-linked questions is itself audited, including
        # review-only questions that are not yet caregiver work.
        creation_revisions = db.query(CareStateRevision).filter(
            CareStateRevision.person_id == person.id,
            CareStateRevision.target_type == "open_questions",
            CareStateRevision.target_id.in_([row.id for row in first]),
            CareStateRevision.change_kind == "created",
        ).all()
        assert {revision.target_id for revision in creation_revisions} == {row.id for row in first}

        first_revision_count = db.query(CareStateRevision).filter(
            CareStateRevision.person_id == person.id,
            CareStateRevision.target_type == "open_questions",
        ).count()
        assert first_revision_count >= len(first)

        second = persist_source_clarification_questions(
            db,
            person_id=person.id,
            caregiver_id=caregiver.id,
            source_kind="daily_capture_extraction",
            source_record_type="care_event",
            source_record_id=event.id,
            evidence_ids=[evidence.id],
            questions=questions,
        )
        db.commit()
        assert {row.id for row in second} == {row.id for row in first}
        # An idempotent replay with no changed evidence or source links does
        # not append another lifecycle revision.
        assert db.query(CareStateRevision).filter(
            CareStateRevision.person_id == person.id,
            CareStateRevision.target_type == "open_questions",
        ).count() == first_revision_count

        # A second authorized caregiver can review a document uploaded by the
        # first caregiver without rewriting the original uploader attribution.
        document_questions = persist_source_clarification_questions(
            db,
            person_id=person.id,
            caregiver_id=reviewing_caregiver.id,
            source_kind="document_extraction",
            source_record_type="evidence",
            source_record_id=evidence.id,
            evidence_ids=[evidence.id],
            questions=["Was her medication changed before discharge?"],
        )
        db.commit()
        assert len(document_questions) == 1
        assert document_questions[0].created_by_caregiver_id == reviewing_caregiver.id
        assert document_questions[0].context[0]["source_record_refs"] == [
            {"record_type": "evidence", "record_id": evidence.id}
        ]
        assert db.query(OpenQuestion).filter_by(person_id=person.id, status="open").count() == 3
        all_question_revisions = db.query(CareStateRevision).filter(
            CareStateRevision.person_id == person.id,
            CareStateRevision.target_type == "open_questions",
        ).all()
        created_question_revisions = [
            revision for revision in all_question_revisions
            if revision.change_kind == "created"
        ]
        expected_question_ids = {row.id for row in first} | {document_questions[0].id}
        assert len(created_question_revisions) == 3
        assert {revision.target_id for revision in created_question_revisions} == expected_question_ids
        assert len(all_question_revisions) > first_revision_count
    finally:
        teardown_db()


def test_source_extraction_questions_reject_cross_person_evidence():
    setup_db()
    try:
        from app.core.database import get_db
        from app.services.uncertainty import persist_source_clarification_questions
        app.dependency_overrides[get_db] = override_get_db

        db = TestingSessionLocal()
        person_a = Person(id="person-source-scope-a", name="Person A")
        caregiver_a = Caregiver(
            id="caregiver-source-scope-a",
            name="Caregiver A",
            email="source-scope-a@example.com",
            relationship="Family",
            person_id=person_a.id,
        )
        person_b = Person(id="person-source-scope-b", name="Person B")
        caregiver_b = Caregiver(
            id="caregiver-source-scope-b",
            name="Caregiver B",
            email="source-scope-b@example.com",
            relationship="Family",
            person_id=person_b.id,
        )
        evidence_b = Evidence(
            id="evidence-source-scope-b",
            person_id=person_b.id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="This source belongs to another person.",
            uploaded_by_caregiver_id=caregiver_b.id,
            reported_by_caregiver_id=caregiver_b.id,
        )
        db.add_all([person_a, caregiver_a, person_b, caregiver_b, evidence_b])
        db.commit()

        with pytest.raises(ValueError, match="missing or belongs to another care record"):
            persist_source_clarification_questions(
                db,
                person_id=person_a.id,
                caregiver_id=caregiver_a.id,
                source_kind="document_extraction",
                source_record_type="evidence",
                source_record_id=evidence_b.id,
                evidence_ids=[evidence_b.id],
                questions=["Who confirmed this information?"],
            )
        assert db.query(OpenQuestion).filter_by(person_id=person_a.id).count() == 0
    finally:
        teardown_db()



def test_finding6_open_information_gaps_do_not_become_caregiver_questions():
    """Missing information remains recorded without automatically creating caregiver work."""
    setup_db()
    try:
        from app.services.care_continuity import next_best_questions

        db = TestingSessionLocal()
        person = Person(id="finding6-gap-person", name="Finding 6 Person")
        caregiver = Caregiver(
            id="finding6-gap-caregiver",
            name="Finding 6 Caregiver",
            email="finding6@example.com",
            relationship="Family",
            person_id=person.id,
        )
        gap = InformationGap(
            id="finding6-nonessential-gap",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="preferred_music",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_PROVIDED,
            priority=Priority.INFORMATIONAL,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="No preference has been recorded.",
        )
        necessary_gap = InformationGap(
            id="finding6-necessary-gap",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="discharge_instruction",
            epistemic_state=EpistemicState.UNKNOWN,
            gap_reason=GapReason.NOT_DOCUMENTED,
            priority=Priority.HIGH,
            lifecycle_status=GapLifecycleStatus.OPEN,
            description="The active discharge instruction is not established.",
            context=[{
                "caregiver_attention_required": True,
                "necessity_rationale": "The current care handoff depends on which discharge instruction is active.",
                "decision_dependency": "The current handoff cannot identify the active discharge instruction without this information.",
            }],
        )
        db.add_all([person, caregiver])
        db.flush()
        discharge_evidence = Evidence(
            id="finding6-discharge-evidence",
            person_id=person.id,
            type="document",
            source_text="Persisted discharge instruction source used for the current handoff.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        necessary_gap.evidence_ids = [discharge_evidence.id]
        db.add_all([discharge_evidence, gap, necessary_gap])
        db.commit()

        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        db.flush()
        assert any(item["source_kind"] == "information_gap" and item["necessity"] == "not_assessed" for item in candidates)
        assert any(item["source_kind"] == "information_gap" and item["necessity"] == "necessary" for item in candidates)
        assert db.query(InformationGap).filter_by(id=gap.id).one().lifecycle_status == GapLifecycleStatus.OPEN
        assert db.query(InformationGap).filter_by(id=necessary_gap.id).one().lifecycle_status == GapLifecycleStatus.OPEN
        necessary = db.query(OpenQuestion).filter_by(person_id=person.id, necessity="necessary").one()
        assert necessary.necessity_rationale == "The current care handoff depends on which discharge instruction is active."
        assert necessary.context[0]["source_id"] == necessary_gap.id

        # The caregiver-facing question projection fails closed: only questions
        # with an explicit necessity assessment and rationale are returned.
        unassessed = OpenQuestion(
            person_id=person.id,
            question="What music does she prefer?",
            subject_type="person",
            subject_id=person.id,
            necessity="not_assessed",
            status="open",
            priority=Priority.INFORMATIONAL,
        )
        optional = OpenQuestion(
            person_id=person.id,
            question="Would she like a different blanket?",
            subject_type="person",
            subject_id=person.id,
            necessity="optional",
            necessity_rationale="Preference information may be useful but is not needed for a current care decision.",
            status="open",
            priority=Priority.INFORMATIONAL,
        )
        legacy_ungrounded = OpenQuestion(
            person_id=person.id,
            question="Can you confirm the current medication dose?",
            subject_type="person",
            subject_id=person.id,
            necessity="necessary",
            necessity_rationale="A legacy request without persisted source evidence.",
            status="open",
            priority=Priority.HIGH,
            context=[{"decision_dependency": "A handoff requires the dose."}],
            evidence_ids=[],
        )
        legacy_invalid_source = OpenQuestion(
            person_id=person.id,
            question="Can you confirm the medication instruction?",
            subject_type="person",
            subject_id=person.id,
            necessity="necessary",
            necessity_rationale="Legacy row references an evidence ID that does not exist.",
            status="open",
            priority=Priority.HIGH,
            context=[{"decision_dependency": "A current handoff depends on this instruction."}],
            evidence_ids=["missing-evidence-id"],
        )
        db.add_all([necessary, unassessed, optional, legacy_ungrounded, legacy_invalid_source])
        db.commit()

        from app.services.uncertainty import UncertaintyEngine
        visible = UncertaintyEngine(db, person_id=person.id).list_open_questions(person.id)
        assert [item.id for item in visible] == [necessary.id]

        # When the explicit decision dependency disappears, the question is
        # automatically suppressed, not marked answered. A later necessity
        # reassessment reopens the same record and preserves revision history.
        from app.services.care_continuity import reassess_open_question_lifecycle
        from app.models.care_continuity import CareStateRevision
        necessary_gap.context = []
        db.flush()
        lifecycle = reassess_open_question_lifecycle(db, person.id)
        db.flush()
        assert lifecycle["suppressed"] == 1
        db.refresh(necessary)
        assert necessary.status == "closed"
        assert necessary.closed_reason.startswith("automatically_suppressed")
        assert necessary.answered_at is None

        new_discharge_evidence = Evidence(
            id="finding6-discharge-evidence-update",
            person_id=person.id,
            type="document",
            source_text="A newer discharge instruction document was added for the upcoming handoff.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        db.add(new_discharge_evidence)
        necessary_gap.evidence_ids = list(necessary_gap.evidence_ids or []) + [new_discharge_evidence.id]
        necessary_gap.context = [{
            "caregiver_attention_required": True,
            "necessity_rationale": "The upcoming handoff again depends on the active discharge instruction.",
            "decision_dependency": "The active discharge instruction is again required for the upcoming handoff.",
        }]
        db.flush()
        next_best_questions(db, person.id, persist=True, commit=False)
        db.flush()
        db.refresh(necessary)
        assert necessary.status == "open"
        assert necessary.closed_at is None
        assert necessary.closed_reason is None
        assert db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.question == necessary.question,
        ).count() == 1
        db.commit()
        assert db.query(CareStateRevision).filter(
            CareStateRevision.person_id == person.id,
            CareStateRevision.target_type == "open_questions",
            CareStateRevision.target_id == necessary.id,
        ).count() >= 2

        # A caregiver's explicit decline is not silently undone by the next
        # reconciliation. Only genuinely new persisted evidence can create a
        # fresh request, linked back to the prior terminal question.
        necessary.status = "declined"
        necessary.closed_at = datetime.now(timezone.utc)
        necessary.closed_reason = "caregiver_declined"
        db.flush()
        prior_count = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.question == necessary.question,
        ).count()
        next_best_questions(db, person.id, persist=True, commit=False)
        db.flush()
        assert db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.question == necessary.question,
        ).count() == prior_count

        later_evidence = Evidence(
            id="finding6-discharge-evidence-later",
            person_id=person.id,
            type="document",
            source_text="A later revised discharge instruction changes the handoff context.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        db.add(later_evidence)
        necessary_gap.evidence_ids = list(necessary_gap.evidence_ids or []) + [later_evidence.id]
        db.flush()
        next_best_questions(db, person.id, persist=True, commit=False)
        db.flush()
        related_questions = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.question == necessary.question,
        ).order_by(OpenQuestion.asked_at.asc()).all()
        assert len(related_questions) == prior_count + 1
        assert any(
            context.get("prior_terminal_question_id") == necessary.id
            and context.get("reassessment_trigger") == "new_persisted_source_or_evidence_after_prior_answer_or_decline"
            for context in (related_questions[-1].context or [])
            if isinstance(context, dict)
        )
        db.close()
    finally:
        teardown_db()


def test_daily_capture_and_document_questions_share_lcr_supersession_lifecycle():
    setup_db()
    try:
        from app.services.uncertainty import (
            persist_source_clarification_questions,
            _question_is_answered_by_current_lcr,
        )
        from app.services.care_continuity import reassess_open_question_lifecycle
        from app.models.care_continuity import CareStateRevision

        db = TestingSessionLocal()
        person = Person(id="finding6-source-lifecycle-person", name="Source Lifecycle Person")
        caregiver = Caregiver(
            id="finding6-source-lifecycle-caregiver",
            name="Source Lifecycle Caregiver",
            email="finding6-source-lifecycle@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="finding6-source-lifecycle-evidence",
            person_id=person.id,
            type="note",
            source_text="The usual sleep schedule is documented in this care record.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        )
        event = CareEvent(
            id="finding6-source-lifecycle-event",
            person_id=person.id,
            event_type="daily_care",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Daily capture",
            description="The usual sleep schedule is documented.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, evidence, event])
        db.commit()

        daily = persist_source_clarification_questions(
            db, person_id=person.id, caregiver_id=caregiver.id,
            source_kind="daily_capture_extraction", source_record_type="care_event",
            source_record_id=event.id, evidence_ids=[evidence.id],
            questions=["What is the sleep schedule?"],
        )[0]
        document = persist_source_clarification_questions(
            db, person_id=person.id, caregiver_id=caregiver.id,
            source_kind="document_extraction", source_record_type="evidence",
            source_record_id=evidence.id, evidence_ids=[evidence.id],
            questions=["What is the sleep schedule?"],
        )[0]
        db.commit()
        assert daily.id == document.id
        contexts = list(daily.context or [])
        assert {item["source_kind"] for item in contexts} >= {
            "daily_capture_extraction", "document_extraction"
        }

        assertion = CareAssertion(
            id="finding6-source-lifecycle-assertion",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="sleep_schedule",
            assertion_kind="document_reviewed_observation",
            asserted_value="Usually sleeps from 9 PM to 6 AM",
            source_label="caregiver report",
            source_text="The usual sleep schedule is documented.",
            evidence_ids=[evidence.id],
            assertion_status="accepted",
            epistemic_state="reported",
            observed_at=datetime.now(timezone.utc),
            review_after_days=30,
        )
        conflict = Contradiction(
            id="finding6-source-lifecycle-conflict",
            person_id=person.id,
            subject_type="person",
            subject_id=person.id,
            field="sleep_schedule",
            description="A competing report remains unresolved.",
            evidence_ids=[evidence.id],
            source_assertion_ids=[assertion.id],
            resolution_status="unresolved",
        )
        db.add_all([assertion, conflict])
        db.commit()

        assert _question_is_answered_by_current_lcr(
            db, person.id, daily.question
        ) is False
        first = reassess_open_question_lifecycle(db, person.id)
        db.refresh(daily)
        assert daily.status == "open"
        assert first["superseded"] == 0

        conflict.resolution_status = "resolved"
        db.flush()
        assert _question_is_answered_by_current_lcr(
            db, person.id, daily.question
        ) is True
        # The production coordinator closes the exact matching LCR gap before
        # question lifecycle reconciliation. Exercise that same ordering.
        from app.services.care_continuity import reconcile_information_gaps_from_accepted_assertions
        reconcile_information_gaps_from_accepted_assertions(db, person.id)
        second = reassess_open_question_lifecycle(db, person.id)
        db.refresh(daily)
        assert daily.status == "closed"
        assert daily.closed_reason.startswith("automatically_superseded")
        assert daily.answered_at is None
        assert second["superseded"] == 1
        assert db.query(CareStateRevision).filter(
            CareStateRevision.person_id == person.id,
            CareStateRevision.target_type == "open_questions",
            CareStateRevision.target_id == daily.id,
        ).count() >= 1
        db.close()
    finally:
        teardown_db()


def test_source_questions_deduplicate_rephrasing_but_preserve_distinct_time_periods():
    setup_db()
    try:
        from app.services.uncertainty import persist_source_clarification_questions

        db = TestingSessionLocal()
        person = Person(id="person-semantic-question", name="Semantic Question Person")
        caregiver = Caregiver(
            id="caregiver-semantic-question",
            name="Semantic Question Caregiver",
            email="semantic-question@example.com",
            relationship="Family",
            person_id=person.id,
        )
        evidence = Evidence(
            id="evidence-semantic-question",
            person_id=person.id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="She woke several times overnight.",
            uploaded_by_caregiver_id=caregiver.id,
            reported_by_caregiver_id=caregiver.id,
        )
        first_event = CareEvent(
            id="event-semantic-question-one",
            person_id=person.id,
            event_type="daily_care",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="First daily care report",
            description="She woke several times overnight.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        second_event = CareEvent(
            id="event-semantic-question-two",
            person_id=person.id,
            event_type="daily_care",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Second daily care report",
            description="She woke several times overnight.",
            evidence_ids=[evidence.id],
            created_by_caregiver_id=caregiver.id,
        )
        db.add_all([person, caregiver, evidence, first_event, second_event])
        db.commit()

        first = persist_source_clarification_questions(
            db,
            person_id=person.id,
            caregiver_id=caregiver.id,
            source_kind="daily_capture_extraction",
            source_record_type="care_event",
            source_record_id=first_event.id,
            evidence_ids=[evidence.id],
            questions=["How many times did she wake overnight?"],
        )
        db.commit()
        assert len(first) == 1

        second = persist_source_clarification_questions(
            db,
            person_id=person.id,
            caregiver_id=caregiver.id,
            source_kind="daily_capture_extraction",
            source_record_type="care_event",
            source_record_id=second_event.id,
            evidence_ids=[evidence.id],
            questions=[
                "How often did she wake up overnight?",
                "How often did she wake up overnight since Friday?",
            ],
        )
        db.commit()

        open_rows = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).all()
        assert len(open_rows) == 2
        assert len(second) == 2
        merged = next(row for row in open_rows if row.id == first[0].id)
        distinct_period = next(row for row in open_rows if row.id != first[0].id)
        assert merged.evidence_ids == [evidence.id]
        assert {
            entry["source_id"]
            for entry in merged.context
            if isinstance(entry, dict) and entry.get("source_kind") == "daily_capture_extraction"
        } == {first_event.id, second_event.id}
        assert "since Friday" in distinct_period.question
        assert distinct_period.id != merged.id
    finally:
        teardown_db()


def test_blocked_medication_and_appointment_actions_create_scoped_necessary_questions():
    setup_db()
    try:
        from app.services.care_continuity import (
            next_best_questions,
            has_valid_caregiver_necessity,
            reassess_open_question_lifecycle,
        )

        db = TestingSessionLocal()
        person = Person(id="finding6-action-person", name="Action Person")
        caregiver = Caregiver(
            id="finding6-action-caregiver",
            name="Action Caregiver",
            email="finding6-action@example.com",
            relationship="Family",
            person_id=person.id,
        )
        other_person = Person(id="finding6-action-other", name="Other Action Person")
        other_caregiver = Caregiver(
            id="finding6-action-other-caregiver",
            name="Other Action Caregiver",
            email="finding6-action-other@example.com",
            relationship="Family",
            person_id=other_person.id,
        )
        medication_source = Evidence(
            id="finding6-medication-action-source",
            person_id=person.id,
            type="document",
            source_text="Medication instruction requires confirmation from the prescribing clinician.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        appointment_source = Evidence(
            id="finding6-appointment-action-source",
            person_id=person.id,
            type="note",
            source_text="Appointment follow-up instruction requires confirmation.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver_report",
        )
        db.add_all([person, caregiver, other_person, other_caregiver, medication_source, appointment_source])
        db.flush()
        medication_action = Action(
            id="finding6-medication-action",
            person_id=person.id,
            source_evidence_id=medication_source.id,
            source_passage=medication_source.source_text,
            source_authority="unknown",
            actor_type="unknown",
            subject_type="medication",
            subject_id="metformin",
            subject_label="Metformin",
            action_type="take",
            normalized_action="Take Metformin 500 mg once daily",
            original_text="Take Metformin 500 mg once daily",
            modality="required",
            status="pending_confirmation",
            is_medication_action=True,
            requires_confirmation=True,
            created_by_caregiver_id=caregiver.id,
        )
        appointment_action = Action(
            id="finding6-appointment-action",
            person_id=person.id,
            source_evidence_id=appointment_source.id,
            source_passage=appointment_source.source_text,
            source_authority="unknown",
            actor_type="unknown",
            subject_type="appointment",
            subject_id="follow-up-appointment",
            subject_label="follow-up appointment",
            action_type="attend",
            normalized_action="Confirm the follow-up appointment",
            original_text="Confirm the follow-up appointment",
            modality="required",
            status="needs_clarification",
            requires_confirmation=True,
            created_by_caregiver_id=caregiver.id,
        )
        foreign_action = Action(
            id="finding6-foreign-action",
            person_id=other_person.id,
            source_evidence_id=medication_source.id,
            source_passage=medication_source.source_text,
            source_authority="unknown",
            actor_type="unknown",
            subject_type="medication",
            subject_id="metformin",
            subject_label="Metformin",
            action_type="take",
            normalized_action="Take Metformin",
            original_text="Take Metformin",
            modality="required",
            status="needs_clarification",
            is_medication_action=True,
            requires_confirmation=True,
            created_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([medication_action, appointment_action, foreign_action])
        db.commit()

        candidates = next_best_questions(db, person.id, persist=True, commit=False)
        action_candidates = [item for item in candidates if item.get("source_kind") == "registered_action"]
        assert {item["source_id"] for item in action_candidates} == {
            medication_action.id, appointment_action.id
        }
        assert all(item["necessity"] == "necessary" for item in action_candidates)
        assert all(item["evidence_ids"] for item in action_candidates)

        questions = db.query(OpenQuestion).filter(
            OpenQuestion.person_id == person.id,
            OpenQuestion.status == "open",
        ).all()
        assert len(questions) == 2
        medication_question = next(item for item in questions if item.intended_answerer_role == "professional")
        assert "prescribing clinician" in medication_question.question
        assert has_valid_caregiver_necessity(medication_question, db, person.id)
        assert not db.query(OpenQuestion).filter(
            OpenQuestion.person_id == other_person.id
        ).count()

        medication_action.status = "approved"
        db.flush()
        counts = reassess_open_question_lifecycle(db, person.id)
        db.refresh(medication_question)
        assert medication_question.status == "closed"
        assert medication_question.closed_reason.startswith("automatically_superseded")
        assert counts["superseded"] >= 1
        db.commit()
        db.close()
    finally:
        teardown_db()


def test_document_reviewed_assertion_preserves_conflict_with_existing_lcr_claim():
    setup_db()
    try:
        from app.services.care_continuity import create_assertion
        from app.schemas.care_continuity import CareAssertionCreate

        db = TestingSessionLocal()
        person = Person(id="finding6-document-conflict-person", name="Document Conflict Person")
        caregiver = Caregiver(
            id="finding6-document-conflict-caregiver",
            name="Document Conflict Caregiver",
            email="finding6-document-conflict@example.com",
            relationship="Family",
            person_id=person.id,
        )
        first = Evidence(
            id="finding6-document-conflict-evidence-a",
            person_id=person.id,
            type="document",
            source_text="Earlier care plan records one mobility support level.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        second = Evidence(
            id="finding6-document-conflict-evidence-b",
            person_id=person.id,
            type="document",
            source_text="New discharge document records a different mobility support level on 12 November.",
            uploaded_by_caregiver_id=caregiver.id,
            source_type="document",
        )
        db.add_all([person, caregiver, first, second])
        db.commit()

        existing = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="mobility_support",
                assertion_kind="current_state",
                asserted_value="Needs one-person assistance",
                source_label="Earlier care plan",
                source_text=first.source_text,
                evidence_ids=[first.id],
                review_after_days=30,
                created_by_caregiver_id=caregiver.id,
            ),
            commit=False,
        )
        existing.assertion_status = "accepted"
        db.flush()

        document = create_assertion(
            db,
            CareAssertionCreate(
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="mobility_support",
                assertion_kind="document_reviewed_observation",
                asserted_value="Walks independently with a frame",
                source_label="Caregiver-reviewed discharge document",
                source_text=second.source_text,
                evidence_ids=[second.id],
                event_time_text="12 November 2026",
                review_after_days=30,
                created_by_caregiver_id=caregiver.id,
            ),
            commit=False,
        )
        document.assertion_status = "accepted"
        db.commit()

        conflict = db.query(Contradiction).filter(
            Contradiction.person_id == person.id,
            Contradiction.subject_type == "person",
            Contradiction.subject_id == person.id,
            Contradiction.field == "mobility_support",
        ).one()
        # One source carries a date but the competing source does not. That is
        # insufficient to establish that the reports describe distinct moments.
        assert conflict.resolution_status == "unresolved"
        assert set(conflict.source_assertion_ids) == {existing.id, document.id}
        assert set(conflict.evidence_ids) == {first.id, second.id}
        assert db.query(CareAssertion).filter(
            CareAssertion.person_id == person.id,
            CareAssertion.field == "mobility_support",
        ).count() == 2
    finally:
        db.close()
        teardown_db()


def test_scenario_03_food_intake_date_correction_supersedes_without_erasing_history():
    setup_db()
    try:
        from app.core.database import get_db
        from app.models.care_continuity import EvidenceObservation
        from app.models.privacy_delivery import AuditLogEvent

        app.dependency_overrides[get_db] = override_get_db
        db = TestingSessionLocal()
        person = Person(id="scenario03-food-person", name="Margaret")
        caregiver = Caregiver(
            id="scenario03-food-caregiver",
            name="Sarah",
            email="scenario03-food@example.com",
            relationship="Family",
            person_id=person.id,
        )
        original_text = "Sarah reported that Margaret barely ate lunch on Monday."
        correction_text = (
            "Sarah corrected the date: Margaret ate most of her lunch on Monday. "
            "It was Tuesday when Margaret barely ate lunch."
        )
        db.add_all([
            person,
            caregiver,
            Evidence(
                id="scenario03-food-original",
                person_id=person.id,
                type="caregiver_report",
                source_text=original_text,
                uploaded_by_caregiver_id=caregiver.id,
                source_type="caregiver_report",
            ),
            Evidence(
                id="scenario03-food-correction",
                person_id=person.id,
                type="caregiver_report",
                source_text=correction_text,
                uploaded_by_caregiver_id=caregiver.id,
                source_type="caregiver_report",
            ),
        ])
        db.commit()
        db.close()

        client = authorized_client(person.id)
        original_passage = "Margaret barely ate lunch on Monday."
        original_start = original_text.index(original_passage)
        original_response = client.post(
            "/api/v1/care-continuity/evidence/scenario03-food-original/observations",
            json={
                "observation_kind": "caregiver_report",
                "epistemic_status": "reported",
                "subject_label": "Margaret",
                "attribute": "food_intake",
                "value_text": "barely ate lunch",
                "source_passage": original_passage,
                "source_span_start": original_start,
                "source_span_end": original_start + len(original_passage),
                "observed_at": "2026-11-09T12:00:00+00:00",
                "reported_at": "2026-11-09T18:00:00+00:00",
                "extraction_method": "human_entered",
                "extraction_version": "scenario-03",
            },
        )
        assert original_response.status_code == 201, original_response.text
        original = original_response.json()

        monday_passage = "Margaret ate most of her lunch on Monday."
        monday_start = correction_text.index(monday_passage)
        monday_response = client.post(
            "/api/v1/care-continuity/evidence/scenario03-food-correction/observations",
            json={
                "observation_kind": "caregiver_report",
                "epistemic_status": "reported",
                "subject_label": "Margaret",
                "attribute": "food_intake",
                "value_text": "ate most of her lunch",
                "source_passage": monday_passage,
                "source_span_start": monday_start,
                "source_span_end": monday_start + len(monday_passage),
                "observed_at": "2026-11-09T12:00:00+00:00",
                "reported_at": "2026-11-12T09:00:00+00:00",
                "extraction_method": "human_entered",
                "extraction_version": "scenario-03",
            },
        )
        assert monday_response.status_code == 201, monday_response.text

        tuesday_passage = "It was Tuesday when Margaret barely ate lunch."
        tuesday_start = correction_text.index(tuesday_passage)
        correction_response = client.post(
            "/api/v1/care-continuity/evidence/scenario03-food-correction/observations",
            json={
                "observation_kind": "caregiver_report",
                "epistemic_status": "reported",
                "subject_label": "Margaret",
                "attribute": "food_intake",
                "value_text": "barely ate lunch",
                "source_passage": tuesday_passage,
                "source_span_start": tuesday_start,
                "source_span_end": tuesday_start + len(tuesday_passage),
                "observed_at": "2026-11-10T12:00:00+00:00",
                "reported_at": "2026-11-12T09:00:00+00:00",
                "supersedes_observation_id": original["id"],
                "correction_rationale": "Sarah corrected the observation date from Monday to Tuesday.",
                "extraction_method": "caregiver_correction",
                "extraction_version": "scenario-03",
            },
        )
        assert correction_response.status_code == 201, correction_response.text
        corrected = correction_response.json()
        assert corrected["supersedes_observation_id"] == original["id"]
        assert corrected["observed_at"].startswith("2026-11-10")
        assert corrected["reported_at"].startswith("2026-11-12")
        assert corrected["reassessment_receipt"]["supersedes_observation_id"] == original["id"]
        assert corrected["care_continuity_execution"]["stages"]["reconciliation"] == "committed"

        with TestingSessionLocal() as verify_db:
            original_row = verify_db.query(EvidenceObservation).filter_by(
                id=original["id"], person_id=person.id
            ).one()
            corrected_row = verify_db.query(EvidenceObservation).filter_by(
                id=corrected["id"], person_id=person.id
            ).one()
            all_rows = verify_db.query(EvidenceObservation).filter_by(
                person_id=person.id, attribute="food_intake"
            ).all()
            superseded_ids = {
                row.supersedes_observation_id for row in all_rows
                if row.supersedes_observation_id
            }
            current_rows = [row for row in all_rows if row.id not in superseded_ids]
            audit = verify_db.query(AuditLogEvent).filter_by(
                target_id=corrected["id"],
                action="evidence_observation.correct",
            ).one()

            assert original_row.observed_at.isoformat().startswith("2026-11-09")
            assert original_row.value_text == "barely ate lunch"
            assert corrected_row.observed_at.isoformat().startswith("2026-11-10")
            assert corrected_row.value_text == original_row.value_text
            assert len([row for row in current_rows if row.value_text == "barely ate lunch"]) == 1
            assert len([row for row in current_rows if row.value_text == "ate most of her lunch"]) == 1
            assert audit.record_metadata["supersedes_observation_id"] == original["id"]
    finally:
        teardown_db()
