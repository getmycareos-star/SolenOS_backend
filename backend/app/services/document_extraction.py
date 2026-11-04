"""Local, deterministic document text extraction.

The extractor never writes care facts. OCR confidence measures character recognition
quality only; it is not clinical confidence or evidence that an instruction is current.
"""
from __future__ import annotations

import io
import shutil
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
import pypdfium2 as pdfium
import pytesseract
from pytesseract import Output
from docx import Document as DocxDocument

MAX_EXTRACTED_CHARS = 100_000
MAX_PDF_PAGES = 100
MAX_IMAGE_PIXELS = 40_000_000


def _ocr_image(image: Image.Image, page_number: int) -> tuple[str, dict[str, Any]]:
    if shutil.which("tesseract") is None:
        raise HTTPException(status_code=503, detail="The document OCR engine is not installed")
    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=413, detail="Image dimensions exceed the 40-megapixel limit")
    image = ImageOps.autocontrast(ImageOps.grayscale(image))
    try:
        orientation = pytesseract.image_to_osd(image, output_type=Output.DICT)
        rotation = int(orientation.get("rotate", 0) or 0)
        if rotation in {90, 180, 270}:
            image = image.rotate(-rotation, expand=True)
    except Exception:
        # OSD is best-effort; OCR confidence remains explicit.
        pass
    if image.width < 1800:
        scale = min(2.0, 1800 / max(image.width, 1))
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    try:
        data = pytesseract.image_to_data(image, output_type=Output.DICT, config="--psm 3")
    except pytesseract.TesseractNotFoundError as exc:
        raise HTTPException(status_code=503, detail="The document OCR engine is not installed") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The image could not be processed by OCR") from exc

    words: list[str] = []
    confidences: list[float] = []
    word_boxes: list[dict[str, Any]] = []
    for raw_word, raw_conf in zip(data.get("text", []), data.get("conf", [])):
        word = str(raw_word).strip()
        try:
            confidence = float(raw_conf)
        except (TypeError, ValueError):
            confidence = -1
        if word:
            words.append(word)
            if confidence >= 0:
                confidences.append(confidence)
            index = len(words) - 1
            if index < 5000:
                word_boxes.append({
                    "text": word,
                    "left": int(data["left"][index]),
                    "top": int(data["top"][index]),
                    "width": int(data["width"][index]),
                    "height": int(data["height"][index]),
                })
    extracted = " ".join(words).strip()
    mean_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None
    quality = "unreadable" if not extracted else (
        "low" if mean_confidence is None or mean_confidence < 55 else "review"
    )
    return extracted, {
        "page_number": page_number,
        "method": "tesseract_ocr",
        "word_count": len(words),
        "mean_word_confidence": mean_confidence,
        "processed_image_width": image.width,
        "processed_image_height": image.height,
        "word_boxes": word_boxes,
        "quality": quality,
        "handwriting_warning": (
            "Handwriting recognition is not reliable. Caregiver verification is required."
        ),
    }


def extract_document(filename: str, mime_type: str, content: bytes) -> dict[str, Any]:
    """Extract text and page-level metadata from a verified immutable upload."""
    if not content:
        raise HTTPException(status_code=422, detail="The saved document is empty")
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    name = (filename or "").lower()
    pages: list[dict[str, Any]] = []

    if mime == "application/pdf" or name.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="PDFs are limited to 100 pages")
            rendered_pdf = pdfium.PdfDocument(content)
            page_texts: list[str] = []
            for index, page in enumerate(reader.pages):
                native_text = (page.extract_text() or "").strip()
                if native_text:
                    page_texts.append(native_text)
                    pages.append({
                        "page_number": index + 1,
                        "method": "native_pdf_text",
                        "word_count": len(native_text.split()),
                        "quality": "review",
                    })
                else:
                    rendered = rendered_pdf[index].render(scale=2).to_pil()
                    page_text, metadata = _ocr_image(rendered, index + 1)
                    page_texts.append(page_text)
                    pages.append(metadata)
            extracted = "\n\n".join(
                f"[Page {index + 1}]\n{text}"
                for index, text in enumerate(page_texts)
                if text
            ).strip()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved PDF could not be read or rendered") from exc
        if not extracted:
            raise HTTPException(status_code=422, detail="No readable text was found in this PDF")
        return {
            "text": extracted[:MAX_EXTRACTED_CHARS],
            "method": "server_pdf_text_and_ocr",
            "pages": pages,
            "truncated": len(extracted) > MAX_EXTRACTED_CHARS,
        }

    image_types = {
        "image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp",
        "image/heic", "image/heif",
    }
    image_extensions = (
        ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif"
    )
    if mime in image_types or name.endswith(image_extensions):
        try:
            if mime in {"image/heic", "image/heif"} or name.endswith((".heic", ".heif")):
                from pillow_heif import register_heif_opener
                register_heif_opener()
            image = Image.open(io.BytesIO(content))
            image.load()
            extracted, metadata = _ocr_image(image, 1)
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, ImportError) as exc:
            raise HTTPException(status_code=415, detail="The uploaded image format could not be decoded") from exc
        return {
            "text": extracted[:MAX_EXTRACTED_CHARS],
            "method": "server_image_ocr",
            "pages": [metadata],
            "truncated": len(extracted) > MAX_EXTRACTED_CHARS,
        }

    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if mime == docx_mime or name.endswith(".docx"):
        try:
            document = DocxDocument(io.BytesIO(content))
            parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text.strip() for cell in row.cells))
            extracted = "\n".join(parts).strip()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved DOCX document could not be read") from exc
        if not extracted:
            raise HTTPException(status_code=422, detail="The DOCX document contains no readable text")
        pages.append({
            "page_number": None, "method": "python_docx",
            "word_count": len(extracted.split()), "quality": "review",
        })
        return {
            "text": extracted[:MAX_EXTRACTED_CHARS],
            "method": "server_docx_text",
            "pages": pages,
            "truncated": len(extracted) > MAX_EXTRACTED_CHARS,
        }

    text_types = (
        mime.startswith("text/")
        or mime in {"application/json", "application/xml", "application/yaml"}
        or name.endswith((".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log"))
    )
    if text_types:
        try:
            extracted = content.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="Saved text document is not UTF-8") from exc
        if not extracted:
            raise HTTPException(status_code=422, detail="The saved document contains no readable text")
        pages.append({
            "page_number": None, "method": "utf8_text",
            "word_count": len(extracted.split()), "quality": "review",
        })
        return {
            "text": extracted[:MAX_EXTRACTED_CHARS],
            "method": "server_utf8_text",
            "pages": pages,
            "truncated": len(extracted) > MAX_EXTRACTED_CHARS,
        }

    raise HTTPException(
        status_code=415,
        detail=(
            "Supported formats: JPEG, PNG, WebP, TIFF, BMP, HEIC, PDF, DOCX, TXT, CSV, "
            "JSON, XML and YAML."
        ),
    )
