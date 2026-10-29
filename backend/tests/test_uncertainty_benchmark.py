"""Adversarial benchmark for SolenOS uncertainty behavior.

Synthetic cases only. These tests measure deterministic contract adherence, not clinical
accuracy, probability calibration, or production readiness. Add cases when a regression
or newly discovered failure mode is identified.
"""
from datetime import datetime, timezone

import pytest

from app.services.uncertainty_intelligence import analyze_uncertainty


CLOCK = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def assess(**overrides):
    args = {
        "claim": "The current care situation is understood.",
        "task": "recommendation",
        "evidence": [],
        "now": CLOCK,
    }
    args.update(overrides)
    return analyze_uncertainty(**args)


@pytest.mark.parametrize(
    ("case_id", "overrides", "required_classification", "expected_state", "expected_sufficiency"),
    [
        (
            "U01_no_evidence",
            {"evidence": []},
            "insufficient_evidence",
            "unknown",
            "insufficient",
        ),
        (
            "U02_source_unavailable_is_not_negative",
            {"evidence": [], "source_access": "unavailable"},
            "knowledge_boundary",
            "unknown",
            "insufficient",
        ),
        (
            "U03_nonstandard_access_fails_closed",
            {"evidence": [{"id": "e1", "text": "Caregiver report.", "source_type": "caregiver_report", "verified": True}], "source_access": "vendor_returned_partialish"},
            "knowledge_boundary",
            "unknown",
            "insufficient",
        ),
        (
            "U04_unverified_evidence_does_not_become_known",
            {"evidence": [{"id": "e1", "text": "A report was entered.", "source_type": "caregiver_report", "verified": False}]},
            "source_verification_uncertainty",
            "partially_known",
            "insufficient",
        ),
        (
            "U05_conflicting_records_remain_conflicting",
            {"evidence": [{"id": "e1", "text": "Appointment is Monday.", "source_type": "document", "verified": True}], "unresolved_conflicts": [{"field": "appointment_date", "values": ["Monday", "Tuesday"]}]},
            "conflicting_evidence",
            "conflicting",
            "insufficient",
        ),
        (
            "U06_change_without_baseline_is_blocked",
            {"task": "change_detection", "evidence": [{"id": "e1", "text": "Ate less at breakfast today.", "source_type": "caregiver_report", "verified": True, "role": "current"}]},
            "missing_baseline",
            "unknown",
            "insufficient",
        ),
        (
            "U07_missing_required_field_is_explicit",
            {"required_fields": ["appointment_date"], "evidence": [{"id": "e1", "text": "A follow-up is planned.", "source_type": "caregiver_report", "verified": True}]},
            "missing_information",
            "unknown",
            "insufficient",
        ),
        (
            "U08_ambiguous_interpretation_is_not_resolved",
            {"evidence": [{"id": "e1", "text": "It happened again.", "source_type": "caregiver_report", "verified": True, "ambiguous": True}]},
            "ambiguous_source_interpretation",
            "ambiguous",
            "insufficient",
        ),
        (
            "U09_stale_evidence_is_flagged",
            {"evidence": [{"id": "e1", "text": "Medication list reviewed.", "source_type": "document", "verified": True, "observed_at": "2026-10-01T12:00:00Z", "max_age_days": 7}]},
            "outdated_information",
            "partially_known",
            "insufficient",
        ),
        (
            "U10_causal_language_requires_review",
            {"claim": "Reduced appetite was caused by the new routine.", "evidence": [{"id": "e1", "text": "Reduced appetite was reported.", "source_type": "caregiver_report", "verified": True}]},
            "causal_inference_review",
            "partially_known",
            "insufficient",
        ),
        (
            "U11_unknown_task_does_not_inherit_generic_sufficiency",
            {"task": "predict_the_future", "evidence": [{"id": "e1", "text": "A caregiver report is available.", "source_type": "caregiver_report", "verified": True}]},
            "unsupported_assessment_task",
            "unknown",
            "insufficient_pending_resolution",
        ),
        (
            "U12_confidence_is_never_fabricated",
            {"evidence": [{"id": "e1", "text": "A caregiver report is available.", "source_type": "caregiver_report", "verified": True}]},
            "source_attribution_incomplete",
            "partially_known",
            "insufficient",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_uncertainty_benchmark_contracts(
    case_id, overrides, required_classification, expected_state, expected_sufficiency
):
    result = assess(**overrides)

    assert required_classification in result["classifications"], case_id
    assert result["epistemic_state"] == expected_state, case_id
    assert result["sufficiency"] == expected_sufficiency, case_id
    assert result["review_required"] is True, case_id
    assert result["conclusion_confidence"] is None, case_id
    assert result["calibration_status"] == "not_calibrated", case_id
    assert result["retrieval_scope"]["completeness_verified"] is False, case_id
    assert result["retrieval_scope"]["unknown_relevant_sources_may_exist"] is True, case_id


def test_benchmark_duplicate_records_do_not_inflate_independent_support():
    result = assess(
        evidence=[
            {"id": "e1", "text": "Breakfast intake was lower.", "source_type": "caregiver_report", "verified": True},
            {"id": "e2", "text": "  breakfast   intake WAS lower. ", "source_type": "derived_summary", "verified": True},
        ]
    )
    assert "duplicate_evidence" in result["classifications"]
    assert result["evidence_record_count"] == 2
    assert result["independent_evidence_record_count"] == 1
    assert result["conclusion_confidence"] is None
    assert result["review_required"] is True


def test_benchmark_accepted_assumption_requires_verified_linked_support_and_review():
    result = assess(
        evidence=[
            {"id": "e1", "text": "A caregiver reported reduced appetite.", "source_type": "caregiver_report", "verified": True},
        ],
        assumptions=[
            {
                "statement": "Reduced appetite is caused by the new routine.",
                "status": "accepted",
                "supporting_evidence_ids": ["e1"],
                "reviewed_by_caregiver_id": "caregiver-1",
                "resolution_rationale": "Reviewed source note.",
            }
        ],
    )
    # A verified record reference proves only that the record exists and is marked
    # verified; it does not establish semantic entailment of the causal hypothesis.
    assert result["assumptions"][0]["status"] == "accepted"
    assert result["assumptions"][0]["engine_semantic_support_verified"] is False
    assert "candidate_support_not_semantically_verified" in result["classifications"]
    assert result["review_required"] is True
    assert result["conclusion_confidence"] is None


def test_benchmark_zero_baseline_does_not_produce_non_finite_metrics():
    result = assess(
        task="change_detection",
        evidence=[
            {"id": "e1", "text": "No intake was recorded.", "source_type": "caregiver_report", "verified": True, "role": "baseline"},
            {"id": "e2", "text": "Intake was recorded today.", "source_type": "caregiver_report", "verified": True, "role": "current"},
        ],
    )
    assert result["comparison_basis"]["explicit_baseline_present"] is True
    assert result["comparison_basis"]["separate_comparison_observation_present"] is True
    assert result["comparison_basis"]["change_established_by_this_assessment"] is False
    assert result["conclusion_confidence"] is None
