from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import Person, Caregiver, CareEvent
from app.core.causal_enums import (
    EvidenceStrength,
    CausalConfidence,
    CausalRelationshipStatus,
    CausalDirection,
    EvidenceRole,
    EvidenceType,
)
from app.services.causal_reasoning import CausalReasoningEngine
from app.schemas.causal import (
    CausalHypothesisCreate,
    CausalEvidenceCreate,
)

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


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()


def seed_person(db, person_id="person-1", name="Test Person"):
    person = Person(id=person_id, name=name)
    caregiver = Caregiver(
        id="caregiver-1",
        name="Test Caregiver",
        email="test@example.com",
        relationship="Family",
        person_id=person_id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()


# ======================================================================
# Service-layer tests
# ======================================================================

def test_temporal_proximity_does_not_imply_causation():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-1")

        engine = CausalReasoningEngine(db)
        cause_time = datetime(2026, 6, 4, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 6, 6, 9, 0, 0, tzinfo=timezone.utc)

        result = engine.assess_relationship(
            person_id="person-1",
            potential_cause_type="medication",
            potential_cause_description="Medication X was started",
            outcome_type="fall",
            outcome_description="a fall was documented",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )

        assert result.relationship_status == CausalRelationshipStatus.OBSERVED_ASSOCIATION
        assert result.causal_confidence == CausalConfidence.INSUFFICIENT
        assert result.evidence_strength == EvidenceStrength.WEAK
        assert "caused" not in result.what_is_uncertain.lower()
        assert "does not establish" in result.what_is_uncertain
        assert result.quality_gate_passed is True
        db.close()
    finally:
        teardown_db()


def test_hypothesis_is_not_promoted_to_fact():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-2")

        engine = CausalReasoningEngine(db)
        hyp = engine.create_hypothesis(
            CausalHypothesisCreate(
                person_id="person-2",
                potential_cause_type="medication",
                potential_cause_description="Medication Y started",
                outcome_type="dizziness",
                outcome_description="dizziness reported",
                relationship_status=CausalRelationshipStatus.CAUSAL_HYPOTHESIS,
                causal_confidence=CausalConfidence.POSSIBLE,
            )
        )
        assert hyp.relationship_status == CausalRelationshipStatus.CAUSAL_HYPOTHESIS
        assert hyp.causal_confidence == CausalConfidence.POSSIBLE
        db.close()
    finally:
        teardown_db()


def test_alternative_explanations_identified_from_record():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-3")

        cause_time = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 7, 3, 9, 0, 0, tzinfo=timezone.utc)

        illness = CareEvent(
            id="event-illness",
            person_id="person-3",
            event_type="illness",
            occurred_at=cause_time,
            title="Acute illness documented",
            created_by_caregiver_id="caregiver-1",
        )
        db.add(illness)
        db.commit()
        db.close()

        db = TestingSessionLocal()
        engine = CausalReasoningEngine(db)
        result = engine.assess_relationship(
            person_id="person-3",
            potential_cause_type="medication",
            potential_cause_description="Medication started",
            outcome_type="fall",
            outcome_description="a fall",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )

        alt_descs = [a["description"] for a in result.alternative_explanations]
        assert any("illness" in d.lower() or "acute" in d.lower() for d in alt_descs)
        assert len(result.alternative_explanations) > 0
        db.close()
    finally:
        teardown_db()


def test_confounder_preserves_ambiguity():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-4")

        cause_time = datetime(2026, 8, 1, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 8, 3, 9, 0, 0, tzinfo=timezone.utc)

        illness = CareEvent(
            id="event-illness-4",
            person_id="person-4",
            event_type="illness",
            occurred_at=cause_time,
            title="Acute illness",
            created_by_caregiver_id="caregiver-1",
        )
        db.add(illness)
        db.commit()
        db.close()

        db = TestingSessionLocal()
        engine = CausalReasoningEngine(db)
        result = engine.assess_relationship(
            person_id="person-4",
            potential_cause_type="medication",
            potential_cause_description="Medication X started",
            outcome_type="fall",
            outcome_description="a fall",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )

        assert len(result.confounders) > 0
        assert result.causal_confidence in (
            CausalConfidence.CONFLICTING,
            CausalConfidence.POSSIBLE,
            CausalConfidence.INSUFFICIENT,
        )
        db.close()
    finally:
        teardown_db()


