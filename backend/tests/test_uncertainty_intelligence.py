"""Behavioral tests for uncertainty intelligence. Fixtures are synthetic and test-only."""
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import Boolean, Column, DateTime, Float, MetaData, String, Table, Text, create_engine, insert

from app.services.lcr_context import build_lcr_context
from app.services.uncertainty_intelligence import analyze_uncertainty, evidence_record_to_uncertainty_item
from app.services.delta_engine import _match_finding_evidence


def test_persisted_evidence_projection_keeps_time_provenance_separate_from_timestamp():
    from types import SimpleNamespace

    row = SimpleNamespace(
        id="evidence-1",
        source_type="document",
        type="document",
        source_text="Follow-up date is not stated.",
        evidence_status="unverified",
        time_provenance="reported_by_caregiver",
        extra_metadata=json.dumps({
            "quality": "partial",
            "readable": True,
            "extraction_status": "partial",
            "temporal_scope": "time_sensitive",
        }),
    )
    item = evidence_record_to_uncertainty_item(row)
    assert item["temporal_provenance"] == "reported_by_caregiver"
    assert "observed_at" not in item
    assert item["quality"] == "partial"
    assert item["readable"] is True
    assert item["extraction_status"] == "partial"


def test_no_evidence_is_unknown_not_negative():
    result = analyze_uncertainty(
        claim="Medication status is not recorded.",
        task="high_consequence_assessment",
        evidence=[],
        required_fields=["medication_status"],
    )
    assert result["epistemic_state"] == "unknown"
    assert result["conclusion_confidence"] is None
    assert result["calibration_status"] == "not_calibrated"
    assert result["sufficiency"] == "insufficient_pending_resolution"
    assert result["missing_fields"] == ["medication_status"]


def test_conflicts_remain_conflicts_and_are_not_auto_resolved():
    conflict = {"field": "medication_status", "evidence_ids": ["test-evidence-a", "test-evidence-b"]}
    result = analyze_uncertainty(
        claim="The medication status differs between records.",
        task="recommendation",
        evidence=[
            {"id": "test-evidence-a", "field": "medication_status", "value": "current", "verified": True},
            {"id": "test-evidence-b", "field": "medication_status", "value": "discontinued", "verified": True},
        ],
        unresolved_conflicts=[conflict],
    )
    assert result["epistemic_state"] == "conflicting"
    assert result["unresolved_conflicts"] == [conflict]
    assert result["sufficiency"] == "insufficient_pending_resolution"
    assert result["conclusion_confidence"] is None


def test_explicit_uncertainty_and_unverified_source_are_separate():
    result = analyze_uncertainty(
        claim="I think the appointment may have happened.",
        task="summarize",
        evidence=[{"id": "test-evidence-c", "source_type": "caregiver_report", "verified": False}],
    )
    assert result["explicit_uncertainty"] is True
    assert result["inferred_uncertainty"] is False
    assert "source_verification_uncertainty" in result["classifications"]
    assert result["source_reliability"] == "not_quantified"


def test_stale_evidence_only_when_source_supplies_freshness_contract():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    result = analyze_uncertainty(
        claim="The record lists a provider.",
        task="summarize",
        evidence=[{
            "id": "test-evidence-d",
            "verified": True,
            "observed_at": "2026-01-01T00:00:00Z",
            "max_age_days": 30,
        }],
        now=now,
    )
    assert "outdated_information" in result["classifications"]
    assert result["epistemic_state"] == "partially_known"


def test_unavailable_source_is_not_treated_as_negative_finding():
    result = analyze_uncertainty(
        claim="Current status cannot be checked.",
        task="identify_change",
        evidence=[],
        source_access="unavailable",
    )
    assert result["epistemic_state"] == "unknown"
    assert "knowledge_boundary" in result["classifications"]
    assert any("not available" in limitation for limitation in result["limitations"])


def test_unsupported_assessment_task_fails_closed():
    result = analyze_uncertainty(
        claim="The care status is current.",
        task="unregistered_high_consequence_workflow",
        evidence=[{"id": "evidence-a", "text": "A caregiver report exists.", "source_type": "caregiver_report"}],
    )
    assert "unsupported_assessment_task" in result["classifications"]
    assert result["epistemic_state"] == "unknown"
    assert result["sufficiency"] == "insufficient_pending_resolution"
    assert result["conclusion_confidence"] is None


