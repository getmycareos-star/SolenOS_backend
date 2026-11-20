import pytest

from app.services.document_candidate_schema import canonical_candidate, statement_context_markers


@pytest.mark.parametrize(
    ("quote", "negation", "temporal"),
    [
        ("No longer requires assistance.", "explicitly_negated", "changed_over_time"),
        ("Previously diagnosed with dementia.", "not_explicitly_marked", "historical"),
        ("Medication discontinued.", "not_explicitly_marked", "changed_over_time"),
        ("Medication dosage changed from 5 mg to 10 mg.", "not_explicitly_marked", "changed_over_time"),
        ("The patient denies experiencing pain.", "explicitly_negated", "not_established"),
        ("Condition resolved last month.", "not_explicitly_marked", "changed_over_time"),
        ("Current medication differs from discharge medication.", "not_explicitly_marked", "changed_over_time"),
        ("The patient is not experiencing pain.", "explicitly_negated", "not_established"),
        ("The patient is not taking the medication.", "explicitly_negated", "not_established"),
    ],
)
def test_adversarial_negation_and_temporal_language_is_preserved(quote, negation, temporal):
    markers = statement_context_markers(quote)
    assert markers["negation_status"] == negation
    assert markers["temporal_context"] == temporal


def test_document_candidate_preserves_patient_denial_and_source_quote():
    quote = "The patient denies experiencing pain."
    candidate = canonical_candidate(
        {"subject": "pain", "observation": quote, "source_quote": quote},
        document_id="adversarial-document",
        source_text=f"[Page 2]\n{quote}",
    )
    assert candidate["source_excerpt"] == quote
    assert candidate["page_number"] == 2
    assert candidate["negation_status"] == "explicitly_negated"
    assert candidate["source_attribution"] == "patient_reported"
    assert candidate["temporal_context"] == "not_established"
