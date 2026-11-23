"""Unit coverage for page-number integrity in document extraction metadata."""

from types import SimpleNamespace

from app.api.document_lifecycle import _extraction_metadata


def _upload():
    return SimpleNamespace(
        filename="care-plan.pdf",
        mime_type="application/pdf",
        sha256="a" * 64,
    )


def test_sparse_page_markers_are_not_reported_as_a_complete_document():
    status, metadata = _extraction_metadata(
        _upload(),
        "[Page 1 OCR]\nMedication review scheduled.\n[Page 3 OCR]\nFollow-up requested.",
        "pdf_text",
    )

    assert status == "partial"
    assert metadata["page_count"] == 3
    assert metadata["processed_page_numbers"] == [1, 3]
    assert metadata["unprocessed_page_numbers"] == [2]


def test_page_retry_failure_is_not_counted_as_processed():
    status, metadata = _extraction_metadata(
        _upload(),
        "[Page 1 OCR]\nMedication review scheduled.\n"
        "[Page 2 OCR]\n[Page not processed: OCR engine error]\n"
        "[Page 3 OCR]\nFollow-up requested.",
        "pdf_ocr",
    )

    assert status == "partial"
    assert metadata["page_count"] == 3
    assert metadata["processed_page_numbers"] == [1, 3]
    assert metadata["unprocessed_page_numbers"] == [2]
    assert metadata["page_retry_attempts"] == {"2": 1}


def test_contiguous_document_pages_remain_complete():
    status, metadata = _extraction_metadata(
        _upload(),
        "[Page 1 OCR]\nMedication review scheduled.\n"
        "[Page 2 OCR]\nFollow-up requested.",
        "pdf_text",
    )

    assert status == "available"
    assert metadata["page_count"] == 2
    assert metadata["processed_page_numbers"] == [1, 2]
    assert metadata["unprocessed_page_numbers"] == []


def test_missing_page_metadata_is_preserved_by_document_review_response_helpers():
    from types import SimpleNamespace

    from app.api.document_intelligence import _persisted_unprocessed_pages

    analysis = SimpleNamespace(
        source_text="[Page 1 OCR]\\nFirst page text.\\n[Page 3 OCR]\\nThird page text.",
        candidates={
            "kind": "document_extraction_v1",
            "page_count": 3,
            "unprocessed_page_numbers": [2],
        },
    )

    assert _persisted_unprocessed_pages(analysis) == [2]


def test_document_interpretation_is_blocked_when_persisted_page_metadata_has_a_gap():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.document_intelligence import _require_complete_extraction

    analysis = SimpleNamespace(
        source_text="[Page 1 OCR]\\nFirst page text.\\n[Page 3 OCR]\\nThird page text.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "page_count": 3,
            "unprocessed_page_numbers": [2],
        },
    )

    try:
        _require_complete_extraction(analysis)
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "2" in exc.detail
    else:
        raise AssertionError("Document interpretation must remain blocked while page 2 is missing.")


def test_standalone_scan_preserves_original_page_label_without_inventing_missing_pages():
    upload = _upload()
    upload.mime_type = "image/jpeg"
    status, metadata = _extraction_metadata(
        upload,
        "[Page 3 OCR]\nMedication review scheduled.",
        "pdf_ocr",
    )

    assert status == "available"
    assert metadata["page_count"] == 1
    assert metadata["processed_page_numbers"] == [3]
    assert metadata["unprocessed_page_numbers"] == []


def test_document_review_response_reports_missing_page_and_hides_old_candidates():
    from types import SimpleNamespace

    from app.api.document_intelligence import _response

    analysis = SimpleNamespace(
        id="analysis-page-gap",
        source_evidence_id="evidence-page-gap",
        source_text="[Page 1 OCR]\\nFirst page text.\\n[Page 3 OCR]\\nThird page text.",
        extraction_status="partial",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "care-plan.pdf",
            "document_type": "general_care_document",
            "page_count": 3,
            "unprocessed_page_numbers": [2],
            "document_intelligence": {
                "status": "available",
                "observations": [{"subject": "Medication", "observation": "Old candidate"}],
            },
        },
    )

    response = _response(analysis)

    assert response["page_count"] == 3
    assert response["extraction_processed_page_numbers"] == [1, 3]
    assert response["extraction_unprocessed_page_numbers"] == [2]
    assert response["observations"] == []
    assert response["interpretation_blocked"] is True