def test_missing_task_is_rejected_instead_of_fabricating_assessment():
    with pytest.raises(ValueError):
        analyze_uncertainty(claim="A real claim", task="", evidence=[])



def test_lcr_context_exposes_active_persisted_analysis_without_cross_person_leakage():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    assessments = Table(
        "uncertainty_assessments",
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("subject_type", String),
        Column("subject_id", String),
        Column("field", String),
        Column("epistemic_state", String),
        Column("confidence", Float),
        Column("known_value", Text),
        Column("gap_reason", String),
        Column("context", Text),
        Column("evidence_ids", Text),
        Column("temporal_precision", String),
        Column("assessed_at", DateTime),
        Column("assessed_by_caregiver_id", String),
        Column("superseded_by_assessment_id", String),
        Column("is_active", Boolean),
    )
    metadata.create_all(engine)
    analysis = {
        "epistemic_state": "unknown",
        "limitations": ["No retrieved evidence records were supplied to this assessment."],
        "clarification_questions": [
            {"target_field": "medication_status", "question": "What is currently known about medication_status?"}
        ],
        "calibration_status": "not_calibrated",
    }
    with engine.begin() as connection:
        connection.execute(insert(assessments), [
            {
                "id": "assessment-active", "person_id": "person-a", "subject_type": "care_record",
                "subject_id": "record-a", "field": "medication_status", "epistemic_state": "unknown",
                "confidence": None, "known_value": None, "gap_reason": "INSUFFICIENT_EVIDENCE",
                "context": json.dumps([{"origin": "delta_engine", "delta_run_pending": False, "analysis": analysis}]),
                "evidence_ids": "[]", "temporal_precision": None,
                "assessed_at": datetime(2026, 10, 29, tzinfo=timezone.utc),
                "assessed_by_caregiver_id": None, "superseded_by_assessment_id": None, "is_active": True,
            },
            {
                "id": "assessment-other-person", "person_id": "person-b", "subject_type": "care_record",
                "subject_id": "record-b", "field": "medication_status", "epistemic_state": "known",
                "confidence": None, "known_value": "must not leak", "gap_reason": None,
                "context": json.dumps([{"private": "other person's context"}]), "evidence_ids": "[]",
                "temporal_precision": None, "assessed_at": datetime(2026, 10, 29, tzinfo=timezone.utc),
                "assessed_by_caregiver_id": None, "superseded_by_assessment_id": None, "is_active": True,
            },
            {
                "id": "assessment-superseded", "person_id": "person-a", "subject_type": "care_record",
                "subject_id": "record-a", "field": "medication_status", "epistemic_state": "known",
                "confidence": None, "known_value": "old state", "gap_reason": None,
                "context": json.dumps([{"superseded": True}]), "evidence_ids": "[]",
                "temporal_precision": None, "assessed_at": datetime(2026, 10, 28, tzinfo=timezone.utc),
                "assessed_by_caregiver_id": None, "superseded_by_assessment_id": "assessment-active", "is_active": False,
            },
        ])
    from sqlalchemy.orm import Session
    from app.api.care_continuity import _build_situation_understanding_assessment
    with Session(engine) as db:
        context = build_lcr_context(db, "person-a", limit_per_source=10)
        situation = _build_situation_understanding_assessment(db, "person-a")
    source = context["sources"]["uncertainty_assessments"]
    assert source["returned_count"] == 1
    item = source["items"][0]
    assert item["id"] == "assessment-active"
    assert item["context"][0]["analysis"]["limitations"] == analysis["limitations"]
    assert item["context"][0]["analysis"]["clarification_questions"] == analysis["clarification_questions"]
    situation_assessment = next(
        record for record in situation["uncertainties_and_conflicts"]
        if record["id"] == "assessment-active"
    )
    assert situation_assessment["uncertainty_analysis"]["limitations"] == analysis["limitations"]
    assert situation_assessment["uncertainty_analysis"]["clarification_questions"] == analysis["clarification_questions"]
    assert "assessment-other-person" not in json.dumps(context)
    assert "must not leak" not in json.dumps(context)
    assert "assessment-superseded" not in json.dumps(context)
    engine.dispose()



