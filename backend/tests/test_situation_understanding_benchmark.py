"""Fifteen adversarial, messy-care scenarios for situation understanding.

Synthetic contract benchmark only. Passing does not establish clinical accuracy,
calibration, database persistence, authorization, or production readiness.
"""
from datetime import datetime, timezone

import pytest

from app.services.uncertainty_intelligence import analyze_uncertainty

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def assess(claim, *, task="recommendation", evidence=(), **kwargs):
    return analyze_uncertainty(
        claim=claim, task=task, evidence=list(evidence), now=NOW, **kwargs
    )


def record(eid, text, **kwargs):
    return {
        "id": eid,
        "text": text,
        "source_type": kwargs.pop("source_type", "caregiver_report"),
        "verified": kwargs.pop("verified", True),
        **kwargs,
    }


def assert_safe_contract(result):
    assert result["conclusion_confidence"] is None
    assert result["calibration_status"] == "not_calibrated"
    assert result["review_required"] is True
    assert result["retrieval_scope"]["completeness_verified"] is False
    assert result["retrieval_scope"]["unknown_relevant_sources_may_exist"] is True


SCENARIOS = [
    pytest.param(
        "01_missing_personal_baseline",
        "Her walking has deteriorated compared with usual.",
        "change_detection",
        [record("walk-now", "Needed help walking to the bathroom this morning.", role="current")],
        {},
        "missing_baseline",
        id="01_missing-personal-baseline",
    ),
    pytest.param(
        "02_conflicting_medication_accounts",
        "The evening dose was taken.",
        "recommendation",
        [record("family", "Daughter says dose was given."), record("log", "Medication log says dose missed.", source_type="medication_log")],
        {"unresolved_conflicts": [{"field": "evening_dose", "values": ["given", "missed"]}]},
        "conflicting_evidence",
        id="02-conflicting-medication-accounts",
    ),
    pytest.param(
        "03_copied_note_false_corroboration",
        "Two independent sources confirm poor intake.",
        "recommendation",
        [record("note-a", "Ate only a few bites at lunch.", source_lineage_id="family-note-7"), record("summary-b", "Ate only a few bites at lunch.", source_type="derived_summary", source_lineage_id="family-note-7")],
        {},
        "duplicate_evidence",
        id="03-copied-note-false-corroboration",
    ),
    pytest.param(
        "04_inaccessible_sources_not_negative",
        "No falls occurred this week.",
        "recommendation",
        [],
        {"source_access": "permission_denied"},
        "knowledge_boundary",
        id="04-inaccessible-sources-not-negative",
    ),
    pytest.param(
        "05_partial_ocr_document",
        "Discharge instructions confirm the new schedule.",
        "recommendation",
        [record("discharge-scan", "Take ... daily; remaining line unreadable", source_type="document", quality="partial", extraction_status="partial", readable=False)],
        {},
        "low_quality_evidence",
        id="05-partial-ocr-document",
    ),
    pytest.param(
        "06_stale_care-plan",
        "The current care plan still requires two-person transfers.",
        "recommendation",
        [record("plan-2024", "Two-person transfer required.", source_type="care_plan", observed_at="2024-01-01T09:00:00Z", max_age_days=30)],
        {},
        "outdated_information",
        id="06-stale-care-plan",
    ),
    pytest.param(
        "07_future-dated-observation",
        "Confusion increased after lunch today.",
        "change_detection",
        [record("future", "More confused after lunch.", observed_at="2026-10-30T13:00:00Z", temporal_scope="current")],
        {},
        "temporal_uncertainty",
        id="07-future-dated-observation",
    ),
    pytest.param(
        "08_time-missing-on-sensitive-report",
        "Night waking is increasing.",
        "change_detection",
        [record("sleep-note", "Awake repeatedly overnight.", temporal_scope="time_sensitive")],
        {},
        "temporal_uncertainty",
        id="08-time-missing-sensitive-report",
    ),
    pytest.param(
        "09_unverified-medication-event",
        "The antibiotic was taken as prescribed.",
        "high_consequence_assessment",
        [record("text-message", "I think she took it.", verified=False, source_type="family_message")],
        {},
        "source_verification_uncertainty",
        id="09-unverified-medication-event",
    ),
    pytest.param(
        "10_temporal-sequence-claimed-as-cause",
        "The new evening routine caused agitation.",
        "recommendation",
        [record("routine", "New evening routine started Monday.", observed_at="2026-10-26T18:00:00Z"), record("agitation", "Agitation reported Tuesday evening.", observed_at="2026-10-27T20:00:00Z")],
        {},
        "causal_inference_review",
        id="10-temporal-sequence-not-causation",
    ),
    pytest.param(
        "11_ambiguous-source-referent",
        "That was worse than before.",
        "recommendation",
        [record("chat", "That was worse than before.", ambiguous=True, interpretation_status="needs_clarification")],
        {},
        "ambiguous_source_interpretation",
        id="11-ambiguous-referent",
    ),
    pytest.param(
        "12_appointment-plan-mistaken-for-attendance",
        "The specialist appointment was attended.",
        "recommendation",
        [record("calendar", "Specialist appointment booked for Thursday.", source_type="calendar")],
        {"required_fields": ["attendance_status"]},
        "missing_information",
        id="12-plan-is-not-attendance",
    ),
    pytest.param(
        "13_competing-explanations",
        "Reduced appetite is caused by the new medication.",
        "recommendation",
        [record("meal", "Ate less at dinner.", observed_at="2026-10-28T18:00:00Z")],
        {"alternatives": [{"hypothesis": "Illness or oral discomfort may also explain reduced intake.", "status": "unresolved", "evidence_ids": ["meal"]}]},
        "unresolved_alternative_hypotheses",
        id="13-competing-explanations",
    ),
    pytest.param(
        "14_candidate-points-to-another-record",
        "The new symptom is confirmed.",
        "recommendation",
        [record("current-1", "Family reports a new symptom.")],
        {"assumptions": [{"statement": "The symptom is new.", "status": "accepted", "supporting_evidence_ids": ["record-from-another-person"], "reviewed_by_caregiver_id": "caregiver-1", "resolution_rationale": "Previously discussed"}]},
        "invalid_candidate_evidence_reference",
        id="14-cross-record-evidence-reference",
    ),
    pytest.param(
        "15_unsupported-prediction",
        "She will become unsafe living alone next month.",
        "predict_the_future",
        [record("single-note", "She left the kettle on once.", observed_at="2026-10-28T08:00:00Z")],
        {},
        "unsupported_assessment_task",
        id="15-unsupported-future-prediction",
    ),
]


@pytest.mark.parametrize(
    "case_id,claim,task,evidence,extra,expected_classification",
    SCENARIOS,
)
def test_messy_care_situation_understanding_contract(
    case_id, claim, task, evidence, extra, expected_classification
):
    result = assess(claim, task=task, evidence=evidence, **extra)
    assert_safe_contract(result)
    assert expected_classification in result["classifications"], (
        f"{case_id}: expected {expected_classification}; "
        f"actual classifications={result['classifications']}; "
        f"limitations={result['limitations']}"
    )

