"""Fixture-based tests for local OCR and native document extraction."""
import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont
from docx import Document

from app.services.document_extraction import extract_document

OCR_AVAILABLE = shutil.which("tesseract") is not None


def _font(size=54):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _scan(text: str, rotate: int = 0, low_quality: bool = False) -> Image.Image:
    image = Image.new("RGB", (2200, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.text((90, 150), text, fill="black", font=_font())
    if low_quality:
        image = image.resize((330, 105)).resize((2200, 700))
    if rotate:
        image = image.rotate(rotate, expand=True, fillcolor="white")
    return image


def _bytes(image: Image.Image, format="PNG") -> bytes:
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()

def _native_pdf(text: str) -> bytes:
    stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(output)


def test_extract_utf8_text_preserves_content():
    result = extract_document("care.txt", "text/plain", b"Caregiver report: breakfast was missed.")
    assert result["text"] == "Caregiver report: breakfast was missed."
    assert result["method"] == "server_utf8_text"
    assert result["pages"][0]["quality"] == "review"


def test_extract_native_pdf_embedded_text_without_ocr():
    result = extract_document("native.pdf", "application/pdf", _native_pdf("Native PDF text is available."))
    assert "Native PDF text is available." in result["text"]
    assert result["pages"][0]["method"] == "native_pdf_text"


def test_extract_docx_paragraphs_and_tables():
    document = Document()
    document.add_paragraph("Care visit summary")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Date"
    table.cell(0, 1).text = "Monday"
    output = io.BytesIO()
    document.save(output)
    result = extract_document(
        "visit.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        output.getvalue(),
    )
    assert "Care visit summary" in result["text"]
    assert "Date | Monday" in result["text"]


@pytest.mark.skipif(not OCR_AVAILABLE, reason="Tesseract is installed in the document OCR CI job")
def test_printed_image_runs_real_ocr_and_retains_word_coordinates():
    result = extract_document("care-note.png", "image/png", _bytes(_scan("Breakfast was mostly uneaten today.")))
    assert "breakfast" in result["text"].lower()
    page = result["pages"][0]
    assert page["method"] == "tesseract_ocr"
    assert page["word_count"] >= 4
    assert page["mean_word_confidence"] is not None
    assert page["word_boxes"]
    assert page["handwriting_warning"]


@pytest.mark.skipif(not OCR_AVAILABLE, reason="Tesseract is installed in the document OCR CI job")
def test_rotated_image_is_orientation_corrected():
    result = extract_document(
        "rotated.png", "image/png",
        _bytes(_scan("Caregiver recorded a morning appointment.", rotate=90)),
    )
    assert "caregiver" in result["text"].lower()


@pytest.mark.skipif(not OCR_AVAILABLE, reason="Tesseract is installed in the document OCR CI job")
def test_scanned_multipage_pdf_ocr_processes_each_page():
    first = _scan("Page one says breakfast was served.")
    second = _scan("Page two says follow up was requested.")
    output = io.BytesIO()
    first.save(output, format="PDF", save_all=True, append_images=[second], resolution=150)
    result = extract_document("scanned.pdf", "application/pdf", output.getvalue())
    assert len(result["pages"]) == 2
    assert "[Page 1]" in result["text"]
    assert "[Page 2]" in result["text"]
    assert "breakfast" in result["text"].lower()
    assert "follow" in result["text"].lower()


@pytest.mark.skipif(not OCR_AVAILABLE, reason="Tesseract is installed in the document OCR CI job")
def test_blank_image_is_explicitly_unreadable_not_a_care_fact():
    result = extract_document("blank.png", "image/png", _bytes(Image.new("RGB", (1800, 700), "white")))
    assert result["text"] == ""
    assert result["pages"][0]["quality"] == "unreadable"


def test_unsupported_file_type_fails_closed():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        extract_document("care.bin", "application/octet-stream", b"not a document")
    assert exc.value.status_code == 415