def test_delta_finding_evidence_requires_matching_stored_excerpt():
    evidence = [
        {"id": "evidence-medication", "text": "Caregiver reports: dose increased to 10 mg."},
        {"id": "evidence-fall", "text": "Caregiver reports a fall yesterday."},
    ]
    matched = _match_finding_evidence(evidence, "dose increased to 10 mg")
    assert [item["id"] for item in matched] == ["evidence-medication"]
    assert _match_finding_evidence(evidence, "") == []
    assert _match_finding_evidence(evidence, "reported dose changed to 10mg") == []


def test_duplicate_source_text_is_not_independent_corroboration():
    result = analyze_uncertainty(
        claim="The appointment is confirmed.",
        task="summarize",
        evidence=[
            {"id": "copy-a", "text": "Appointment scheduled for Tuesday.", "source_type": "caregiver_report", "verified": False},
            {"id": "copy-b", "text": "Appointment scheduled for Tuesday.", "source_type": "copied_summary", "verified": False},
        ],
    )
    assert result["evidence_record_count"] == 2
    assert result["independent_evidence_record_count"] == 1
    assert len(result["duplicate_evidence_groups"]) == 1
    assert result["duplicate_evidence_groups"][0]["independent_corroboration"] is False
    assert "duplicate_evidence" in result["classifications"]
    assert result["conclusion_confidence"] is None


def test_change_assessment_without_explicit_baseline_is_insufficient():
    result = analyze_uncertainty(
        claim="Appetite changed.",
        task="identify_change",
        evidence=[{"id": "new-observation", "text": "Ate less at breakfast.", "source_type": "caregiver_report"}],
    )
    assert "missing_baseline" in result["classifications"]
    assert result["epistemic_state"] == "unknown"
    assert result["sufficiency"] == "insufficient_pending_resolution"
    assert result["conclusion_confidence"] is None


def test_explicit_baseline_allows_only_a_possible_change_assessment():
    result = analyze_uncertainty(
        claim="Appetite may have changed.",
        task="identify_change",
        evidence=[
            {"id": "baseline", "text": "Ate usual breakfast yesterday.", "source_type": "caregiver_report", "role": "baseline"},
            {"id": "current", "text": "Ate less at breakfast today.", "source_type": "caregiver_report"},
        ],
    )
    assert "missing_baseline" not in result["classifications"]
    assert result["sufficiency"] == "possible_change_only"
    assert result["conclusion_confidence"] is None


def test_baseline_without_current_observation_cannot_establish_change():
    result = analyze_uncertainty(
        claim="Appetite changed.",
        task="identify_change",
        evidence=[{
            "id": "baseline-only",
            "text": "Usual breakfast yesterday.",
            "source_type": "caregiver_report",
            "role": "baseline",
            "is_baseline": True,
        }],
    )
    assert "missing_baseline" in result["classifications"]
    assert result["epistemic_state"] == "unknown"
    assert result["comparison_basis"]["explicit_baseline_present"] is True
    assert result["comparison_basis"]["separate_comparison_observation_present"] is False
    assert result["sufficiency"] == "insufficient_pending_resolution"


def test_failed_or_truncated_source_is_not_a_negative_finding():
    result = analyze_uncertainty(
        claim="No appointment is listed.",
        task="summarize",
        evidence=[],
        source_access="truncated",
    )
    assert result["epistemic_state"] == "unknown"
    assert "knowledge_boundary" in result["classifications"]
    assert any("truncated" in limitation for limitation in result["limitations"])


def test_unreadable_evidence_reduces_sufficiency_without_confidence_inflation():
    result = analyze_uncertainty(
        claim="The document establishes the current instruction.",
        task="recommendation",
        evidence=[{
            "id": "unreadable-doc",
            "text": "",
            "source_type": "document",
            "verified": False,
            "readable": False,
            "extraction_status": "unreadable",
        }],
    )
    assert "low_quality_evidence" in result["classifications"]
    assert result["sufficiency"] == "insufficient_pending_resolution"
    assert result["conclusion_confidence"] is None


def test_malformed_optional_metadata_does_not_crash_or_create_certainty():
    result = analyze_uncertainty(
        claim="The care status is current.",
        task="summarize",
        evidence=[{
            "id": "malformed-metadata",
            "text": "Caregiver report exists.",
            "source_type": "caregiver_report",
            "verified": False,
            "temporal_scope": ["unexpected", "shape"],
            "extraction_status": {"unexpected": "shape"},
        }],
    )
    assert result["epistemic_state"] == "partially_known"
    assert result["conclusion_confidence"] is None
    assert "source_verification_uncertainty" in result["classifications"]


