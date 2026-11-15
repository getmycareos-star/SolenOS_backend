from types import SimpleNamespace

from app.services.delta_engine import _canonical_change_classification


def _change(*, subject_type="care_domain", subject_id="mobility", change_type="increase"):
    return SimpleNamespace(
        change_type=change_type,
        baseline_coverage_status="covered",
        current_coverage_status="covered",
        significance_verdict="meaningful",
        confidence=0.95,
        subject_type=subject_type,
        subject_id=subject_id,
    )


def test_increase_in_non_adverse_domain_is_not_assumed_to_be_more_frequent_or_better():
    classification, supported = _canonical_change_classification(
        _change(subject_id="mobility", change_type="increase"),
        evidence_complete=True,
    )
    assert classification == "uncertain"
    assert supported is False


def test_decrease_in_non_adverse_domain_is_not_assumed_to_be_improvement():
    classification, supported = _canonical_change_classification(
        _change(subject_id="mobility", change_type="decrease"),
        evidence_complete=True,
    )
    assert classification == "uncertain"
    assert supported is False


def test_increase_in_explicit_adverse_domain_is_classified_as_worsened():
    classification, supported = _canonical_change_classification(
        _change(subject_id="night_waking", change_type="increase"),
        evidence_complete=True,
    )
    assert classification == "worsened"
    assert supported is True


def test_decrease_in_explicit_adverse_domain_is_classified_as_improved():
    classification, supported = _canonical_change_classification(
        _change(subject_id="pain", change_type="decrease"),
        evidence_complete=True,
    )
    assert classification == "improved"
    assert supported is True


def test_incomplete_evidence_never_supports_a_directional_claim():
    classification, supported = _canonical_change_classification(
        _change(subject_id="night_waking", change_type="increase"),
        evidence_complete=False,
    )
    assert classification == "uncertain"
    assert supported is False
