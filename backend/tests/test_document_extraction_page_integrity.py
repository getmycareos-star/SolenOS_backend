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
