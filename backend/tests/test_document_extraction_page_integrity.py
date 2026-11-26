"""Unit coverage for page-number integrity in document extraction metadata."""

import hashlib
import io
from types import SimpleNamespace

from pypdf import PdfWriter

from app.api.document_lifecycle import _extraction_metadata


def _upload(page_count: int = 3):
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    output = io.BytesIO()
    writer.write(output)
    content = output.getvalue()
    return SimpleNamespace(
        filename="care-plan.pdf",
        mime_type="application/pdf",
        content=content,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_backend="postgres",
    )


def test_sparse_page_markers_are_not_reported_as_a_complete_document():
    status, metadata = _extraction_metadata(
        _upload(),
        "[Page 1 OCR]\nMedication review scheduled.\n[Page 3 OCR]\nFollow-up requested.",
        "pdf_text",
    )

    assert status == "partial"
    assert metadata["page_count"] == 3
    assert metadata["page_count_source"] == "original_binary"
    assert metadata["processed_page_numbers"] == [1, 3]
    assert metadata["unprocessed_page_numbers"] == [2]




def test_long_document_text_within_expanded_capacity_is_preserved():
    from app.api.document_lifecycle import MAX_EXTRACTED_CHARS, _bounded_extraction

    long_text = "clinical detail " * 10_000
    assert 100_000 < len(long_text) <= MAX_EXTRACTED_CHARS
    persisted, method = _bounded_extraction(long_text, "server_pdf_text")
    assert persisted == long_text
    assert method == "server_pdf_text"


def test_native_pdf_retry_preserves_saved_pages_and_extracts_only_missing_pages(monkeypatch):
    from types import SimpleNamespace

    from app.api import document_lifecycle

    class NativePage:
        def __init__(self, text):
            self.text = text

        def extract_text(self, extraction_mode=None):
            return self.text

    class NativeReader:
        pages = [
            NativePage("Current page one text is intentionally different from the saved first page."),
            NativePage("Current page two text is intentionally different from the saved second page."),
            NativePage("Newly extracted page three has enough readable native text."),
            NativePage("Newly extracted page four has enough readable native text."),
        ]

    monkeypatch.setattr(document_lifecycle, "PdfReader", lambda *args, **kwargs: NativeReader())
    upload = SimpleNamespace(filename="resume.pdf", mime_type="application/pdf")
    previous = "[Page 1]\nSaved first page.\n[Page 2]\nSaved second page."

    extracted, method = document_lifecycle._extract(
        upload,
        b"test-pdf",
        retry_only_pages={3, 4},
        previous_source_text=previous,
    )

    assert method == "server_pdf_text"
    assert "[Page 1]\nSaved first page." in extracted
    assert "[Page 2]\nSaved second page." in extracted
    assert "Newly extracted page three" in extracted
    assert "Newly extracted page four" in extracted
    assert "Current page one" not in extracted
    assert "Current page two" not in extracted


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
        _upload(2),
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
        source_text="[Page 1 OCR]\nFirst page text.\n[Page 3 OCR]\nThird page text.",
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
        source_text="[Page 1 OCR]\nFirst page text.\n[Page 3 OCR]\nThird page text.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "page_count": 3,
            "unprocessed_page_numbers": [2],
        },
    )

    try:
        _require_complete_extraction(analysis, _upload(3))
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "2" in exc.detail
    else:
        raise AssertionError("Document interpretation must remain blocked while page 2 is missing.")


def test_standalone_scan_preserves_original_page_label_without_inventing_missing_pages():
    upload = _upload()
    upload.filename = "scan.jpg"
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
        source_text="[Page 1 OCR]\nFirst page text.\n[Page 3 OCR]\nThird page text.",
        extraction_status="partial",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "care-plan.pdf",
            "document_type": "general_care_document",
            "page_count": 3,
            "unprocessed_page_numbers": [2],
            "document_intelligence": {
                "status": "available",
                "observations": [{
                    "subject": "Medication",
                    "observation": "Old candidate",
                    "review_status": "accepted_by_caregiver",
                }],
            },
        },
    )

    response = _response(analysis, _upload(3))

    assert response["page_count"] == 3
    assert response["extraction_processed_page_numbers"] == [1, 3]
    assert response["extraction_unprocessed_page_numbers"] == [2]
    assert response["observations"] == []
    assert response["interpretation_blocked"] is True