def test_strong_evidence_upgrades_confidence():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-5")

        engine = CausalReasoningEngine(db)
        cause_time = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 9, 2, 9, 0, 0, tzinfo=timezone.utc)

        result = engine.assess_relationship(
            person_id="person-5",
            potential_cause_type="medication",
            potential_cause_description="Medication X started",
            outcome_type="dizziness",
            outcome_description="dizziness",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )
        assert result.causal_confidence == CausalConfidence.INSUFFICIENT
        assert result.causal_confidence != CausalConfidence.ESTABLISHED

        hyp = engine.create_hypothesis(
            CausalHypothesisCreate(
                person_id="person-5",
                potential_cause_type="medication",
                potential_cause_description="Medication X started",
                outcome_type="dizziness",
                outcome_description="dizziness",
                relationship_status=CausalRelationshipStatus.CAUSAL_HYPOTHESIS,
                causal_confidence=CausalConfidence.POSSIBLE,
            )
        )

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-5",
                role=EvidenceRole.SUPPORTING,
                evidence_type=EvidenceType.CLINICIAN_DOCUMENTATION,
                strength=EvidenceStrength.STRONG,
                description="Clinician documented medication as likely cause of dizziness",
            )
        )

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-5",
                role=EvidenceRole.SUPPORTING,
                evidence_type=EvidenceType.REEVALUATION_CHALLENGE,
                strength=EvidenceStrength.STRONG,
                description="Dizziness resolved after discontinuation; returned on rechallenge",
            )
        )

        engine.reassess_hypothesis(hyp.id)
        db.refresh(hyp)

        assert hyp.evidence_strength == EvidenceStrength.STRONG
        assert hyp.causal_confidence == CausalConfidence.SUPPORTED
        assert hyp.relationship_status == CausalRelationshipStatus.SUPPORTED
        db.close()
    finally:
        teardown_db()


def test_contradictory_evidence_preserves_conflict():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-6")

        engine = CausalReasoningEngine(db)
        hyp = engine.create_hypothesis(
            CausalHypothesisCreate(
                person_id="person-6",
                potential_cause_type="medication",
                potential_cause_description="Medication X started",
                outcome_type="dizziness",
                outcome_description="dizziness",
                relationship_status=CausalRelationshipStatus.CAUSAL_HYPOTHESIS,
            )
        )

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-6",
                role=EvidenceRole.SUPPORTING,
                evidence_type=EvidenceType.CLINICIAN_DOCUMENTATION,
                strength=EvidenceStrength.STRONG,
                description="Clinician says medication contributed to dizziness",
            )
        )

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-6",
                role=EvidenceRole.CONTRADICTORY,
                evidence_type=EvidenceType.CORRELATION,
                strength=EvidenceStrength.WEAK,
                description="Dizziness documented before medication initiation",
            )
        )

        engine.reassess_hypothesis(hyp.id)
        db.refresh(hyp)

        assert hyp.causal_confidence == CausalConfidence.CONFLICTING
        assert hyp.relationship_status == CausalRelationshipStatus.CONFLICTING
        trace = engine.get_evidence_trace(hyp.id)
        assert len(trace["contradictory_evidence"]) == 1
        db.close()
    finally:
        teardown_db()


def test_causal_direction_not_assumed_from_sequence():
    """Reverse causality: medication change after decline does not mean
    medication caused the decline."""
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-7")

        engine = CausalReasoningEngine(db)
        decline_time = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
        med_change_time = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)

        result = engine.assess_relationship(
            person_id="person-7",
            potential_cause_type="medication_change",
            potential_cause_description="Medication changed",
            outcome_type="mobility_decline",
            outcome_description="mobility declined",
            cause_time=med_change_time,
            outcome_time=decline_time,
        )

        assert result.causal_direction == CausalDirection.OUTCOME_TO_CAUSE
        assert result.relationship_status != CausalRelationshipStatus.SUPPORTED
        db.close()
    finally:
        teardown_db()


def test_no_causation_keyword_without_evidence():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-8")

        engine = CausalReasoningEngine(db)
        cause_time = datetime(2026, 11, 1, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 11, 3, 9, 0, 0, tzinfo=timezone.utc)

        result = engine.assess_relationship(
            person_id="person-8",
            potential_cause_type="medication",
            potential_cause_description="Medication started",
            outcome_type="fall",
            outcome_description="a fall",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )

        combined = (
            result.what_happened
            + " " + result.what_followed
            + " " + result.why_it_may_be_relevant
            + " " + result.what_is_uncertain
            + " " + result.what_may_warrant_attention
        ).lower()
        assert "caused" not in combined
        assert "caused the" not in combined
        db.close()
    finally:
        teardown_db()


