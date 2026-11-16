"""Pure adversarial tests for the independent longitudinal interpretation evaluator."""
from app.services.delta_engine import evaluate_longitudinal_interpretations


def _item(**overrides):
    value = {
        "authoritative_change_id": "change-1",
        "domain": "nutrition",
        "name": "eating",
        "change_type": "increase",
        "classification": "potential_worsening",
        "certainty": "candidate",
        "evidence_supported": True,
        "claim_supported": True,
        "source_reference_status": "matched",
        "baseline_coverage_status": "covered",
        "current_coverage_status": "covered",
        "source_record_refs": [
            {"record_type": "care_event", "record_id": "event-1", "source_excerpt": "Ate less at dinner."}
        ],
    }
    value.update(overrides)
    return value


def test_supported_interpretation_requires_linked_evidence_and_complete_coverage():
    item = _item()
    result = evaluate_longitudinal_interpretations([item])
    assert result["overall_status"] == "supported"
    assert item["interpretation_status"] == "supported"
    assert item["interpretation_caregiver_visible"] is True
    assert item["interpretation_supporting_evidence_ids"] == ["event-1"]


def test_missing_source_is_withheld_as_insufficient_evidence():
    item = _item(source_record_refs=[])
    result = evaluate_longitudinal_interpretations([item])
    assert result["overall_status"] == "unsupported"
    assert item["interpretation_status"] == "insufficient_evidence"
    assert item["interpretation_caregiver_visible"] is False
    assert item["interpretation_supporting_evidence_ids"] == []


def test_conflicting_reports_are_preserved_without_selecting_a_winner():
    item = _item(
        change_type="conflicting",
        classification="conflicting",
        source_record_refs=[
            {"record_type": "care_event", "record_id": "report-a"},
            {"record_type": "care_event", "record_id": "report-b"},
        ],
    )
    result = evaluate_longitudinal_interpretations([item])
    assert result["overall_status"] == "conflicting"
    assert item["interpretation_status"] == "conflicting"
    assert item["interpretation_contradictory_evidence_ids"] == ["report-a", "report-b"]
    assert item["interpretation_caregiver_visible"] is True


def test_explicitly_historical_evidence_is_not_presented_as_current():
    item = _item(historical_only=True)
    result = evaluate_longitudinal_interpretations([item])
    assert result["overall_status"] == "stale_evidence"
    assert item["interpretation_status"] == "stale_evidence"
    assert item["interpretation_caregiver_visible"] is False


def test_incomplete_baseline_downgrades_interpretation_to_partial_support():
    item = _item(baseline_coverage_status="unknown")
    result = evaluate_longitudinal_interpretations([item])
    assert result["overall_status"] == "partially_supported"
    assert item["interpretation_status"] == "partially_supported"
    assert item["interpretation_caregiver_visible"] is True


def test_failed_source_verification_is_unsupported_even_with_a_reference():
    item = _item(evidence_supported=False, claim_supported=False)
    result = evaluate_longitudinal_interpretations([item])
    assert result["overall_status"] == "unsupported"
    assert item["interpretation_status"] == "unsupported"
    assert item["interpretation_caregiver_visible"] is False


def test_evaluator_does_not_mutate_source_references_or_create_a_care_record():
    refs = [{"record_type": "care_event", "record_id": "event-original", "source_excerpt": "Original observation."}]
    item = _item(source_record_refs=refs)
    result = evaluate_longitudinal_interpretations([item])
    assert item["source_record_refs"] == refs
    assert result["source_evidence_mutated"] is False
    assert result["creates_competing_care_record"] is False

def test_complete_comparison_with_no_emitted_change_is_not_mislabeled_insufficient():
    result = evaluate_longitudinal_interpretations(
        [], history_coverage={"complete": True}
    )
    assert result["overall_status"] == "no_change_detected"
    assert result["caregiver_visible"] is False
    assert result["counts"]["no_change_detected"] == 1
