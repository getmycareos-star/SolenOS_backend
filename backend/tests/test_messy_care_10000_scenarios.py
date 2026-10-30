"""10,000 deterministic adversarial care-information scenarios.

This is a deterministic service-contract corpus, not clinical validation. It deliberately
keeps its claims narrow: classification and uncertainty behavior are tested here;
PostgreSQL persistence, identity isolation, and longitudinal reconstruction are tested
separately by the PostgreSQL integration suite.
"""
from datetime import datetime, timezone
import hashlib
import json

import pytest

from app.services.uncertainty_intelligence import analyze_uncertainty

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
CASES_PER_FAMILY = 500

# Twenty independently labelled failure families. Labels are assigned from the
# construction rule, never inferred from the service response.
FAMILIES = (
    "contradiction",
    "inaccessible_source",
    "duplicate_lineage",
    "unverified_report",
    "missing_source_attribution",
    "missing_event_time",
    "stale_evidence",
    "unreadable_evidence",
    "causal_overreach",
    "invalid_evidence_reference",
    "missing_baseline",
    "ambiguous_interpretation",
    "missing_required_information",
    "unresolved_assumption",
    "unresolved_alternative",
    "no_evidence",
    "explicit_uncertainty",
    "future_timestamp",
    "invalid_timestamp",
    "nonstandard_source_access",
)

EXPECTED = {
    "contradiction": "conflicting_evidence",
    "inaccessible_source": "knowledge_boundary",
    "duplicate_lineage": "duplicate_evidence",
    "unverified_report": "source_verification_uncertainty",
    "missing_source_attribution": "source_attribution_incomplete",
    "missing_event_time": "temporal_uncertainty",
    "stale_evidence": "outdated_information",
    "unreadable_evidence": "low_quality_evidence",
    "causal_overreach": "causal_inference_review",
    "invalid_evidence_reference": "invalid_candidate_evidence_reference",
    "missing_baseline": "missing_baseline",
    "ambiguous_interpretation": "ambiguous_source_interpretation",
    "missing_required_information": "missing_information",
    "unresolved_assumption": "unresolved_assumptions",
    "unresolved_alternative": "unresolved_alternative_hypotheses",
    "no_evidence": "insufficient_evidence",
    "explicit_uncertainty": "explicit_uncertainty",
    "future_timestamp": "temporal_uncertainty",
    "invalid_timestamp": "temporal_uncertainty",
    "nonstandard_source_access": "knowledge_boundary",
}


def _record(evidence_id, text, **fields):
    return {
        "id": evidence_id,
        "text": text,
        "source_type": "caregiver_report",
        "verified": True,
        **fields,
    }


def build_cases():
    cases = []
    for family in FAMILIES:
        for index in range(CASES_PER_FAMILY):
            suffix = f"{family}-{index:04d}"
            evidence_id = f"ev-{hashlib.sha256(suffix.encode()).hexdigest()[:20]}"
            claim = f"Care-history statement for adversarial case {suffix}."
            evidence = [_record(evidence_id, f"Original source statement {suffix}.")]
            extra = {}
            source_access = "available"
            task = "recommendation"

            if family == "contradiction":
                extra["unresolved_conflicts"] = [{
                    "field": f"care_status_{index % 17}",
                    "values": ["reported_present", "reported_absent"],
                    "source_ids": [evidence_id, f"conflict-{index}"],
                }]
            elif family == "inaccessible_source":
                source_access = ("permission_denied", "timeout", "stale_cache", "partial")[index % 4]
            elif family == "duplicate_lineage":
                lineage = f"origin-{index % 23}"
                evidence = [
                    _record(evidence_id, f"Source statement {suffix}.", source_lineage_id=lineage),
                    _record(
                        f"{evidence_id}-copy",
                        f"Derived copy {suffix}.",
                        source_lineage_id=lineage,
                        source_type="derived_summary",
                    ),
                ]
            elif family == "unverified_report":
                evidence[0]["verified"] = False
            elif family == "missing_source_attribution":
                evidence[0].pop("source_type")
            elif family == "missing_event_time":
                evidence[0]["temporal_scope"] = "current"
            elif family == "stale_evidence":
                evidence[0]["observed_at"] = "2026-01-01T00:00:00Z"
                evidence[0]["max_age_days"] = 30
            elif family == "unreadable_evidence":
                evidence[0]["quality"] = "partial"
                evidence[0]["extraction_status"] = "partial"
                evidence[0]["readable"] = False
            elif family == "causal_overreach":
                claim = f"The new care routine caused the reported change in case {suffix}."
            elif family == "invalid_evidence_reference":
                extra["assumptions"] = [{
                    "statement": f"Unverified assumption {suffix}.",
                    "status": "accepted",
                    "supporting_evidence_ids": [f"not-retrieved-{index}"],
                    "reviewed_by_caregiver_id": f"caregiver-{index}",
                    "resolution_rationale": "Synthetic adversarial invalid-reference fixture.",
                }]
            elif family == "missing_baseline":
                task = "change_detection"
                evidence[0]["role"] = "current"
            elif family == "ambiguous_interpretation":
                evidence[0]["ambiguous"] = True
                evidence[0]["interpretation_status"] = "needs_clarification"
            elif family == "missing_required_information":
                extra["required_fields"] = ["attendance_status"]
            elif family == "unresolved_assumption":
                extra["assumptions"] = [{
                    "statement": f"Unverified assumption {suffix}.",
                    "status": "unresolved",
                    "supporting_evidence_ids": [evidence_id],
                }]
            elif family == "unresolved_alternative":
                extra["alternatives"] = [{
                    "hypothesis": f"Alternative explanation {suffix}.",
                    "status": "unresolved",
                    "evidence_ids": [evidence_id],
                }]
            elif family == "no_evidence":
                evidence = []
            elif family == "explicit_uncertainty":
                claim = f"Maybe the care event occurred in case {suffix}."
            elif family == "future_timestamp":
                evidence[0]["observed_at"] = "2026-10-30T13:00:00Z"
                evidence[0]["temporal_scope"] = "current"
            elif family == "invalid_timestamp":
                evidence[0]["observed_at"] = "not-a-timestamp"
            elif family == "nonstandard_source_access":
                source_access = "source_registry_inconsistent"

            cases.append({
                "case_id": suffix,
                "family": family,
                "expected": EXPECTED[family],
                "claim": claim,
                "task": task,
                "evidence": evidence,
                "extra": extra,
                "source_access": source_access,
            })
    return cases


