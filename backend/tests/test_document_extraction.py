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
