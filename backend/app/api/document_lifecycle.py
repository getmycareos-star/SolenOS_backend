"""Persist source-verified text extraction and local OCR against uploaded evidence."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from datetime import datetime, timezone

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

from fastapi import APIRouter, Depends, HTTPException, Request
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.collaboration import EvidenceUpload
from app.services.document_candidate_schema import classify_document

router = APIRouter(prefix="/documents", tags=["document lifecycle"])
MAX_EXTRACTED_CHARS = 100_000
MAX_PDF_PAGES = 100
MAX_OCR_PAGES = 25
MIN_NATIVE_PDF_TEXT_CHARS = 40
MAX_IMAGE_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _identity(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        return str(person_id), str(caregiver_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS care session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Care session is invalid or expired")
    return str(session.person_id), str(session.caregiver_id)


def _verified_content(upload: EvidenceUpload) -> bytes:
    """Verify the persisted original before both first extraction and cached retries."""
    content = bytes(upload.content)
    if len(content) != upload.size_bytes or hashlib.sha256(content).hexdigest() != upload.sha256:
        raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
    return content


def _ocr_image(image: Image.Image) -> str:
    """Run the installed local Tesseract engine; never silently fall back to a fake result."""
    try:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        return pytesseract.image_to_string(normalized, lang="eng", config="--psm 6 -c preserve_interword_spaces=1").strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="The local OCR engine is not installed; the original document remains preserved.",
        ) from exc
    except pytesseract.TesseractError as exc:
        raise HTTPException(status_code=422, detail="OCR could not read this document image") from exc


class _HTMLText(HTMLParser):
    """Minimal HTML-to-text converter for email bodies; scripts/styles are discarded."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "head"}:
            self.hidden += 1
        elif tag.lower() in {"p", "div", "br", "li", "tr"} and not self.hidden:
            self.parts.append("\\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "head"} and self.hidden:
            self.hidden -= 1
        elif tag.lower() in {"p", "div", "li", "tr"} and not self.hidden:
            self.parts.append("\\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        return re.sub(r"\\n{3,}", "\\n\\n", "".join(self.parts)).strip()


def _extract_docx(content: bytes) -> str:
    """Read only the main WordprocessingML document with bounded ZIP expansion."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024 or info.compress_size == 0:
                raise HTTPException(status_code=413, detail="Word document content exceeds extraction limits")
            if info.file_size / max(info.compress_size, 1) > 100:
                raise HTTPException(status_code=413, detail="Word document compression ratio exceeds safety limits")
            xml = archive.read(info)
        root = ET.fromstring(xml)
    except HTTPException:
        raise
    except (KeyError, zipfile.BadZipFile, ET.ParseError, OSError) as exc:
        raise HTTPException(status_code=422, detail="The saved Word document could not be read") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", ns):
        value = "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        if value.strip():
            paragraphs.append(value)
    return "\\n".join(paragraphs).strip()


def _extract_rtf(content: bytes) -> str:
    """Conservatively decode common RTF text controls without executing embedded objects."""
    try:
        source = content.decode("latin-1")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="The saved RTF document could not be decoded") from exc
    output = []
    stack = []
    skip = False
    index = 0
    while index < len(source):
        char = source[index]
        if char == "{":
            stack.append(skip)
            index += 1
            continue
        if char == "}":
            skip = stack.pop() if stack else False
            index += 1
            continue
        if char == "\\\\":
            if index + 1 >= len(source):
                break
            nxt = source[index + 1]
            if nxt in "{}\\\\":
                if not skip:
                    output.append(nxt)
                index += 2
                continue
            if nxt == "'":
                token = source[index + 2:index + 4]
                try:
                    if not skip and len(token) == 2:
                        output.append(bytes([int(token, 16)]).decode("cp1252"))
                except (ValueError, UnicodeDecodeError):
                    pass
                index += 4
                continue
            match = re.match(r"\\\\([a-zA-Z]+)(-?\\d+)? ?", source[index:])
            if match:
                word, number = match.group(1), match.group(2)
                if word in {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer"}:
                    skip = True
                elif not skip and word in {"par", "line"}:
                    output.append("\\n")
                elif not skip and word == "tab":
                    output.append("\\t")
                elif not skip and word == "u" and number:
                    try:
                        output.append(chr(int(number) % 65536))
                    except ValueError:
                        pass
                index += len(match.group(0))
                continue
            index += 2
            continue
        if not skip and char not in "\\r\\n":
            output.append(char)
        index += 1
    return re.sub(r"[ \\t]+", " ", "".join(output)).strip()


def _bounded_extraction(text: str, method: str) -> tuple[str, str]:
    if len(text) > MAX_EXTRACTED_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Extraction exceeds the {MAX_EXTRACTED_CHARS}-character processing limit. "
                "The original remains saved; this document needs a supported smaller section or page range."
            ),
        )
    return text, method


def _scan_adjusted_image(image: Image.Image, evidence: Evidence | None) -> tuple[Image.Image, bool]:
    """Create an in-memory OCR derivative; never mutate or replace the persisted original."""
    if evidence is None:
        return image, False
    try:
        metadata = evidence.extra_metadata if isinstance(evidence.extra_metadata, dict) else json.loads(evidence.extra_metadata or "{}")
    except (TypeError, ValueError):
        return image, False
    if not isinstance(metadata, dict):
        return image, False
    capture = metadata.get("capture") if isinstance(metadata.get("capture"), dict) else {}
    if capture.get("method") != "scan":
        return image, False
    scan = metadata.get("scan_processing") if isinstance(metadata.get("scan_processing"), dict) else {}
    rotation = scan.get("rotation_degrees", 0)
    try:
        rotation = float(rotation or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Saved scan rotation metadata is invalid")
    if not (-360 <= rotation <= 360):
        raise HTTPException(status_code=422, detail="Saved scan rotation is outside the supported range")
    crop = scan.get("crop_insets_percent") if isinstance(scan.get("crop_insets_percent"), dict) else {}
    insets = {}
    for side in ("top", "right", "bottom", "left"):
        try:
            value = float(crop.get(side, 0) or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Saved scan crop metadata is invalid")
        if not (0 <= value <= 80):
            raise HTTPException(status_code=422, detail="Saved scan crop metadata is outside the supported range")
        insets[side] = value
    if insets["left"] + insets["right"] >= 80 or insets["top"] + insets["bottom"] >= 80:
        raise HTTPException(status_code=422, detail="Saved scan crop leaves too little page area")
    adjusted = ImageOps.exif_transpose(image).convert("RGB")
    if rotation:
        # Browser canvas uses positive clockwise angles; Pillow's positive angle is counter-clockwise.
        adjusted = adjusted.rotate(-rotation, expand=True, fillcolor="white")
    width, height = adjusted.size
    left = round(width * insets["left"] / 100)
    right = round(width * (100 - insets["right"]) / 100)
    top = round(height * insets["top"] / 100)
    bottom = round(height * (100 - insets["bottom"]) / 100)
    if left or top or right < width or bottom < height:
        adjusted = adjusted.crop((left, top, right, bottom))
    if adjusted.width * adjusted.height > MAX_IMAGE_PIXELS:
        adjusted.thumbnail((int(MAX_IMAGE_PIXELS ** 0.5), int(MAX_IMAGE_PIXELS ** 0.5)))
    return adjusted, True


def _extract(upload: EvidenceUpload, content: bytes | None = None, evidence: Evidence | None = None) -> tuple[str, str]:
    content = _verified_content(upload) if content is None else content
    mime = (upload.mime_type or "").split(";", 1)[0].lower()
    filename = (upload.filename or "").lower()
    if mime == "application/pdf" or filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content), strict=True)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise HTTPException(status_code=413, detail="PDFs are limited to 100 pages")
            native_pages = []
            for page in reader.pages:
                try:
                    page_text = page.extract_text(extraction_mode="layout") or ""
                except TypeError:
                    # Older pypdf versions do not expose layout extraction.
                    page_text = page.extract_text() or ""
                native_pages.append(page_text.strip())
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved PDF could not be read") from exc

        # Sparse hidden text layers (e.g. a scanned page with only a footer)
        # do not prove that the clinical content is digitally readable.
        ocr_page_indexes = [
            index for index, value in enumerate(native_pages)
            if len(value.strip()) < MIN_NATIVE_PDF_TEXT_CHARS
        ]
        if native_pages and not ocr_page_indexes:
            page_texts = [f"[Page {index + 1}]\n{text}" for index, text in enumerate(native_pages)]
            return _bounded_extraction("\n\n".join(page_texts), "server_pdf_text")

        if len(ocr_page_indexes) > MAX_OCR_PAGES:
            raise HTTPException(
                status_code=413,
                detail=f"Scanned PDF pages are limited to {MAX_OCR_PAGES} per extraction.",
            )
        rendered = None
        try:
            rendered = pdfium.PdfDocument(content)
            page_texts = []
            for index, native_text in enumerate(native_pages):
                if len(native_text.strip()) >= MIN_NATIVE_PDF_TEXT_CHARS:
                    page_texts.append(f"[Page {index + 1}]\n{native_text}")
                    continue
                page = rendered[index]
                try:
                    width, height = page.get_size()
                    if width * height * 4 > MAX_IMAGE_PIXELS:
                        raise HTTPException(
                            status_code=413,
                            detail=f"PDF page {index + 1} exceeds the 20-megapixel OCR limit.",
                        )
                    bitmap = page.render(scale=2)
                    image = bitmap.to_pil()
                    try:
                        ocr_text = _ocr_image(image)
                    finally:
                        image.close()
                        bitmap.close()
                    if ocr_text:
                        combined_text = "\n".join(value for value in (native_text.strip(), ocr_text) if value)
                        page_texts.append(f"[Page {index + 1} OCR]\n{combined_text}")
                    elif native_text.strip():
                        page_texts.append(f"[Page {index + 1}]\n{native_text.strip()}")
                    else:
                        # Keep unreadable page identity explicit; never invent content.
                        page_texts.append(f"[Page {index + 1} OCR]\n[No readable text detected on this page]")
                finally:
                    page.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved PDF page could not be rendered for OCR") from exc
        finally:
            if rendered is not None:
                rendered.close()
        extracted = "\n\n".join(page_texts).strip()
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="No readable text was found after OCR. The original document remains preserved.",
            )
        method = "server_pdf_text_plus_tesseract_ocr" if any(native_pages) else "server_tesseract_ocr_pdf"
        return _bounded_extraction(extracted, method)

    if mime.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise HTTPException(status_code=413, detail="Images are limited to 20 megapixels for OCR")
                adjusted, used_scan_adjustments = _scan_adjusted_image(image, evidence)
                try:
                    extracted = _ocr_image(adjusted)
                finally:
                    if used_scan_adjustments:
                        adjusted.close()
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(status_code=422, detail="The saved image could not be decoded for OCR") from exc
        if not extracted:
            raise HTTPException(
                status_code=422,
                detail="No readable text was found in the image. The original document remains preserved.",
            )
        return _bounded_extraction(f"[Image OCR]\n[Page 1 OCR]\n{extracted}", "server_tesseract_ocr_image")

    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document" or filename.endswith(".docx"):
        extracted = _extract_docx(content)
        if not extracted:
            raise HTTPException(status_code=422, detail="No readable text was found in the Word document")
        return _bounded_extraction(extracted, "server_docx_xml")

    if mime in {"application/rtf", "text/rtf"} or filename.endswith(".rtf"):
        extracted = _extract_rtf(content)
        if not extracted:
            raise HTTPException(status_code=422, detail="No readable text was found in the RTF document")
        return _bounded_extraction(extracted, "server_rtf_text")

    if mime == "message/rfc822" or filename.endswith(".eml"):
        try:
            message = BytesParser(policy=policy.default).parsebytes(content)
            body = message.get_body(preferencelist=("plain",))
            if body is not None:
                extracted = body.get_content()
            else:
                html_body = message.get_body(preferencelist=("html",))
                parser = _HTMLText()
                if html_body is not None:
                    parser.feed(html_body.get_content())
                extracted = parser.text()
            headers = "\\n".join(
                f"{name}: {message.get(name)}" for name in ("From", "To", "Date", "Subject") if message.get(name)
            )
            extracted = (headers + "\\n\\n" + str(extracted or "")).strip()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="The saved email could not be read") from exc
        if not extracted:
            raise HTTPException(status_code=422, detail="The email contains no readable message body")
        return _bounded_extraction(extracted, "server_email_text")

    if mime == "application/msword" or filename.endswith(".doc"):
        raise HTTPException(
            status_code=415,
            detail="Legacy .doc files are preserved, but extraction is unavailable on this server. Save as .docx or PDF and upload again.",
        )

    if mime.startswith("text/") or mime in {"application/json", "application/xml", "application/yaml"} or filename.endswith(
        (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml", ".log")
    ):
        try:
            extracted = content.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="Saved text document is not UTF-8") from exc
        if not extracted:
            raise HTTPException(status_code=422, detail="The saved document contains no readable text")
        return _bounded_extraction(extracted, "server_utf8_text")

    raise HTTPException(
        status_code=415,
        detail="Supported formats are UTF-8 text, text-based or scanned PDF, and common image files.",
    )



def _normalized_extraction_text(value: str) -> str:
    """Compare legacy unlabelled extractions with the newer page-labelled format."""
    without_labels = re.sub(
        r"(?m)^\[(?:Page\s+\d+(?:\s+OCR)?|Image OCR|No readable text detected on this page)\]\s*\n?",
        "",
        value or "",
    )
    return re.sub(r"\s+", " ", without_labels).strip()


def _verify_persisted_extraction(
    analysis: CaptureAnalysis,
    upload: EvidenceUpload,
    db: Session,
    content: bytes | None = None,
) -> bytes:
    """Verify binary and extracted-text integrity without re-running OCR on every read."""
    content = _verified_content(upload) if content is None else content
    binary_digest = hashlib.sha256(content).hexdigest()
    metadata = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if metadata.get("kind") != "document_extraction_v1" or metadata.get("binary_sha256") != binary_digest:
        raise HTTPException(status_code=409, detail="The extraction no longer matches the preserved original document")

    source_text = (analysis.source_text or "").strip()
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    stored_digest = metadata.get("source_text_sha256")
    if stored_digest:
        if stored_digest != source_digest:
            raise HTTPException(status_code=409, detail="The persisted extraction text changed after extraction")
    else:
        # Backfill the digest for older persisted rows exactly once. Re-run OCR
        # only for this legacy case, then persist the integrity marker.
        original_text, _ = _extract(upload, content)
        if _normalized_extraction_text(original_text) != _normalized_extraction_text(source_text):
            raise HTTPException(status_code=409, detail="Persisted extraction text does not match the original uploaded document")
        metadata = dict(metadata)
        metadata["source_text_sha256"] = source_digest
        analysis.candidates = metadata
        db.add(analysis)
        db.flush()
    return content


def _response(analysis: CaptureAnalysis) -> dict:
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    intelligence = details.get("document_intelligence")
    intelligence = intelligence if isinstance(intelligence, dict) else {}
    observations = intelligence.get("observations")
    observations = observations if isinstance(observations, list) else []
    return {
        "analysis_id": analysis.id,
        "source_evidence_id": analysis.source_evidence_id,
        "extraction_status": analysis.extraction_status,
        "extraction_method": details.get("extraction_method"),
        "source_text_sha256": hashlib.sha256((analysis.source_text or "").strip().encode("utf-8")).hexdigest(),
        "extracted_char_count": len(analysis.source_text or ""),
        "candidate_count": len(observations),
        "intelligence_status": intelligence.get("status") or "not_run",
        "review_required": any(
            isinstance(item, dict) and item.get("review_status") in {"pending_review", "corrected_pending_approval"}
            for item in observations
        ),
        "written_to_lcr": any(
            isinstance(item, dict) and item.get("review_status") == "accepted_by_caregiver"
            for item in observations
        ),
    }


@router.post("/{evidence_id}/extract", status_code=201)
def extract_saved_document(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    """Extract from the persisted original bytes and bind the result to its evidence ID."""
    person_id, caregiver_id = _identity(request, db)
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).with_for_update().first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Saved document not found in this care record")
    upload = db.query(EvidenceUpload).filter(EvidenceUpload.evidence_id == evidence_id).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="Original document bytes are unavailable")

    # Verify the immutable original on every request, including idempotent retries.
    # A cached analysis must never hide later byte corruption or source replacement.
    content = _verified_content(upload)
    existing = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).all()
    for row in existing:
        metadata = row.candidates if isinstance(row.candidates, dict) else {}
        if metadata.get("kind") == "document_extraction_v1":
            _verify_persisted_extraction(row, upload, db, content)
            db.commit()
            db.refresh(row)
            return _response(row)

    source_text, method = _extract(upload, content, evidence)
    analysis = CaptureAnalysis(
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=None,
        source_evidence_id=evidence_id,
        source_text=source_text,
        extraction_status="available",
        candidates={
            "kind": "document_extraction_v1",
            "filename": upload.filename,
            "mime_type": upload.mime_type,
            "binary_sha256": upload.sha256,
            "source_text_sha256": hashlib.sha256(source_text.strip().encode("utf-8")).hexdigest(),
            "extraction_method": method,
            **classify_document(upload.filename or "", source_text),
            "page_count": len(re.findall(r"(?m)^\[Page\s+\d+", source_text)) or 1,
            "observations": [],
            "open_questions": [],
        },
        review_history=[],
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return _response(analysis)


@router.get("/analyses/{analysis_id}")
def get_document_extraction(analysis_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _identity(request, db)
    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis_id,
        CaptureAnalysis.person_id == person_id,
        CaptureAnalysis.caregiver_id == caregiver_id,
    ).first()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    if details.get("kind") != "document_extraction_v1":
        raise HTTPException(status_code=404, detail="Document extraction not found")
    evidence = db.query(Evidence).filter(
        Evidence.id == analysis.source_evidence_id,
        Evidence.person_id == person_id,
        Evidence.uploaded_by_caregiver_id == caregiver_id,
        Evidence.type == "document",
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Document extraction not found")
    upload = db.query(EvidenceUpload).filter(
        EvidenceUpload.evidence_id == analysis.source_evidence_id
    ).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="Original document is unavailable")
    content = _verify_persisted_extraction(analysis, upload, db)
    db.commit()
    db.refresh(analysis)
    details = analysis.candidates if isinstance(analysis.candidates, dict) else {}
    intelligence = details.get("document_intelligence")
    if isinstance(intelligence, dict) and intelligence.get("source_text_sha256"):
        current_text_hash = hashlib.sha256((analysis.source_text or "").strip().encode("utf-8")).hexdigest()
        if intelligence["source_text_sha256"] != current_text_hash:
            raise HTTPException(status_code=409, detail="The persisted extraction text changed after document understanding")
    return {
        **_response(analysis),
        "extracted_text": analysis.source_text,
        "document": {
            "filename": details.get("filename"),
            "mime_type": details.get("mime_type"),
            "binary_sha256": details.get("binary_sha256"),
        },
    }