def test_assessment_never_claims_the_retrieved_evidence_set_is_complete():
    result = analyze_uncertainty(
        claim="The available note describes an appointment.",
        task="summarize",
        evidence=[{"id": "appointment-note", "text": "Appointment planned.", "source_type": "caregiver_report"}],
    )
    assert result["retrieval_scope"]["basis"] == "only_records_supplied_to_this_assessment"
    assert result["retrieval_scope"]["completeness_verified"] is False
    assert result["retrieval_scope"]["unknown_relevant_sources_may_exist"] is True
    assert result["assumptions"] == []


def test_orm_persisted_assessment_supersession_lcr_projection_and_rollback():
    """Exercise the real mapped tables and SQLAlchemy transaction semantics on SQLite."""
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import IntegrityError
    from app.core.database import Base
    from app.core.uncertainty_enums import EpistemicState, GapReason
    from app.models.care import Caregiver, Person
    from app.models.uncertainty import UncertaintyAssessment
    from app.models.care_continuity import CareStateRevision

    engine = create_engine("sqlite://")
    # Create the complete mapped schema so foreign-key dependencies used by
    # the LCR projection are present regardless of SQLAlchemy table ordering.
    Base.metadata.create_all(engine)

    analysis_old = {"epistemic_state": "unknown", "limitations": ["No current evidence."], "calibration_status": "not_calibrated"}
    analysis_new = {"epistemic_state": "partially_known", "limitations": ["Evidence is reported, not independently verified."], "calibration_status": "not_calibrated"}
    with Session(engine) as db:
        db.add(Person(id="orm-person-a", name="Test person"))
        db.flush()
        old = UncertaintyAssessment(
            id="orm-assessment-old", person_id="orm-person-a", subject_type="care_record",
            subject_id="medication", field="status", epistemic_state=EpistemicState.UNKNOWN,
            confidence=None, gap_reason=GapReason.INSUFFICIENT_EVIDENCE,
            context=[{"origin": "delta_engine", "analysis": analysis_old}],
            evidence_ids=None, is_active=True,
        )
        db.add(old)
        db.commit()

        # A later assessment supersedes, but does not delete, the earlier history.
        old.is_active = False
        old.superseded_by_assessment_id = "orm-assessment-new"
        db.add(UncertaintyAssessment(
            id="orm-assessment-new", person_id="orm-person-a", subject_type="care_record",
            subject_id="medication", field="status", epistemic_state=EpistemicState.PARTIALLY_KNOWN,
            confidence=None, gap_reason=None,
            context=[{"origin": "delta_engine", "delta_run_pending": False, "analysis": analysis_new}],
            evidence_ids=None, is_active=True,
        ))
        db.commit()

        history = db.query(UncertaintyAssessment).filter(
            UncertaintyAssessment.person_id == "orm-person-a",
            UncertaintyAssessment.subject_type == "care_record",
            UncertaintyAssessment.subject_id == "medication",
            UncertaintyAssessment.field == "status",
        ).all()
        assert len(history) == 2
        prior = next(item for item in history if item.id == "orm-assessment-old")
        current = next(item for item in history if item.id == "orm-assessment-new")
        assert prior.is_active is False
        assert prior.superseded_by_assessment_id == current.id
        assert current.is_active is True

        projection = build_lcr_context(db, "orm-person-a", limit_per_source=10)
        projected = projection["sources"]["uncertainty_assessments"]
        assert projected["returned_count"] == 1
        assert projected["items"][0]["id"] == "orm-assessment-new"
        assert projected["items"][0]["context"][0]["analysis"]["limitations"] == analysis_new["limitations"]

        # Force a real uniqueness failure after changing state; rollback must preserve
        # the committed active assessment and its historical predecessor.
        current.is_active = False
        db.add(UncertaintyAssessment(
            id="orm-assessment-new", person_id="orm-person-a", subject_type="care_record",
            subject_id="medication", field="status", epistemic_state=EpistemicState.UNKNOWN,
            confidence=None, context=[], evidence_ids=None, is_active=True,
        ))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        restored = db.query(UncertaintyAssessment).filter(
            UncertaintyAssessment.person_id == "orm-person-a"
        ).all()
        assert next(item for item in restored if item.id == "orm-assessment-new").is_active is True
        assert next(item for item in restored if item.id == "orm-assessment-old").is_active is False
    engine.dispose()
