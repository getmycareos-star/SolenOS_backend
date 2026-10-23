"""Authenticated document text extraction for the Living Care Record.

Extraction is deliberately pre-cognitive: it returns source text and technical
quality metadata only. It does not diagnose, summarize, or authorize care.
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import PurePath
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

router = APIRouter(tags=["document-extraction"])
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 10
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | IMAGE_EXTENSIONS | {".pdf"}
PDF_MAGIC = b"%PDF-"
LOW_CONFIDENCE_THRESHOLD = 0.65


def _ocr_image(image: Any) -> tuple[str, float | None]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:
        raise RuntimeError("OCR dependencies are not installed on this service.") from exc

    data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 3")
    words: list[str] = []
    confidences: list[float] = []
    for word, raw_confidence in zip(data.get("text", []), data.get("conf", [])):
        word = str(word).strip()
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if word and confidence >= 0:
            words.append(word)
            confidences.append(confidence / 100.0)
    if not words:
        return "", None
    return " ".join(words), sum(confidences) / len(confidences)


def extract_document_content(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    """Extract a bounded document without persisting raw binary content."""
    if not data:
        raise ValueError("The selected file is empty.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("The selected file exceeds the 10 MiB limit.")

    extension = PurePath(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Supported formats are PDF, common image files, and plain text files.")
    digest = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    confidence: float | None = None
    source = "text"

    if extension in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("This text file is not valid UTF-8 and could not be read safely.") from exc
    elif extension == ".pdf":
        if not data.startswith(PDF_MAGIC):
            raise ValueError("The file extension says PDF, but its content is not a valid PDF.")
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("This PDF is encrypted. Export an unlocked copy and try again.")
            pages = reader.pages
            if not pages:
                raise ValueError("This PDF has no readable pages.")
            if len(pages) > MAX_PDF_PAGES:
                warnings.append(f"Only the first {MAX_PDF_PAGES} of {len(pages)} pages were processed.")
            page_text = [(page.extract_text() or "").strip() for page in pages[:MAX_PDF_PAGES]]
            text = "\n\n".join(part for part in page_text if part)
            source = "pdf_text"
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("This PDF could not be opened. The file may be damaged or unsupported.") from exc

        if not text.strip():
            try:
                import fitz
                from PIL import Image
                document = fitz.open(stream=data, filetype="pdf")
                if document.page_count > MAX_PDF_PAGES:
                    warnings.append(f"Only the first {MAX_PDF_PAGES} of {document.page_count} scanned pages were processed.")
                ocr_parts: list[str] = []
                page_confidences: list[float] = []
                for page in document[:MAX_PDF_PAGES]:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    image_obj = Image.open(io.BytesIO(pixmap.tobytes("png")))
                    page_text_value, page_confidence = _ocr_image(image_obj)
                    if page_text_value:
                        ocr_parts.append(page_text_value)
                    if page_confidence is not None:
                        page_confidences.append(page_confidence)
                document.close()
                text = "\n\n".join(ocr_parts)
                confidence = sum(page_confidences) / len(page_confidences) if page_confidences else None
                source = "pdf_ocr"
            except RuntimeError as exc:
                raise ValueError("This PDF appears to be scanned, but OCR is unavailable on this service.") from exc
            except Exception as exc:
                raise ValueError("Text could not be extracted from this scanned PDF.") from exc
    else:
        try:
            from PIL import Image
            image_obj = Image.open(io.BytesIO(data))
            image_obj.verify()
            image_obj = Image.open(io.BytesIO(data))
            text, confidence = _ocr_image(image_obj)
            source = "image_ocr"
        except RuntimeError as exc:
            raise ValueError("OCR is unavailable on this service. Try a text-based PDF or text file.") from exc
        except Exception as exc:
            raise ValueError("This image could not be read. Try a clearer JPG, PNG, or TIFF image.") from exc

    text = re.sub(r"\x00", "", text).strip()
    if not text:
        raise ValueError("No readable text was found. The document may be blank, damaged, or too unclear.")
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        warnings.append("OCR confidence is low. Check the extracted text against the original before relying on it.")
    if len(text) > 500_000:
        text = text[:500_000]
        warnings.append("Extracted text was truncated at 500,000 characters.")
    return {
        "ok": True,
        "document": {
            "id": f"doc_{digest[:24]}",
            "name": PurePath(filename).name[:255],
            "mime_type": content_type or "application/octet-stream",
            "extracted_text": text,
            "extracted_text_preview": text[:600],
            "extracted_char_count": len(text),
            "ocr_confidence": confidence,
            "extraction_source": source,
            "extraction_warnings": warnings,
            "sha256": digest,
        },
    }


@router.post("/documents/extract")
async def extract_document(request: Request, file: UploadFile = File(...)):
    # Session-scope middleware sets these only after validating the bearer token.
    if not getattr(request.state, "scoped_person_id", None) or not getattr(request.state, "scoped_caregiver_id", None):
        raise HTTPException(status_code=401, detail="An authenticated care-record session is required.")
    try:
        data = await file.read(MAX_DOCUMENT_BYTES + 1)
        result = extract_document_content(file.filename or "upload", file.content_type or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await file.close()
    return result