def test_evidence_traceability():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-9")

        engine = CausalReasoningEngine(db)
        hyp = engine.create_hypothesis(
            CausalHypothesisCreate(
                person_id="person-9",
                potential_cause_type="medication",
                potential_cause_description="Medication X started June 4",
                outcome_type="fall",
                outcome_description="fall on June 6",
                relationship_status=CausalRelationshipStatus.OBSERVED_ASSOCIATION,
                temporal_relationship="occurred 2 days before",
                time_gap_seconds=172800,
                provenance="care_event_analysis",
                first_observed_date=datetime(2026, 6, 4, tzinfo=timezone.utc),
            )
        )

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-9",
                role=EvidenceRole.SUPPORTING,
                evidence_type=EvidenceType.TEMPORAL_PROXIMITY,
                strength=EvidenceStrength.WEAK,
                description="Fall occurred two days after medication started",
                source_document="progress_note_2026_06_06",
                caregiver_id="caregiver-1",
            )
        )

        trace = engine.get_evidence_trace(hyp.id)
        assert trace != {}
        assert trace["hypothesis"]["potential_cause"] == "Medication X started June 4"
        assert trace["hypothesis"]["temporal_relationship"] == "occurred 2 days before"
        assert trace["hypothesis"]["first_observed_date"] == "2026-06-04T00:00:00"
        assert len(trace["supporting_evidence"]) == 1
        assert trace["supporting_evidence"][0]["source_document"] == "progress_note_2026_06_06"
        db.close()
    finally:
        teardown_db()


def test_quality_gate_downgrades_without_cause():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-10")

        engine = CausalReasoningEngine(db)
        cause_time = datetime(2026, 12, 1, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 12, 3, 9, 0, 0, tzinfo=timezone.utc)

        result = engine.assess_relationship(
            person_id="person-10",
            potential_cause_type="medication",
            potential_cause_description="",
            outcome_type="fall",
            outcome_description="a fall",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )

        assert "No identifiable potential cause" in result.quality_gate_notes
        db.close()
    finally:
        teardown_db()


def test_hypothesis_lifecycle_strengthens_then_weakens():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-11")

        engine = CausalReasoningEngine(db)
        hyp = engine.create_hypothesis(
            CausalHypothesisCreate(
                person_id="person-11",
                potential_cause_type="medication",
                potential_cause_description="Medication started",
                outcome_type="dizziness",
                outcome_description="dizziness",
                relationship_status=CausalRelationshipStatus.CAUSAL_HYPOTHESIS,
            )
        )

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-11",
                role=EvidenceRole.SUPPORTING,
                evidence_type=EvidenceType.CLINICIAN_DOCUMENTATION,
                strength=EvidenceStrength.STRONG,
                description="Clinician documented as contributing",
            )
        )
        engine.reassess_hypothesis(hyp.id)
        db.refresh(hyp)
        assert hyp.causal_confidence == CausalConfidence.SUPPORTED
        assert hyp.evidence_strength == EvidenceStrength.STRONG

        engine.add_evidence(
            CausalEvidenceCreate(
                hypothesis_id=hyp.id,
                person_id="person-11",
                role=EvidenceRole.CONTRADICTORY,
                evidence_type=EvidenceType.CORRELATION,
                strength=EvidenceStrength.MODERATE,
                description="Symptom predates medication",
            )
        )
        engine.reassess_hypothesis(hyp.id)
        db.refresh(hyp)
        assert hyp.causal_confidence == CausalConfidence.CONFLICTING
        assert hyp.relationship_status == CausalRelationshipStatus.CONFLICTING
        db.close()
    finally:
        teardown_db()


def test_repeatable_pattern_strengthens():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-12")

        engine = CausalReasoningEngine(db)
        cause_time = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        outcome_time = datetime(2026, 1, 2, 9, 0, 0, tzinfo=timezone.utc)

        result = engine.assess_relationship(
            person_id="person-12",
            potential_cause_type="medication",
            potential_cause_description="Medication started",
            outcome_type="dizziness",
            outcome_description="dizziness",
            cause_time=cause_time,
            outcome_time=outcome_time,
        )

        hyp = engine.create_hypothesis_from_analysis(
            "person-12", result, caregiver_id="caregiver-1"
        )
        assert hyp is not None
        assert hyp.relationship_status in (
            CausalRelationshipStatus.OBSERVED_ASSOCIATION,
            CausalRelationshipStatus.CAUSAL_HYPOTHESIS,
        )
        db.close()
    finally:
        teardown_db()


