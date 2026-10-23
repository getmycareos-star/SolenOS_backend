import pytest

from app.api.document_extraction import MAX_DOCUMENT_BYTES, extract_document_content


def test_extracts_plain_text_with_digest_and_provenance():
    payload = b"Discharge summary: follow-up requested within two weeks."
    result = extract_document_content("discharge.txt", "text/plain", payload)
    document = result["document"]
    assert result["ok"] is True
    assert document["extracted_text"] == payload.decode()
    assert document["extraction_source"] == "text"
    assert document["sha256"]
    assert document["extracted_char_count"] == len(payload.decode())
    assert document["ocr_confidence"] is None


def test_rejects_empty_or_oversized_documents():
    with pytest.raises(ValueError, match="empty"):
        extract_document_content("notes.txt", "text/plain", b"")
    with pytest.raises(ValueError, match="10 MiB"):
        extract_document_content("notes.txt", "text/plain", b"x" * (MAX_DOCUMENT_BYTES + 1))


def test_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="Supported formats"):
        extract_document_content("care.exe", "application/octet-stream", b"data")


def test_rejects_pdf_with_mismatched_signature():
    with pytest.raises(ValueError, match="not a valid PDF"):
        extract_document_content("discharge.pdf", "application/pdf", b"not a PDF")


def test_rejects_invalid_utf8_text_instead_of_silently_corrupting_it():
    with pytest.raises(ValueError, match="valid UTF-8"):
        extract_document_content("notes.txt", "text/plain", b"\xff\xfe\xfd")



def test_extracts_text_from_a_real_pdf():
    import fitz

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Discharge summary follow-up in two weeks")
    payload = pdf.tobytes()
    pdf.close()

    result = extract_document_content("discharge.pdf", "application/pdf", payload)
    assert "Discharge summary" in result["document"]["extracted_text"]
    assert result["document"]["extraction_source"] == "pdf_text"
    assert result["document"]["sha256"]


def test_image_ocr_low_confidence_is_explicit(monkeypatch):
    import io
    from PIL import Image
    import app.api.document_extraction as extraction

    image = Image.new("RGB", (120, 60), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    monkeypatch.setattr(extraction, "_ocr_image", lambda image: ("Discharge follow-up", 0.42))

    result = extract_document_content("scan.png", "image/png", buffer.getvalue())
    document = result["document"]
    assert document["extracted_text"] == "Discharge follow-up"
    assert document["extraction_source"] == "image_ocr"
    assert document["ocr_confidence"] == 0.42
    assert any("confidence is low" in warning for warning in document["extraction_warnings"])


def test_ocr_rejects_images_above_the_pixel_budget(monkeypatch):
    from PIL import Image
    import app.api.document_extraction as extraction

    monkeypatch.setattr(extraction, "MAX_OCR_PIXELS", 4)
    oversized = Image.new("RGB", (3, 2), "white")
    with pytest.raises(ValueError, match="safe OCR processing limit"):
        extraction._ocr_image(oversized)



def test_one_source_sentence_can_yield_multiple_review_candidates_with_exact_spans():
    from app.api.document_extraction import _extract_document_candidates

    source = "Medication: take 5 mg daily and call the clinic for follow-up in two weeks."
    candidates = _extract_document_candidates(source, "evidence-123")
    kinds = {candidate["candidate_type"] for candidate in candidates}

    assert {"medication", "instruction", "follow_up"} <= kinds
    for candidate in candidates:
        start = candidate["source_span_start"]
        end = candidate["source_span_end"]
        assert source[start:end] == candidate["source_quote"]
        assert candidate["source_evidence_id"] == "evidence-123"
        assert candidate["review_status"] == "pending_review"
