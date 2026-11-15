from types import SimpleNamespace

from app.services.delta_engine import _canonical_change_classification


def _change(*, subject_type="care_domain", subject_id="mobility", change_type="increase"):
    # Change.confidence is deliberately uncalibrated in production.
    return SimpleNamespace(
        change_type=change_type,
        baseline_coverage_status="covered",
        current_coverage_status="covered",
        significance_verdict="meaningful_candidate",
        confidence=None,
        subject_type=subject_type,
        subject_id=subject_id,
    )


def _assessment(*, verdict="meaningful_candidate", confidence=0.95):
    return SimpleNamespace(
        verdict=verdict,
        significance_confidence=confidence,
        is_active=True,
    )


def _classify(change, *, evidence_complete=True, assessment=None):
    return _canonical_change_classification(
        change,
        evidence_complete=evidence_complete,
        significance_assessment=assessment,
    )


def test_increase_in_non_adverse_domain_is_not_assumed_to_be_more_frequent_or_better():
    classification, candidate = _classify(
        _change(subject_id="mobility"),
        assessment=_assessment(),
    )
    assert classification == "uncertain"
    assert candidate is False


def test_decrease_in_non_adverse_domain_is_not_assumed_to_be_improvement():
    classification, candidate = _classify(
        _change(subject_id="mobility", change_type="decrease"),
        assessment=_assessment(),
    )
    assert classification == "uncertain"
    assert candidate is False


def test_increase_in_explicit_adverse_domain_is_only_a_potential_worsening():
    classification, candidate = _classify(
        _change(subject_id="night_waking"),
        assessment=_assessment(),
    )
    assert classification == "potential_worsening"
    assert candidate is True


def test_decrease_in_explicit_adverse_domain_is_only_a_potential_improvement():
    classification, candidate = _classify(
        _change(subject_id="pain", change_type="decrease"),
        assessment=_assessment(),
    )
    assert classification == "potential_improvement"
    assert candidate is True


def test_incomplete_evidence_never_supports_a_directional_candidate():
    classification, candidate = _classify(
        _change(subject_id="night_waking"),
        evidence_complete=False,
        assessment=_assessment(),
    )
    assert classification == "uncertain"
    assert candidate is False


def test_change_without_linked_significance_assessment_is_uncertain():
    # A legacy significance_verdict or Change.confidence must not substitute
    # for the active linked SignificanceAssessment.
    classification, candidate = _classify(_change(subject_id="night_waking"))
    assert classification == "uncertain"
    assert candidate is False


def test_not_meaningful_assessment_cannot_produce_a_change_candidate():
    classification, candidate = _classify(
        _change(subject_id="night_waking"),
        assessment=_assessment(verdict="not_meaningful", confidence=0.99),
    )
    assert classification == "uncertain"
    assert candidate is False


def test_low_significance_confidence_cannot_produce_a_change_candidate():
    classification, candidate = _classify(
        _change(subject_id="night_waking"),
        assessment=_assessment(confidence=0.64),
    )
    assert classification == "uncertain"
    assert candidate is False


def test_directional_polarity_requires_an_exact_adverse_measure_label():
    for subject_id in ("no_pain", "pain_management", "mobility_with_falls"):
        classification, candidate = _classify(
            _change(subject_id=subject_id, change_type="increase"),
            assessment=_assessment(),
        )
        assert classification == "uncertain"
        assert candidate is False


def test_known_adverse_measure_aliases_remain_directionally_candidate_only():
    for subject_id in ("pain_score", "fall_count", "night_waking_frequency"):
        classification, candidate = _classify(
            _change(subject_id=subject_id, change_type="increase"),
            assessment=_assessment(),
        )
        assert classification == "potential_worsening"
        assert candidate is True
