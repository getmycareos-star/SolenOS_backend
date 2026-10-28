"""Behavioral tests for uncertainty intelligence. Fixtures are synthetic and test-only."""
from datetime import datetime, timezone

import pytest

from app.services.uncertainty_intelligence import analyze_uncertainty


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
