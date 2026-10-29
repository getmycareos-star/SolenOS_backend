"""Situation-understanding adversarial/metamorphic benchmark.

This benchmark measures deterministic safety contracts in the uncertainty layer.
It is not a clinical-accuracy, calibration, or production-readiness score. Synthetic
inputs are deliberately sparse; a safe result preserves uncertainty rather than
manufacturing a baseline, causal link, source independence, or confidence.
"""
from datetime import datetime, timezone

import pytest

from app.services.uncertainty_intelligence import analyze_uncertainty


NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def assess(*, claim, task="recommendation", evidence=(), **kwargs):
    return analyze_uncertainty(
        claim=claim,
        task=task,
        evidence=list(evidence),
        now=NOW,
        **kwargs,
    )


def assert_uncertainty_is_not_overstated(result):
    assert result["conclusion_confidence"] is None
    assert result["calibration_status"] == "not_calibrated"
    assert result["review_required"] is True
    assert result["retrieval_scope"]["completeness_verified"] is False
    assert result["retrieval_scope"]["unknown_relevant_sources_may_exist"] is True


@pytest.mark.parametrize(
    "case_id,claim,task,evidence,extra",
    [
        (
            "SU01_no_baseline",
            "Eating has changed from the person's usual pattern.",
            "change_detection",
            [{"id": "current", "text": "Ate less at breakfast today.", "source_type": "caregiver_report", "verified": True, "role": "current"}],
            {},
        ),
        (
            "SU02_single_observation",
            "The person has a persistent change in sleep.",
            "change_detection",
            [{"id": "e1", "text": "Slept poorly last night.", "source_type": "caregiver_report", "verified": True}],
            {},
        ),
        (
            "SU03_causal_claim_temporal_only",
            "The new routine caused reduced appetite.",
            "recommendation",
            [
                {"id": "e1", "text": "A new routine began on Monday.", "source_type": "caregiver_report", "verified": True, "observed_at": "2026-10-26T09:00:00Z"},
                {"id": "e2", "text": "Reduced appetite was reported on Tuesday.", "source_type": "caregiver_report", "verified": True, "observed_at": "2026-10-27T09:00:00Z"},
            ],
            {},
        ),
        (
            "SU04_reporting_rate_not_event_rate",
            "The event frequency increased.",
            "change_detection",
            [
                {"id": "baseline", "text": "One event was recorded in the earlier period.", "source_type": "caregiver_report", "verified": True, "role": "baseline"},
                {"id": "current", "text": "Three notes were entered in the current period.", "source_type": "caregiver_report", "verified": True, "role": "current"},
            ],
            {},
        ),
        (
            "SU05_duplicate_source_not_independent",
            "Multiple independent sources confirm the same change.",
            "recommendation",
            [
                {"id": "e1", "text": "Breakfast intake was lower than usual according to the caregiver report.", "source_type": "caregiver_report", "verified": True, "source_lineage_id": "source-1"},
                {"id": "e2", "text": "Breakfast intake was lower than usual according to the caregiver report.", "source_type": "derived_summary", "verified": True, "source_lineage_id": "source-1"},
            ],
            {},
        ),
        (
            "SU06_unknown_access_not_negative",
            "No relevant event occurred.",
            "recommendation",
            [],
            {"source_access": "unavailable"},
        ),
        (
            "SU07_missing_domain_field",
            "The appointment was attended.",
            "recommendation",
            [{"id": "e1", "text": "A follow-up appointment was planned.", "source_type": "caregiver_report", "verified": True}],
            {"required_fields": ["attendance_status"]},
        ),
        (
            "SU08_conflicting_dates",
            "The appointment is on Monday.",
            "recommendation",
            [{"id": "e1", "text": "One note says Monday.", "source_type": "document", "verified": True}],
            {"unresolved_conflicts": [{"field": "appointment_date", "values": ["Monday", "Tuesday"]}]},
        ),
        (
            "SU09_unverified_report",
            "The medication was taken.",
            "recommendation",
            [{"id": "e1", "text": "Medication may have been taken.", "source_type": "caregiver_report", "verified": False}],
            {},
        ),
        (
            "SU10_unanchored_time",
            "The person's condition has worsened over time.",
            "change_detection",
            [{"id": "e1", "text": "The person seemed more tired.", "source_type": "caregiver_report", "verified": True}],
            {},
        ),
        (
            "SU11_caregiver_capacity_not_inferred",
            "The caregiver is unable to provide care.",
            "recommendation",
            [{"id": "e1", "text": "The caregiver said today was difficult.", "source_type": "caregiver_report", "verified": True}],
            {},
        ),
        (
            "SU12_unsupported_task",
            "The person will deteriorate next month.",
            "predict_the_future",
            [{"id": "e1", "text": "A caregiver report is available.", "source_type": "caregiver_report", "verified": True}],
            {},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_situation_understanding_adversarial_cases_preserve_limits(
    case_id, claim, task, evidence, extra
):
    result = assess(claim=claim, task=task, evidence=evidence, **extra)
    assert_uncertainty_is_not_overstated(result)


def test_situation_understanding_ordering_only_does_not_create_causation():
    earlier = assess(
        claim="The new routine caused reduced appetite.",
        evidence=[
            {"id": "routine", "text": "A new routine began.", "source_type": "caregiver_report", "verified": True, "observed_at": "2026-10-26T09:00:00Z"},
            {"id": "appetite", "text": "Reduced appetite was reported later.", "source_type": "caregiver_report", "verified": True, "observed_at": "2026-10-27T09:00:00Z"},
        ],
    )
    reversed_order = assess(
        claim="The new routine caused reduced appetite.",
        evidence=[
            {"id": "appetite", "text": "Reduced appetite was reported later.", "source_type": "caregiver_report", "verified": True, "observed_at": "2026-10-27T09:00:00Z"},
            {"id": "routine", "text": "A new routine began.", "source_type": "caregiver_report", "verified": True, "observed_at": "2026-10-26T09:00:00Z"},
        ],
    )
    assert "causal_inference_review" in earlier["classifications"]
    assert "causal_inference_review" in reversed_order["classifications"]
    assert_uncertainty_is_not_overstated(earlier)
    assert_uncertainty_is_not_overstated(reversed_order)


def test_situation_understanding_unverified_evidence_cannot_become_calibrated_by_duplication():
    base = assess(
        claim="The current care situation is understood.",
        evidence=[{"id": "e1", "text": "A caregiver reported reduced appetite.", "source_type": "caregiver_report", "verified": False}],
    )
    duplicated = assess(
        claim="The current care situation is understood.",
        evidence=[
            {"id": "e1", "text": "A caregiver reported reduced appetite.", "source_type": "caregiver_report", "verified": False},
            {"id": "e2", "text": "A caregiver reported reduced appetite.", "source_type": "derived_summary", "verified": False},
        ],
    )
    assert base["conclusion_confidence"] is None
    assert duplicated["conclusion_confidence"] is None
    assert base["calibration_status"] == duplicated["calibration_status"] == "not_calibrated"
    assert base["review_required"] is duplicated["review_required"] is True


@pytest.mark.parametrize("task", ["change_detection", "recommendation", "handoff", "professional_summary"])
def test_situation_understanding_sparse_evidence_is_not_promoted_across_consumers(task):
    result = assess(
        claim="The person's current condition is stable.",
        task=task,
        evidence=[{"id": "e1", "text": "One caregiver note was entered today.", "source_type": "caregiver_report", "verified": True}],
    )
    assert_uncertainty_is_not_overstated(result)