def test_identify_temporal_associations():
    setup_db()
    try:
        db = TestingSessionLocal()
        seed_person(db, person_id="person-13")

        base = datetime(2026, 2, 1, 9, 0, 0, tzinfo=timezone.utc)
        med_event = CareEvent(
            id="event-med",
            person_id="person-13",
            event_type="medication",
            occurred_at=base,
            title="Medication started",
            created_by_caregiver_id="caregiver-1",
        )
        fall_event = CareEvent(
            id="event-fall",
            person_id="person-13",
            event_type="fall",
            occurred_at=base + timedelta(days=2),
            title="Fall documented",
            created_by_caregiver_id="caregiver-1",
        )
        far_event = CareEvent(
            id="event-far",
            person_id="person-13",
            event_type="appointment",
            occurred_at=base + timedelta(days=60),
            title="Annual checkup",
            created_by_caregiver_id="caregiver-1",
        )
        db.add_all([med_event, fall_event, far_event])
        db.commit()
        db.close()

        db = TestingSessionLocal()
        engine = CausalReasoningEngine(db)
        associations = engine.identify_temporal_associations(
            person_id="person-13",
            reference_event_id="event-med",
            window=timedelta(days=14),
        )

        related_titles = [a.outcome_description for a in associations]
        assert "Fall documented" in related_titles
        assert "Annual checkup" not in related_titles
        assert all(a.relation_type in ("before", "after", "simultaneous") for a in associations)
        db.close()
    finally:
        teardown_db()


# ======================================================================
# API-layer tests
# ======================================================================

def test_api_assess_does_not_assert_causation():
    setup_db()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/causal/assess",
            params={
                "person_id": "person-20",
                "potential_cause_type": "medication",
                "potential_cause_description": "Medication X was started",
                "outcome_type": "fall",
                "outcome_description": "a fall was documented",
                "cause_time": "2026-06-04T09:00:00+00:00",
                "outcome_time": "2026-06-06T09:00:00+00:00",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["evidence_strength"] == "weak"
        assert data["causal_confidence"] == "insufficient"
        assert "does not establish" in data["what_is_uncertain"]
        db = TestingSessionLocal()
        seed_person(db, person_id="person-20")
        db.close()
    finally:
        teardown_db()


def test_api_create_and_trace_hypothesis():
    setup_db()
    try:
        client = TestClient(app)
        db = TestingSessionLocal()
        seed_person(db, person_id="person-21")
        db.close()

        response = client.post(
            "/api/v1/causal/hypotheses",
            json={
                "person_id": "person-21",
                "potential_cause_type": "medication",
                "potential_cause_description": "Medication X started",
                "outcome_type": "fall",
                "outcome_description": "fall on June 6",
                "relationship_status": "observed_association",
            },
        )
        assert response.status_code == 200
        hyp_id = response.json()["id"]

        response = client.post(
            "/api/v1/causal/evidence",
            json={
                "hypothesis_id": hyp_id,
                "person_id": "person-21",
                "role": "supporting",
                "evidence_type": "temporal_proximity",
                "strength": "weak",
                "description": "Fall occurred two days after medication started",
                "source_document": "note_001",
            },
        )
        assert response.status_code == 200

        response = client.get(f"/api/v1/causal/trace/{hyp_id}")
        assert response.status_code == 200
        trace = response.json()
        assert trace["hypothesis"]["potential_cause"] == "Medication X started"
        assert len(trace["supporting_evidence"]) == 1
    finally:
        teardown_db()


def test_api_repeated_relationship_strengthens():
    setup_db()
    try:
        client = TestClient(app)
        db = TestingSessionLocal()
        seed_person(db, person_id="person-22")
        db.close()

        response = client.post(
            "/api/v1/causal/hypotheses",
            json={
                "person_id": "person-22",
                "potential_cause_type": "medication",
                "potential_cause_description": "Medication started",
                "outcome_type": "dizziness",
                "outcome_description": "dizziness",
                "relationship_status": "causal_hypothesis",
                "is_repeatable": True,
                "repeat_count": 3,
                "repeat_pattern": "started->dizziness, stopped->improved, restarted->returned",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_repeatable"] is True
        assert data["repeat_count"] == 3
    finally:
        teardown_db()
