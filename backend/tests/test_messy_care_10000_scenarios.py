"""Ten-thousand-case deterministic adversarial uncertainty contract benchmark.

This is a synthetic metadata/contract stress test, not clinical validation.
Each case has an independently specified expected classification. The test
also enforces that no case emits calibrated confidence or bypasses review.
"""
from datetime import datetime, timedelta, timezone

from app.services.uncertainty_intelligence import analyze_uncertainty

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
CASE_FAMILIES = (
    "empty_retrieval",
    "duplicate_lineage",
    "inaccessible_source",
    "partial_document",
    "missing_observation_time",
    "unresolved_conflict",
    "missing_required_field",
    "unverified_source",
    "causal_claim",
    "stale_source",
)
EXPECTED = {
    "empty_retrieval": "insufficient_evidence",
    "duplicate_lineage": "duplicate_evidence",
    "inaccessible_source": "knowledge_boundary",
    "partial_document": "low_quality_evidence",
    "missing_observation_time": "temporal_uncertainty",
    "unresolved_conflict": "conflicting_evidence",
    "missing_required_field": "missing_information",
    "unverified_source": "source_verification_uncertainty",
    "causal_claim": "causal_inference_review",
    "stale_source": "outdated_information",
}


def _case(family: str, variant: int):
    evidence_id = f"synthetic-{family}-{variant:04d}"
    common = {
        "id": evidence_id,
        "source_type": "caregiver_report",
        "text": f"Synthetic care observation variant {variant}.",
        "verified": True,
    }
    claim = "The care record requires review."
    task = "recommendation"
    evidence = [common]
    kwargs = {}

    if family == "empty_retrieval":
        evidence = []
    elif family == "duplicate_lineage":
        evidence = [
            {**common, "source_lineage_id": f"lineage-{variant:04d}"},
            {
                **common,
                "id": evidence_id + "-copy",
                "source_type": "derived_summary",
                "source_lineage_id": f"lineage-{variant:04d}",
            },
        ]
    elif family == "inaccessible_source":
        evidence = []
        kwargs["source_access"] = "permission_denied"
    elif family == "partial_document":
        evidence = [{
            **common,
            "source_type": "document",
            "quality": "partial",
            "extraction_status": "partial",
            "readable": False,
        }]
    elif family == "missing_observation_time":
        evidence = [{
            **common,
            "temporal_scope": "time_sensitive",
        }]
    elif family == "unresolved_conflict":
        kwargs["unresolved_conflicts"] = [{
            "field": "mobility_support",
            "values": [f"independent report A {variant}", f"independent report B {variant}"],
        }]
    elif family == "missing_required_field":
        kwargs["required_fields"] = ["attendance_status"]
        evidence = [{**common, "field": "appointment_date", "value": f"date-{variant}"}]
    elif family == "unverified_source":
        evidence = [{**common, "verified": False}]
    elif family == "causal_claim":
        claim = f"The new routine caused the reported change in case {variant}."
    elif family == "stale_source":
        evidence = [{
            **common,
            "observed_at": (NOW - timedelta(days=31 + (variant % 365))).isoformat(),
            "max_age_days": 30,
        }]
    else:
        raise AssertionError(f"Unknown case family: {family}")

    return claim, task, evidence, kwargs


def test_10000_adversarial_uncertainty_scenarios_preserve_safety_contract():
    executed = 0
    family_counts = {family: 0 for family in CASE_FAMILIES}

    for family in CASE_FAMILIES:
        expected = EXPECTED[family]
        for variant in range(1000):
            claim, task, evidence, kwargs = _case(family, variant)
            result = analyze_uncertainty(
                claim=claim,
                task=task,
                evidence=evidence,
                now=NOW,
                **kwargs,
            )
            assert expected in result["classifications"], (
                f"{family}/{variant}: expected {expected}; "
                f"actual={result['classifications']}"
            )
            assert result["conclusion_confidence"] is None, f"{family}/{variant}: fabricated confidence"
            assert result["calibration_status"] == "not_calibrated"
            assert result["review_required"] is True
            assert result["retrieval_scope"]["completeness_verified"] is False
            assert result["retrieval_scope"]["unknown_relevant_sources_may_exist"] is True
            assert result["source_reliability"] == "not_quantified"
            executed += 1
            family_counts[family] += 1

    assert executed == 10_000
    assert all(count == 1000 for count in family_counts.values())
    print(f"MESSY_CARE_10000_SCENARIOS_EXECUTED={executed}")
    print(f"MESSY_CARE_10000_FAMILY_COUNTS={family_counts}")
