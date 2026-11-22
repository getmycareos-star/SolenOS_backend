"""Pure response-contract tests for persisted document extraction state."""

from types import SimpleNamespace

from app.api.document_lifecycle import _response


def test_partial_ocr_hides_stale_review_candidates_but_preserves_accepted_history():
    analysis = SimpleNamespace(
        id="analysis-partial-ocr",
        source_evidence_id="evidence-partial-ocr",
        extraction_status="partial",
        source_text=(
            "[Page 1 OCR]\n"
            "[Page not processed: OCR engine error]"
        ),
        candidates={
            "extraction_method": "local_ocr",
            "document_intelligence": {
                "status": "completed",
                "observations": [
                    {
                        "review_status": "pending_review",
                        "observation": "Stale suggestion from an earlier extraction",
                    },
                    {
                        "review_status": "accepted_by_caregiver",
                        "observation": "Previously accepted care detail",
                    },
                ],
            },
        },
    )

    result = _response(analysis)

    assert result["candidate_count"] == 0
    assert result["review_required"] is False
    assert result["interpretation_blocked"] is True
    assert result["written_to_lcr"] is True
    assert result["unprocessed_page_numbers"] == [1]


def test_complete_ocr_keeps_pending_candidates_available_for_caregiver_review():
    analysis = SimpleNamespace(
        id="analysis-complete-ocr",
        source_evidence_id="evidence-complete-ocr",
        extraction_status="available",
        source_text="[Page 1 OCR]\nCaregiver note: breakfast was mostly uneaten.",
        candidates={
            "document_intelligence": {
                "status": "completed",
                "observations": [
                    {
                        "review_status": "pending_review",
                        "observation": "Breakfast was mostly uneaten.",
                    },
                ],
            },
        },
    )

    result = _response(analysis)

    assert result["candidate_count"] == 1
    assert result["review_required"] is True
    assert result["interpretation_blocked"] is False
    assert result["written_to_lcr"] is False