def test_document_review_response_uses_persisted_page_numbers_for_standalone_scan():
    from types import SimpleNamespace

    from app.api.document_intelligence import _response

    analysis = SimpleNamespace(
        id="scan-analysis",
        source_evidence_id="scan-evidence",
        source_text="[Page 3 OCR]\nMedication review scheduled.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "page_count": 1,
            "processed_page_numbers": [3],
            "unprocessed_page_numbers": [],
        },
    )

    response = _response(analysis, _upload(3))
    assert response["page_count"] == 1
    assert response["extraction_processed_page_count"] == 1
    assert response["extraction_processed_page_numbers"] == [3]
    assert response["extraction_unprocessed_page_numbers"] == []


def test_document_review_response_never_counts_a_failed_page_as_processed():
    from types import SimpleNamespace

    from app.api.document_intelligence import _response

    analysis = SimpleNamespace(
        id="partial-analysis",
        source_evidence_id="partial-evidence",
        source_text="[Page 1 OCR]\nFirst page.\n[Page 2 OCR]\n[Page not processed: OCR engine error]\n[Page 3 OCR]\nThird page.",
        extraction_status="partial",
        candidates={
            "kind": "document_extraction_v1",
            "page_count": 3,
            "processed_page_numbers": [1, 2, 3],
            "unprocessed_page_numbers": [2],
        },
    )

    response = _response(analysis, _upload(3))
    assert response["extraction_processed_page_numbers"] == [1, 3]
    assert response["extraction_unprocessed_page_numbers"] == [2]
    assert response["interpretation_blocked"] is True


def test_pdf_filename_still_enforces_missing_pages_when_mime_type_is_generic():
    upload = _upload()
    upload.mime_type = "application/octet-stream"
    status, metadata = _extraction_metadata(
        upload,
        "[Page 1 OCR]\nFirst page.\n[Page 3 OCR]\nThird page.",
        "pdf_ocr",
    )

    assert status == "partial"
    assert metadata["page_count"] == 3
    assert metadata["processed_page_numbers"] == [1, 3]
    assert metadata["unprocessed_page_numbers"] == [2]


def test_legacy_pdf_without_page_manifest_detects_missing_source_page():
    from types import SimpleNamespace

    from app.api.document_intelligence import _persisted_unprocessed_pages

    analysis = SimpleNamespace(
        source_text="[Page 1 OCR]\nFirst page text.\n[Page 3 OCR]\nThird page text.",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
        },
    )

    assert _persisted_unprocessed_pages(analysis) == [2]


def test_legacy_pdf_page_gap_blocks_interpretation_without_saved_gap_metadata():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.document_intelligence import _require_complete_extraction

    analysis = SimpleNamespace(
        source_text="[Page 1 OCR]\nFirst page text.\n[Page 3 OCR]\nThird page text.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
        },
    )

    try:
        _require_complete_extraction(analysis, _upload(3))
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "2" in exc.detail
    else:
        raise AssertionError("A legacy PDF with an unrecorded missing page must not be interpreted.")


def test_legacy_pdf_page_gap_blocks_old_candidates_in_review_response():
    from types import SimpleNamespace

    from app.api.document_intelligence import _response

    analysis = SimpleNamespace(
        id="legacy-gap-analysis",
        source_evidence_id="legacy-gap-evidence",
        source_text="[Page 1 OCR]\nFirst page text.\n[Page 3 OCR]\nThird page text.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "document_intelligence": {
                "status": "available",
                "observations": [{"subject": "Medication", "observation": "Old candidate"}],
            },
        },
    )

    response = _response(analysis, _upload(3))

    assert response["extraction_processed_page_numbers"] == [1, 3]
    assert response["extraction_processed_page_count"] == 2
    assert response["extraction_unprocessed_page_numbers"] == [2]
    assert response["interpretation_blocked"] is True
    assert response["observations"] == []



def test_legacy_pdf_page_count_is_reconciled_to_the_preserved_original():
    from types import SimpleNamespace

    from app.api.document_intelligence import _response

    analysis = SimpleNamespace(
        id="legacy-wrong-page-total",
        source_evidence_id="legacy-wrong-page-total-source",
        source_text="[Page 1 OCR]\nFirst page.\n[Page 2 OCR]\nSecond page.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 2,
            "unprocessed_page_numbers": [],
            "document_intelligence": {
                "status": "available",
                "observations": [{"subject": "Care", "observation": "Old candidate"}],
            },
        },
    )

    response = _response(analysis, _upload(3))

    assert response["page_count"] == 3
    assert response["total_page_count"] == 3
    assert response["page_count_verified"] is True
    assert response["extraction_unprocessed_page_numbers"] == [3]
    assert response["interpretation_blocked"] is True
    assert response["observations"] == []


