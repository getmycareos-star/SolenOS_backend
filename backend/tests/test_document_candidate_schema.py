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


def test_ocr_quality_metadata_is_not_sent_as_document_source_text():
    source = (
        "[Page 3 OCR]\n"
        "[OCR quality: mean_token_confidence=62; low_confidence_tokens=4; token_count=18; metric=tesseract_engine_heuristic_not_probability]\n"
        "Patient walked 20 metres with a frame."
    )
    assert split_document_pages(source) == [(3, "Patient walked 20 metres with a frame.")]
    assert page_for_excerpt(source, "Patient walked 20 metres with a frame.") == 3
