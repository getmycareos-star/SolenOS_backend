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
        _require_complete_extraction(analysis)
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

    response = _response(analysis)
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

    response = _response(analysis)
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
        _require_complete_extraction(analysis)
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

    response = _response(analysis)

    assert response["extraction_processed_page_numbers"] == [1, 3]
    assert response["extraction_processed_page_count"] == 2
    assert response["extraction_unprocessed_page_numbers"] == [2]
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
    assert response["processed_page_numbers"] == [1, 3]
    assert response["unprocessed_page_numbers"] == [2]
    assert response["interpretation_blocked"] is True
    assert response["candidate_count"] == 0


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