def test_legacy_pdf_without_source_bytes_is_not_assumed_complete():
    from types import SimpleNamespace

    from app.api.document_intelligence import _response

    analysis = SimpleNamespace(
        id="legacy-pdf-without-binary",
        source_evidence_id="legacy-pdf-without-binary-source",
        source_text="[Page 1 OCR]\nOnly extracted page.",
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 1,
            "document_intelligence": {
                "status": "available",
                "observations": [{"subject": "Care", "observation": "Old candidate"}],
            },
        },
    )

    response = _response(analysis)

    assert response["page_count_verified"] is False
    assert response["interpretation_blocked"] is True
    assert response["observations"] == []


def test_lifecycle_response_blocks_legacy_pdf_page_gaps_and_hides_old_candidates():
    from types import SimpleNamespace

    from app.api.document_lifecycle import _response

    analysis = SimpleNamespace(
        id="legacy-lifecycle-gap-analysis",
        source_evidence_id="legacy-lifecycle-gap-evidence",
        source_text="[Page 1 OCR]\\nFirst page text.\\n[Page 3 OCR]\\nThird page text.",
        extraction_status="available",
        candidates={
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "unprocessed_page_numbers": [],
            "document_intelligence": {
                "status": "available",
                "observations": [{"subject": "Medication", "observation": "Old candidate"}],
            },
        },
    )

    response = _response(analysis)

    assert response["page_count"] == 3
    assert response["page_count_verified"] is False
    assert response["processed_page_numbers"] == [1, 3]
    assert response["unprocessed_page_numbers"] == [2]
    assert response["interpretation_blocked"] is True
    assert response["candidate_count"] == 0
    assert response["written_to_lcr"] is False



def test_lifecycle_response_trusts_only_page_totals_marked_as_original_binary():
    from types import SimpleNamespace

    from app.api.document_lifecycle import _response

    analysis = SimpleNamespace(
        id="verified-pdf-page-total",
        source_evidence_id="verified-pdf-source",
        source_text="[Page 1 OCR]\nFirst page.\n[Page 2 OCR]\nSecond page.",
        extraction_status="available",
        candidates={
            "filename": "care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 2,
            "page_count_source": "original_binary",
            "unprocessed_page_numbers": [],
        },
    )

    response = _response(analysis)

    assert response["page_count"] == 2
    assert response["page_count_verified"] is True
    assert response["interpretation_blocked"] is False


def test_lifecycle_response_does_not_invent_missing_pages_for_standalone_scan():
    from types import SimpleNamespace

    from app.api.document_lifecycle import _response

    analysis = SimpleNamespace(
        id="standalone-scan-analysis",
        source_evidence_id="standalone-scan-evidence",
        source_text="[Page 3 OCR]\\nMedication review scheduled.",
        extraction_status="available",
        candidates={
            "filename": "scan.jpg",
            "mime_type": "image/jpeg",
            "page_count": 1,
            "processed_page_numbers": [3],
            "unprocessed_page_numbers": [],
        },
    )

    response = _response(analysis)

    assert response["page_count"] == 1
    assert response["processed_page_numbers"] == [3]
    assert response["unprocessed_page_numbers"] == []
    assert response["interpretation_blocked"] is False


def test_lifecycle_response_ignores_malformed_persisted_page_numbers():
    from types import SimpleNamespace

    from app.api.document_lifecycle import _response

    analysis = SimpleNamespace(
        id="malformed-page-metadata-analysis",
        source_evidence_id="malformed-page-metadata-evidence",
        source_text="[Page 1 OCR]\\nFirst page.\\n[Page 3 OCR]\\nThird page.",
        extraction_status="available",
        candidates={
            "filename": "care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "unprocessed_page_numbers": [None, "2", True, {"page": 2}],
        },
    )

    response = _response(analysis)

    assert response["unprocessed_page_numbers"] == [2]
    assert response["interpretation_blocked"] is True