CASES = build_cases()
CORPUS_SHA256 = hashlib.sha256(
    json.dumps(CASES, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_10000_adversarial_messy_care_scenarios(case):
    result = analyze_uncertainty(
        claim=case["claim"],
        task=case["task"],
        evidence=case["evidence"],
        source_access=case["source_access"],
        now=NOW,
        **case["extra"],
    )

    assert case["expected"] in result["classifications"], (
        f"{case['case_id']}: expected {case['expected']}; "
        f"got {result['classifications']}; limitations={result['limitations']}"
    )
    assert result["conclusion_confidence"] is None
    assert result["review_required"] is True
    assert result["calibration_status"] == "not_calibrated"
    assert result["source_reliability"] == "not_quantified"
    assert result["retrieval_scope"]["completeness_verified"] is False
    assert result["retrieval_scope"]["unknown_relevant_sources_may_exist"] is True
    assert result["comparison_basis"]["change_established_by_this_assessment"] is False

    supplied_ids = {item["id"] for item in case["evidence"] if item.get("id")}
    returned_ids = {item["id"] for item in result["evidence_refs"] if item.get("id")}
    assert returned_ids <= supplied_ids

    if case["family"] == "contradiction":
        assert result["epistemic_state"] == "conflicting"
        assert result["unresolved_conflicts"] == case["extra"]["unresolved_conflicts"]
    if case["family"] == "duplicate_lineage":
        assert result["independent_evidence_record_count"] == 1
        assert result["duplicate_evidence_groups"]
    if case["family"] == "invalid_evidence_reference":
        assert result["assumptions"][0]["status"] == "unresolved"
        assert result["assumptions"][0]["evidence_references_valid"] is False
    if case["family"] in {"inaccessible_source", "nonstandard_source_access", "no_evidence"}:
        assert result["epistemic_state"] == "unknown"
    if case["family"] == "unresolved_assumption":
        assert result["assumptions"][0]["status"] == "unresolved"
    if case["family"] == "unresolved_alternative":
        assert result["alternatives"][0]["status"] == "unresolved"
    if case["family"] == "explicit_uncertainty":
        assert result["explicit_uncertainty"] is True


def test_10000_case_corpus_is_complete_and_independently_labelled():
    assert len(CASES) == 10_000
    assert len({case["case_id"] for case in CASES}) == 10_000
    assert {
        family: sum(case["family"] == family for case in CASES)
        for family in FAMILIES
    } == {family: CASES_PER_FAMILY for family in FAMILIES}
    assert all(case["expected"] == EXPECTED[case["family"]] for case in CASES)
    print(f"MESSY_CARE_10000_CORPUS_SHA256={CORPUS_SHA256}")
    print("MESSY_CARE_10000_FAMILIES=20")
    print("MESSY_CARE_10000_CASES_DECLARED=10000")
