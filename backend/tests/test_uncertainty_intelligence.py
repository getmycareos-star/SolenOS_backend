"""Behavioral tests for uncertainty intelligence. Fixtures are synthetic and test-only."""
from datetime import datetime, timezone
import json

import pytest
from sqlalchemy import Boolean, Column, DateTime, Float, MetaData, String, Table, Text, create_engine, insert

from app.services.lcr_context import build_lcr_context
from app.services.uncertainty_intelligence import analyze_uncertainty
from app.services.delta_engine import _match_finding_evidence


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
