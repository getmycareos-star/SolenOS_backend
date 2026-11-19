from datetime import datetime, timezone

from app.schemas.care_continuity import CareAssertionCreate
from app.api.document_intelligence import _explicit_document_event_datetime
from datetime import datetime, timezone
from app.services.document_candidate_schema import (
    apply_medication_conflict_warnings,
    canonical_candidate,
    chunk_document_pages,
    page_for_excerpt,
    split_document_pages,
    unreadable_page_numbers,
)


def test_page_boundaries_are_preserved_and_quotes_resolve_to_exact_page():
    source = "[Page 1]\nDischarge diagnosis: pneumonia\n\n[Page 2 OCR]\nFollow up in 14 days."
    assert page_for_excerpt(source, "Discharge diagnosis: pneumonia") == 1
    assert page_for_excerpt(source, "Follow up in 14 days.") == 2
    assert split_document_pages(source) == [
        (1, "Discharge diagnosis: pneumonia"),
        (2, "Follow up in 14 days."),
    ]


def test_candidate_has_canonical_provenance_and_separate_medication_fields():
    source = "[Page 4]\nTake metformin 500 mg twice daily."
    item = {
        "subject": "metformin",
        "observation": "Take metformin 500 mg twice daily.",
        "source_quote": "Take metformin 500 mg twice daily.",
    }
    candidate = canonical_candidate(
        item,
        document_id="evidence-1",
        source_text=source,
        recorded_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    assert candidate["document_id"] == "evidence-1"
    assert candidate["page_number"] == 4
    assert candidate["source_span_unit"] == "unicode_code_points"
    assert source[candidate["source_span_start"]:candidate["source_span_end"]] == candidate["source_excerpt"]
    assert candidate["field_type"] == "medication"
    assert candidate["medication_details"]["strength"] == "500 mg"
    assert candidate["medication_details"]["dosage_instructions"] == "Take metformin 500 mg twice daily"
    assert candidate["event_date"] is None
    assert candidate["review_status"] == "Pending"


def test_missing_dosage_and_conflicting_instructions_are_flagged_not_inferred():
    source = "[Page 1]\nMetformin 500 mg.\n\n[Page 2]\nMetformin 1000 mg twice daily."
    first = canonical_candidate(
        {"subject": "Metformin", "observation": "Metformin 500 mg.", "source_quote": "Metformin 500 mg."},
        document_id="doc-1",
        source_text=source,
    )
    second = canonical_candidate(
        {"subject": "Metformin", "observation": "Metformin 1000 mg twice daily.", "source_quote": "Metformin 1000 mg twice daily."},
        document_id="doc-1",
        source_text=source,
    )
    assert first["medication_details"]["dosage_instructions"] is None
    assert any("dosage remains missing" in warning for warning in first["warnings"])
    candidates = [
        {"subject": "Metformin 500 mg", "observation": first["extracted_value"], "source_quote": first["source_excerpt"], "field_type": "medication", "warnings": first["warnings"]},
        {"subject": "Metformin", "observation": second["extracted_value"], "source_quote": second["source_excerpt"], "field_type": "medication", "warnings": second["warnings"]},
    ]
    apply_medication_conflict_warnings(candidates)
    assert all(any("Conflicting medication instructions" in warning for warning in row["warnings"]) for row in candidates)


def test_invalid_calendar_date_is_left_missing_and_warned():
    excerpt = "Appointment on 31/02/2026."
    candidate = canonical_candidate(
        {"subject": "appointment", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-2",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["event_date"] is None
    assert any("Date format is ambiguous or invalid" in warning for warning in candidate["warnings"])



def test_outdated_instructions_and_multiple_dates_are_flagged():
    excerpt = "Previous instruction: take warfarin 2 mg daily until 2025-01-10; new instruction recorded 2026-02-11."
    candidate = canonical_candidate(
        {"subject": "warfarin", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-3",
        source_text=f"[Page 2]\n{excerpt}",
    )
    assert candidate["event_date"] is None
    assert any("Multiple dates" in warning for warning in candidate["warnings"])
    assert any("outdated or superseded" in warning for warning in candidate["warnings"])



def test_unreadable_pages_are_reported_without_becoming_candidates():
    source = (
        "[Page 1]\nDischarge diagnosis: pneumonia\n\n"
        "[Page 2 OCR]\n[No readable text detected on this page]\n\n"
        "[Page 3 OCR]\nFollow up in 14 days."
    )
    assert unreadable_page_numbers(source) == [2]
    assert split_document_pages(source) == [
        (1, "Discharge diagnosis: pneumonia"),
        (3, "Follow up in 14 days."),
    ]



def test_long_page_is_chunked_without_dropping_source_text():
    page_text = ("A" * 6000) + "\n" + ("B" * 6000) + "\n" + ("C" * 6000)
    chunks = chunk_document_pages([(7, page_text)], max_chars=8000)
    assert len(chunks) == 3
    assert all(page_number == 7 for page_number, _ in chunks)
    assert all(len(chunk) <= 8000 for _, chunk in chunks)
    assert "".join(chunks[index][1] for index in range(len(chunks))) == page_text



def test_recorded_date_is_not_mislabelled_as_event_date():
    excerpt = "Recorded on 2026-10-15. Patient slept poorly."
    candidate = canonical_candidate(
        {"subject": "sleep", "observation": "Patient slept poorly.", "source_quote": excerpt},
        document_id="doc-recorded-date",
        source_text=f"[Page 1]\n{excerpt}",
        recorded_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    assert candidate["event_date"] is None
    assert candidate["recorded_date"] == "2026-10-15"
    assert candidate["extracted_at"] == "2026-11-11T00:00:00+00:00"
    assert any("document recording date" in warning for warning in candidate["warnings"])



def test_twelve_page_discharge_summary_keeps_each_page_identity():
    source = "\n\n".join(
        f"[Page {number}]\nDischarge summary page {number}. Follow-up instruction {number}."
        for number in range(1, 13)
    )
    pages = split_document_pages(source)
    chunks = chunk_document_pages(pages)
    assert len(pages) == 12
    assert [page_number for page_number, _ in pages] == list(range(1, 13))
    assert [page_number for page_number, _ in chunks] == list(range(1, 13))
    assert all(f"page {number}" in text for number, text in chunks)



def test_month_name_dates_are_flagged_and_mixed_date_conflicts_remain_unresolved():
    excerpt = "Admission date: 2026-10-12. Discharge date: 14 October 2026."
    candidate = canonical_candidate(
        {"subject": "admission", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-natural-date",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["event_date"] is None
    assert any("Multiple dates" in warning for warning in candidate["warnings"])



def test_event_date_is_not_confused_with_separately_labelled_recorded_date():
    excerpt = "Medication instruction: Metformin 500 mg once daily. Event date 2026-10-12. Recorded on 2026-10-13."
    candidate = canonical_candidate(
        {"subject": "Metformin", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-event-recorded-date",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["event_date"] == "2026-10-12"
    assert candidate["recorded_date"] == "2026-10-13"
    assert not any("Multiple dates" in warning for warning in candidate["warnings"])



def test_unambiguous_month_name_event_date_is_normalized():
    excerpt = "Discharged on 14 October 2026."
    candidate = canonical_candidate(
        {"subject": "discharge", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-month-name-date",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["event_date"] == "2026-10-14"
    assert not any("Date format is ambiguous or invalid" in warning for warning in candidate["warnings"])



def test_medication_strength_and_dosage_are_resolved_from_matching_table_row():
    excerpt = "Metformin 500 mg once daily\nAmlodipine 5 mg twice daily"
    candidate = canonical_candidate(
        {
            "subject": "Amlodipine",
            "observation": "Amlodipine 5 mg twice daily",
            "source_quote": excerpt,
        },
        document_id="doc-medication-table",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["medication_details"] == {
        "strength": "5 mg",
        "dosage_instructions": "Amlodipine 5 mg twice daily",
    }
    assert not any("Multiple medication strengths" in warning for warning in candidate["warnings"])


def test_unmatched_multi_strength_medication_excerpt_does_not_guess_strength():
    excerpt = "Metformin 500 mg once daily\nAmlodipine 5 mg twice daily"
    candidate = canonical_candidate(
        {
            "subject": "Medication list",
            "observation": "Medication list includes two entries.",
            "source_quote": excerpt,
        },
        document_id="doc-ambiguous-medication-table",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["medication_details"]["strength"] is None
    assert any("Multiple medication strengths" in warning for warning in candidate["warnings"])



def test_recorded_date_before_event_date_does_not_hide_event_date():
    excerpt = "Recorded on 2026-10-13. Event date 2026-10-12."
    candidate = canonical_candidate(
        {"subject": "care event", "observation": "Event date 2026-10-12.", "source_quote": excerpt},
        document_id="doc-recorded-before-event",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["event_date"] == "2026-10-12"
    assert candidate["recorded_date"] == "2026-10-13"
    assert not any("Multiple dates" in warning for warning in candidate["warnings"])



def test_natural_recorded_date_is_normalized_without_becoming_event_date():
    excerpt = "Recorded on 15 October 2026. Patient slept poorly."
    candidate = canonical_candidate(
        {"subject": "sleep", "observation": "Patient slept poorly.", "source_quote": excerpt},
        document_id="doc-natural-recorded-date",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["event_date"] is None
    assert candidate["recorded_date"] == "2026-10-15"
    assert any("document recording date" in warning for warning in candidate["warnings"])



def test_repeated_source_quote_has_no_false_unique_span():
    excerpt = "Take metformin 500 mg twice daily."
    source = f"[Page 1]\n{excerpt}\n\n[Page 2]\n{excerpt}"
    candidate = canonical_candidate(
        {"subject": "metformin", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-repeated-quote",
        source_text=source,
    )
    assert candidate["source_span_start"] is None
    assert candidate["source_span_end"] is None
    assert any("unique source span is unavailable" in warning for warning in candidate["warnings"])



def test_explicit_negation_and_reporter_attribution_survive_candidate_normalization():
    excerpt = "The patient denies pain."
    candidate = canonical_candidate(
        {"subject": "pain", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-negation-attribution",
        source_text=f"[Page 2]\n{excerpt}",
    )
    assert candidate["source_attribution"] == "patient_reported"
    assert candidate["negation_status"] == "explicitly_negated"
    assert candidate["source_excerpt"] == excerpt
    assert any("explicit negative wording" in warning for warning in candidate["warnings"])


def test_tentative_statement_is_not_normalized_as_an_established_finding():
    excerpt = "Possible pneumonia; the clinician suspects an infection."
    candidate = canonical_candidate(
        {"subject": "respiratory condition", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-tentative",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["modality"] == "tentative"
    assert any("tentative" in warning for warning in candidate["warnings"])


def test_conditional_instruction_retains_its_condition():
    excerpt = "If fever develops, contact the care team."
    candidate = canonical_candidate(
        {"subject": "follow-up instruction", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-conditional",
        source_text=f"[Page 3]\n{excerpt}",
    )
    assert candidate["conditionality"] == "conditional"
    assert any("conditional" in warning for warning in candidate["warnings"])


def test_historical_statement_is_not_mislabeled_as_current():
    excerpt = "History of stroke documented in the prior assessment."
    candidate = canonical_candidate(
        {"subject": "stroke", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-historical",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["temporal_context"] == "historical"
    assert any("historical" in warning for warning in candidate["warnings"])


def test_recommendation_does_not_imply_completion_or_outcome():
    excerpt = "Follow-up with cardiology is recommended in four weeks."
    candidate = canonical_candidate(
        {"subject": "cardiology follow-up", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-recommendation",
        source_text=f"[Page 5]\n{excerpt}",
    )
    assert candidate["statement_kind"] == "recommendation"
    assert any("does not establish" in warning for warning in candidate["warnings"])


def test_professional_attribution_is_preserved_without_claiming_independent_verification():
    excerpt = "The nurse observed difficulty walking."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-professional-attribution",
        source_text=f"[Page 4]\n{excerpt}",
    )
    assert candidate["source_attribution"] == "professional_observed"
    assert candidate["review_status"] == "Pending"
    assert candidate["confidence"] is None


def test_unmarked_statement_does_not_get_false_positive_certainty_or_attribution():
    excerpt = "The person walked to the kitchen."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-unmarked",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["source_attribution"] == "not_stated"
    assert candidate["negation_status"] == "not_explicitly_marked"
    assert candidate["modality"] == "not_explicitly_marked"
    assert candidate["conditionality"] == "not_explicitly_marked"
    assert candidate["temporal_context"] == "not_established"
    assert candidate["statement_kind"] == "not_detected"


def test_generic_assertion_input_cannot_forge_server_derived_document_context():
    payload = CareAssertionCreate(
        person_id="person-1",
        subject_type="person",
        subject_id="person-1",
        field="mobility",
        asserted_value="Walks with assistance",
        source_label="Caregiver report",
        source_text="Patient reports walking with assistance.",
        evidence_ids=["evidence-1"],
        source_context={
            "authority": "clinically_verified",
            "source_attribution": "professional_observed",
        },
    )
    assert "source_context" not in payload.model_dump()


def test_definite_inability_is_not_mislabeled_as_tentative_language():
    excerpt = "The patient could not walk without assistance."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-definite-inability",
        source_text=f"[Page 2]\n{excerpt}",
    )
    assert candidate["negation_status"] == "not_explicitly_marked", "inability is a positive functional limitation, not a negated care observation"
    assert candidate["modality"] == "not_explicitly_marked"


def test_temporal_when_clause_is_not_automatically_treated_as_conditional_instruction():
    excerpt = "The patient was assessed when admitted on 2026-11-10."
    candidate = canonical_candidate(
        {"subject": "hospital event", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-temporal-when",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["conditionality"] == "not_explicitly_marked"



def test_only_source_quoted_iso_event_dates_become_time_bound_observation_times():
    candidate = {"event_date": "2026-11-10"}
    observed_at = _explicit_document_event_datetime(
        candidate,
        "The patient was admitted on 2026-11-10.",
    )
    assert observed_at == datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_document_issue_date_is_not_mistaken_for_a_care_event_date():
    candidate = {"event_date": "2026-11-10"}
    assert _explicit_document_event_datetime(
        candidate,
        "Discharge summary dated 2026-11-10.",
    ) is None


def test_model_normalized_date_not_present_in_source_quote_is_not_used_for_delta():
    candidate = {"event_date": "2026-11-10"}
    assert _explicit_document_event_datetime(
        candidate,
        "The patient reports eating half of lunch during the observation period.",
    ) is None

def test_explicit_iso_event_date_is_required_for_time_bound_document_assertion():
    candidate = {"event_date": "2026-11-10"}
    result = _explicit_document_event_datetime(candidate, "Observed on 2026-11-10.")
    assert result == datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_document_issue_date_is_not_used_as_event_date():
    candidate = {"event_date": "2026-11-10"}
    assert _explicit_document_event_datetime(candidate, "Report dated 2026-11-10.") is None


def test_event_date_not_present_in_source_quote_is_not_used():
    candidate = {"event_date": "2026-11-10"}
    assert _explicit_document_event_datetime(candidate, "No explicit event date in this passage.") is None


def test_without_assistance_is_not_misclassified_as_negated():
    excerpt = "The patient walks without assistance."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-without-assistance",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["negation_status"] == "not_explicitly_marked"


def test_explicit_no_longer_statement_retains_negation_and_temporal_context():
    excerpt = "The patient no longer requires assistance with walking."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-no-longer",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["negation_status"] == "explicitly_negated"
    assert candidate["temporal_context"] == "historical"


def test_professional_reported_is_not_promoted_to_direct_observation():
    excerpt = "The nurse reported that the patient had reduced appetite."
    candidate = canonical_candidate(
        {"subject": "nutrition", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-professional-reported",
        source_text=f"[Page 2]\n{excerpt}",
    )
    assert candidate["source_attribution"] == "professional_documented"


def test_direct_professional_observation_is_distinguished_from_documentation():
    excerpt = "The nurse observed unsteady gait during examination."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-professional-observed",
        source_text=f"[Page 2]\n{excerpt}",
    )
    assert candidate["source_attribution"] == "professional_observed"


def test_no_longer_is_a_change_marker_not_historical_only():
    excerpt = "The patient no longer requires assistance with walking."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-changed-state",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert candidate["temporal_context"] == "changed_over_time"


def test_changed_state_warning_requires_comparison_with_earlier_context():
    excerpt = "The patient no longer requires assistance with walking."
    candidate = canonical_candidate(
        {"subject": "mobility", "observation": excerpt, "source_quote": excerpt},
        document_id="doc-changed-state-warning",
        source_text=f"[Page 1]\n{excerpt}",
    )
    assert any("change from an earlier state" in warning for warning in candidate["warnings"])
