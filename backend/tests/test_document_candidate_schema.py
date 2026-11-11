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
        {"subject": "Metformin", "observation": first["extracted_value"], "field_type": "medication", "warnings": first["warnings"]},
        {"subject": "Metformin", "observation": second["extracted_value"], "field_type": "medication", "warnings": second["warnings"]},
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
