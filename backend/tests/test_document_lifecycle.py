import hashlib
import io
import json

import pytest
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.models.collaboration import EvidenceUpload
from app.models.qwen_background_job import QwenBackgroundJob

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    with TestClient(app) as test_client:
        created = test_client.post("/api/v1/sessions/anonymous", json={
            "name": "Document lifecycle recipient", "caregiver_name": "Family caregiver"
        })
        assert created.status_code == 201, created.text
        yield test_client, created.json()
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def persist_document(session, content: bytes, filename="care-note.txt", mime="text/plain"):
    import hashlib
    import uuid
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    evidence_id = str(uuid.uuid4())
    db = TestSessionLocal()
    try:
        db.add(Evidence(
            id=evidence_id, person_id=person_id, type="document", source_text="",
            uploaded_by_caregiver_id=caregiver_id, source_type="caregiver_upload",
        ))
        db.flush()
        db.add(EvidenceUpload(
            evidence_id=evidence_id, filename=filename, mime_type=mime,
            content=content, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
        ))
        db.commit()
    finally:
        db.close()
    return evidence_id


def test_extract_reads_saved_original_and_persists_analysis(client):
    test_client, session = client
    evidence_id = persist_document(session, b"Care note: breakfast was mostly uneaten.")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_evidence_id"] == evidence_id
    assert body["extraction_method"] == "server_utf8_text"
    assert body["written_to_lcr"] is False

    retrieved = test_client.get(f"/api/v1/documents/analyses/{body['analysis_id']}", headers=auth)
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["extracted_text"] == "Care note: breakfast was mostly uneaten."
    assert retrieved.json()["document"]["binary_sha256"]


def test_extract_is_idempotent_for_same_saved_document(client):
    test_client, session = client
    evidence_id = persist_document(session, b"Care note: medication was collected.")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    first = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    second = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["analysis_id"] == second.json()["analysis_id"]


def test_extract_denies_cross_person_document_access(client):
    test_client, first = client
    second_response = test_client.post("/api/v1/sessions/anonymous", json={
        "name": "Different recipient", "caregiver_name": "Different caregiver"
    })
    assert second_response.status_code == 201, second_response.text
    evidence_id = persist_document(first, b"Private care note")
    foreign_auth = {"Authorization": f"Bearer {second_response.json()['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=foreign_auth)
    assert response.status_code == 404, response.text


def _scanned_text_image():
    image = Image.new("RGB", (2200, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=64)
    draw.text((60, 150), "Patient walked 20 metres with a frame on 12 October.", fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_real_tesseract_ocr_extracts_uploaded_scanned_image(client):
    test_client, session = client
    image_bytes = _scanned_text_image()
    evidence_id = persist_document(session, image_bytes, "scan.png", "image/png")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_method"] == "server_tesseract_ocr_image"
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert retrieved.status_code == 200, retrieved.text
    extracted = retrieved.json()["extracted_text"]
    assert extracted.startswith("[Image OCR]")
    assert "Patient walked 20 metres" in extracted
    assert "12 October" in extracted




def test_sparse_native_pdf_text_layer_still_triggers_ocr_for_clinical_content(client, monkeypatch):
    from app.api import document_lifecycle

    class NativePage:
        def extract_text(self, extraction_mode=None):
            return "footer 1"

    class Reader:
        pages = [NativePage()]

        def __init__(self, *args, **kwargs):
            pass

    class Bitmap:
        def to_pil(self):
            return Image.new("RGB", (20, 20), "white")

        def close(self):
            pass

    class RenderPage:
        def get_size(self):
            return (600, 800)

        def render(self, scale=2):
            return Bitmap()

        def close(self):
            pass

    class RenderedPdf:
        def __getitem__(self, index):
            assert index == 0
            return RenderPage()

        def close(self):
            pass

    class Pdfium:
        @staticmethod
        def PdfDocument(content):
            return RenderedPdf()

    monkeypatch.setattr(document_lifecycle, "PdfReader", Reader)
    monkeypatch.setattr(document_lifecycle.pdfium, "PdfDocument", Pdfium.PdfDocument)
    monkeypatch.setattr(document_lifecycle, "_ocr_image", lambda image: "Patient walked 20 metres with a frame.")

    test_client, session = client
    evidence_id = persist_document(session, b"%PDF-sparse-text-layer", "mixed-scan.pdf", "application/pdf")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_method"] == "server_pdf_text_plus_tesseract_ocr"
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}",
        headers=auth,
    )
    assert retrieved.status_code == 200, retrieved.text
    extracted = retrieved.json()["extracted_text"]
    assert "[Page 1 OCR]" in extracted
    assert "footer 1" in extracted
    assert "Patient walked 20 metres" in extracted

def test_real_tesseract_ocr_extracts_scanned_pdf_with_page_provenance(client):
    test_client, session = client
    image_bytes = _scanned_text_image()
    image = Image.open(io.BytesIO(image_bytes))
    pdf_buffer = io.BytesIO()
    image.save(pdf_buffer, format="PDF", resolution=150)
    image.close()
    pdf_bytes = pdf_buffer.getvalue()
    evidence_id = persist_document(session, pdf_bytes, "scan.pdf", "application/pdf")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_method"] == "server_tesseract_ocr_pdf"
    quality = response.json()["ocr_quality_by_page"]
    assert len(quality) == 1
    assert quality[0]["page_number"] == 1
    assert quality[0]["method"] == "tesseract_word_confidence_heuristic"
    assert quality[0]["is_probability"] is False
    assert quality[0]["token_count"] > 0
    assert "Handwriting may be misread" in quality[0]["handwriting_warning"]
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert retrieved.status_code == 200, retrieved.text
    extracted = retrieved.json()["extracted_text"]
    assert "[Page 1 OCR]" in extracted
    assert "Patient walked 20 metres" in extracted


def test_scanned_pdf_processes_more_than_25_pages_and_reports_partial_results(client, monkeypatch):
    from fastapi import HTTPException
    from pypdf import PdfWriter
    from app.api import document_lifecycle

    writer = PdfWriter()
    for _ in range(30):
        writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    test_client, session = client
    evidence_id = persist_document(session, output.getvalue(), "thirty-page-scan.pdf", "application/pdf")
    calls = {"count": 0}

    def page_ocr(_image):
        calls["count"] += 1
        if calls["count"] == 2:
            raise HTTPException(status_code=422, detail="Page OCR failed")
        return f"Extracted page {calls['count']}"

    monkeypatch.setattr(document_lifecycle, "_ocr_image", page_ocr)
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["extraction_status"] == "partial"
    assert payload["page_count"] == 30
    assert payload["unreadable_page_numbers"] == []
    assert payload["unprocessed_page_numbers"] == [2]
    assert payload["processed_page_numbers"] == [1, *range(3, 31)]
    assert calls["count"] == 30
    retrieved = test_client.get(f"/api/v1/documents/analyses/{payload['analysis_id']}", headers=auth)
    assert retrieved.status_code == 200, retrieved.text
    assert "[Page 2 OCR]" in retrieved.json()["extracted_text"]
    assert "[Page not processed: OCR engine error]" in retrieved.json()["extracted_text"]
    assert "[No readable text detected on this page]" not in retrieved.json()["extracted_text"]
    assert "Extracted page 30" in retrieved.json()["extracted_text"]


def test_partial_extraction_retries_saved_original_without_duplicate_upload(client, monkeypatch):
    from fastapi import HTTPException
    from pypdf import PdfWriter
    from app.api import document_lifecycle

    test_client, session = client
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    original = output.getvalue()
    evidence_id = persist_document(session, original, "recoverable-two-page-scan.pdf", "application/pdf")
    state = {"attempt": 1, "page": 0, "calls": []}

    def page_ocr(_image):
        state["page"] += 1
        state["calls"].append((state["attempt"], state["page"]))
        if state["attempt"] == 1 and state["page"] == 2:
            raise HTTPException(status_code=422, detail="Temporary page OCR failure")
        return f"Recovered page {state['page']}"

    monkeypatch.setattr(document_lifecycle, "_ocr_image", page_ocr)
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    first = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert first.status_code == 201, first.text
    assert first.json()["extraction_status"] == "partial"
    assert first.json()["unreadable_page_numbers"] == []
    assert first.json()["unprocessed_page_numbers"] == [2]
    analysis_id = first.json()["analysis_id"]

    state["attempt"] = 2
    state["page"] = 1
    second = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert second.status_code == 201, second.text
    assert second.json()["analysis_id"] == analysis_id
    assert second.json()["extraction_status"] == "available"
    assert second.json()["unreadable_page_numbers"] == []
    assert second.json()["processed_page_numbers"] == [1, 2]
    assert state["calls"] == [(1, 1), (1, 2), (2, 2)], "retry must reprocess only the failed page"

    retrieved = test_client.get(f"/api/v1/documents/analyses/{analysis_id}", headers=auth)
    assert retrieved.status_code == 200, retrieved.text
    assert "Recovered page 2" in retrieved.json()["extracted_text"]
    with TestSessionLocal() as db:
        assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 1
        assert db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).count() == 1
        saved = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
        assert saved.sha256 == hashlib.sha256(original).hexdigest()
        assert saved.content == original


def test_real_upload_endpoint_feeds_document_extraction(client):
    test_client, session = client
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "evidence_type": "document",
            "extracted_text": "FORGED CLIENT TEXT: this must never become authoritative evidence.",
        },
        files={"file": ("uploaded-care-note.txt", b"Uploaded through the real endpoint.", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    processing_job_id = uploaded.json()["document_processing_job_id"]
    assert processing_job_id
    assert uploaded.json()["processing_status"] == "queued"
    with TestSessionLocal() as db:
        job = db.query(QwenBackgroundJob).filter_by(id=processing_job_id).one()
        assert job.workflow == "document_intake"
        assert job.status == "queued"
        assert __import__("json").loads(job.evidence_ids_json) == [evidence_id]
        stored = db.query(Evidence).filter_by(id=evidence_id).one()
        assert stored.person_id == session["person"]["id"]
        assert stored.uploaded_by_caregiver_id == session["caregiver"]["id"]
        assert stored.type == "document"

    queue_state = test_client.get("/api/v1/documents/intelligence", headers=auth)
    assert queue_state.status_code == 200, queue_state.text
    assert any(
        item["evidence_id"] == evidence_id and item["status"] == "queued"
        for item in queue_state.json()["processing_jobs"]
    )

    # Client-provided OCR/text must not enter the authoritative Evidence source.
    with TestSessionLocal() as db:
        evidence = db.query(Evidence).filter_by(id=evidence_id).one()
        assert evidence.source_text == ""
        metadata = __import__("json").loads(evidence.extra_metadata or "{}")
        assert metadata["text_source"] == "server_extraction_pending"
        assert metadata["source_integrity"]["client_supplied_document_text_ignored"] is True

    extracted = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert extracted.status_code == 201, extracted.text
    assert extracted.json()["source_evidence_id"] == evidence_id

    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{extracted.json()['analysis_id']}",
        headers=auth,
    )
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["extracted_text"] == "Uploaded through the real endpoint."


def test_cached_extraction_rechecks_original_binary_integrity(client, monkeypatch):
    from fastapi import HTTPException
    from app.api import document_lifecycle

    test_client, session = client
    evidence_id = persist_document(session, b"Original saved care note")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    first = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert first.status_code == 201, first.text

    from app.api.document_lifecycle import extract_saved_document

    original_verify = extract_saved_document.__globals__["_verified_content"]
    calls = 0

    def fail_on_retry(upload):
        nonlocal calls
        calls += 1
        if calls >= 1:
            raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
        return original_verify(upload)

    monkeypatch.setitem(extract_saved_document.__globals__, "_verified_content", fail_on_retry)
    retry = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert retry.status_code == 500, retry.text
    assert retry.json()["detail"] == "Stored evidence failed its integrity check"



def test_duplicate_upload_is_preserved_and_linked_to_original(client):
    test_client, session = client
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    payload = {
        "person_id": session["person"]["id"],
        "caregiver_id": session["caregiver"]["id"],
        "evidence_type": "document",
    }
    original = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=payload,
        files={"file": ("discharge-copy.txt", b"identical original document bytes", "text/plain")},
    )
    assert original.status_code == 201, original.text
    with TestSessionLocal() as db:
        original_evidence_id = original.json()["evidence_id"]
        assert db.query(Evidence).filter_by(id=original_evidence_id).one()
        assert db.query(EvidenceUpload).filter_by(evidence_id=original_evidence_id).one()
    duplicate = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=payload,
        files={"file": ("copy-of-discharge.txt", b"identical original document bytes", "text/plain")},
    )
    assert original.status_code == 201, original.text
    assert duplicate.status_code == 201, duplicate.text
    assert original.json()["persisted"] is True
    assert original.json()["duplicate_detected"] is False
    assert duplicate.json()["persisted"] is True
    assert duplicate.json()["duplicate_detected"] is True
    assert duplicate.json()["duplicate_of_evidence_id"] == original.json()["evidence_id"]
    assert duplicate.json()["evidence_id"] != original.json()["evidence_id"]



def test_poor_quality_photo_with_unreadable_ocr_is_preserved_without_inferred_text(client, monkeypatch):
    from app.api import document_lifecycle
    from app.models.capture_analysis import CaptureAnalysis

    test_client, session = client
    image = Image.new("RGB", (240, 160), "#d0d0d0")
    draw = ImageDraw.Draw(image)
    draw.text((8, 8), "Medication 500 mg", fill="#b8b8b8", font=ImageFont.load_default(size=12))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=8)
    image.close()
    original_bytes = output.getvalue()
    monkeypatch.setattr(document_lifecycle.pytesseract, "image_to_data", lambda *args, **kwargs: {"text": [], "conf": [], "block_num": [], "par_num": [], "line_num": []})
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "evidence_type": "document",
        },
        files={"file": ("poor-quality-photo.jpg", original_bytes, "image/jpeg")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    with TestSessionLocal() as db:
        stored = db.query(Evidence).filter_by(id=evidence_id).one()
        assert stored.person_id == session["person"]["id"]
        assert stored.uploaded_by_caregiver_id == session["caregiver"]["id"]
        assert stored.type == "document"

    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_status"] == "partial"
    assert response.json()["processed_page_numbers"] == [1]
    assert response.json()["unreadable_page_numbers"] == [1]
    assert response.json()["unprocessed_page_numbers"] == []
    assert response.json()["extracted_char_count"] > 0

    original_download = test_client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
    assert original_download.status_code == 200, original_download.text
    assert original_download.content == original_bytes
    with TestSessionLocal() as db:
        assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 1
        analysis = db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).one()
        assert "[No readable text detected on this page]" in analysis.source_text
        assert "Medication 500 mg" not in analysis.source_text



def test_multi_column_pdf_uses_layout_preserving_text_extraction(client, monkeypatch):
    from app.api import document_lifecycle

    class MultiColumnPage:
        def extract_text(self, extraction_mode=None):
            assert extraction_mode == "layout"
            return "LEFT COLUMN: Medication 500 mg\\nRIGHT COLUMN: Follow-up in 14 days"

    class LayoutReader:
        pages = [MultiColumnPage()]

        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(document_lifecycle, "PdfReader", LayoutReader)
    test_client, session = client
    evidence_id = persist_document(session, b"%PDF-layout-test", "multi-column.pdf", "application/pdf")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert retrieved.status_code == 200, retrieved.text
    assert "LEFT COLUMN: Medication 500 mg" in retrieved.json()["extracted_text"]
    assert "RIGHT COLUMN: Follow-up in 14 days" in retrieved.json()["extracted_text"]


def _digital_pdf_with_two_columns():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
    })
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 50 720 Td (Discharge summary: patient walked 20 metres.) Tj ET\n"
        b"BT /F1 12 Tf 320 720 Td (Medication: metformin 500 mg.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_clean_digital_pdf_extracts_real_text_without_ocr(client):
    test_client, session = client
    evidence_id = persist_document(
        session,
        _digital_pdf_with_two_columns(),
        "digital-discharge-summary.pdf",
        "application/pdf",
    )
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_method"] == "server_pdf_text"
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert retrieved.status_code == 200, retrieved.text
    assert "Discharge summary: patient walked 20 metres" in retrieved.json()["extracted_text"]
    assert "Medication: metformin 500 mg" in retrieved.json()["extracted_text"]


def _docx_bytes(text):
    import zipfile
    output = io.BytesIO()
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>' + text + '</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "mime", "content", "method", "expected"),
    [
        (
            "care-plan.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_bytes("Patient needs help with morning dressing."),
            "server_docx_xml",
            "Patient needs help with morning dressing.",
        ),
        (
            "care-note.rtf",
            "application/rtf",
            b"{\\rtf1\\ansi Patient\\'92s mobility\\par Needs a walking frame.}",
            "server_rtf_text",
            "Patient’s mobility\nNeeds a walking frame.",
        ),
        (
            "clinic-email.eml",
            "message/rfc822",
            b"From: clinic@example.org\r\nTo: family@example.org\r\nSubject: Follow-up\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nPlease arrange a review next Tuesday.",
            "server_email_text",
            "Please arrange a review next Tuesday.",
        ),
    ],
)
def test_supported_office_and_email_formats_extract_from_saved_original(
    client, filename, mime, content, method, expected
):
    test_client, session = client
    evidence_id = persist_document(session, content, filename, mime)
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_method"] == method
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert retrieved.status_code == 200, retrieved.text
    assert expected in retrieved.json()["extracted_text"]


def test_legacy_doc_is_preserved_and_extracted_with_local_antiword(client, monkeypatch):
    from types import SimpleNamespace
    import app.api.document_lifecycle as lifecycle

    test_client, session = client
    original = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-word-binary"
    evidence_id = persist_document(session, original, "legacy.doc", "application/msword")
    observed = {}
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: "/usr/bin/antiword")

    def fake_run(command, **kwargs):
        with open(command[-1], "rb") as source:
            observed["original"] = source.read()
        return SimpleNamespace(
            returncode=0,
            stdout=b"Discharge summary\nNeeds a walking frame.",
            stderr=b"",
        )

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 201, response.text
    assert response.json()["extraction_method"] == "server_antiword_doc"
    detail = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert detail.status_code == 200, detail.text
    assert "Needs a walking frame" in detail.json()["extracted_text"]
    assert observed["original"] == original
    with TestSessionLocal() as db:
        upload = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
        assert upload.content == original


def test_snap_care_photo_upload_is_persisted_without_document_ocr(client):
    test_client, session = client
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    image_bytes = _scanned_text_image()
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "evidence_type": "care_photo",
            "capture_method": "snap",
            "capture_timestamp": "2026-11-12T06:00:00Z",
        },
        files={"file": ("current-care-photo.png", image_bytes, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    with TestSessionLocal() as db:
        evidence = db.query(Evidence).filter_by(id=evidence_id).one()
        upload = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
        assert evidence.type == "care_photo"
        assert upload.content == image_bytes
        assert upload.sha256
        metadata = __import__("json").loads(evidence.extra_metadata or "{}")
        assert metadata["capture"]["method"] == "snap"
        assert metadata["capture"]["captured_at"] == "2026-11-12T06:00:00+00:00"
    with TestSessionLocal() as db:
        assert db.query(QwenBackgroundJob).filter(QwenBackgroundJob.evidence_ids_json.contains(evidence_id)).count() == 0
    # Document OCR is intentionally not run against Snap care-photo evidence.
    extraction = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert extraction.status_code == 404, extraction.text


def test_extraction_limit_fails_explicitly_without_silent_truncation(client):
    test_client, session = client
    evidence_id = persist_document(session, b"A" * 100_001, "long-care-note.txt", "text/plain")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 413, response.text
    assert "processing limit" in response.json()["detail"]
    # The original evidence remains retrievable after the extraction limit is reached.
    original = test_client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
    assert original.status_code == 200, original.text
    assert len(original.content) == 100_001


def test_scan_crop_and_rotation_create_ocr_derivative_without_mutating_original():
    from types import SimpleNamespace
    from app.api.document_lifecycle import _scan_adjusted_image

    original = Image.new("RGB", (100, 200), "white")
    evidence = SimpleNamespace(extra_metadata=__import__("json").dumps({
        "capture": {"method": "scan"},
        "scan_processing": {
            "rotation_degrees": 90,
            "crop_insets_percent": {"top": 10, "right": 20, "bottom": 10, "left": 10},
        },
    }))
    derivative, changed = _scan_adjusted_image(original, evidence)
    assert changed is True
    assert derivative.size == (140, 80)
    assert original.size == (100, 200)
    assert original.getpixel((0, 0)) == (255, 255, 255)
    derivative.close()
    original.close()


def test_snap_photo_does_not_receive_document_scan_adjustments():
    from types import SimpleNamespace
    from app.api.document_lifecycle import _scan_adjusted_image

    original = Image.new("RGB", (100, 200), "white")
    evidence = SimpleNamespace(extra_metadata='{"capture":{"method":"snap"},"scan_processing":{"rotation_degrees":90,"crop_insets_percent":{"left":20}}}')
    derivative, changed = _scan_adjusted_image(original, evidence)
    assert changed is False
    assert derivative is original
    assert derivative.size == (100, 200)
    original.close()


def test_invalid_scan_adjustment_metadata_is_rejected():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.api.document_lifecycle import _scan_adjusted_image

    original = Image.new("RGB", (100, 200), "white")
    evidence = SimpleNamespace(extra_metadata='{"capture":{"method":"scan"},"scan_processing":{"rotation_degrees":999}}')
    with pytest.raises(HTTPException) as error:
        _scan_adjusted_image(original, evidence)
    assert error.value.status_code == 422
    original.close()


def test_upload_retry_key_returns_same_evidence_and_rejects_changed_payload(client):
    test_client, session = client
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    payload = b"Retry-safe care note with exact content."
    form = {
        "person_id": session["person"]["id"],
        "caregiver_id": session["caregiver"]["id"],
        "evidence_type": "document",
        "capture_method": "upload",
        "capture_timestamp": "2026-11-12T06:15:00+00:00",
        "ingestion_key": "stable-upload-retry-001",
    }
    first = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=form,
        files={"file": ("care-note.txt", payload, "text/plain")},
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["evidence_id"]
    first_job_id = first.json()["document_processing_job_id"]
    assert first_job_id
    with TestSessionLocal() as db:
        first_evidence = db.query(Evidence).filter_by(id=first_id).one()
        assert first_evidence.ingestion_key == form["ingestion_key"]
        assert db.query(EvidenceUpload).filter_by(evidence_id=first_id).one()

    replay = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=form,
        files={"file": ("care-note.txt", payload, "text/plain")},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["evidence_id"] == first_id
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["document_processing_job_id"] == first_job_id

    changed = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=form,
        files={"file": ("care-note.txt", payload + b" changed", "text/plain")},
    )
    assert changed.status_code == 409, changed.text

    with TestSessionLocal() as db:
        assert db.query(Evidence).filter_by(person_id=session["person"]["id"]).count() == 1


def test_invalid_outlook_msg_is_preserved_and_reported_as_unreadable(client):
    test_client, session = client
    original = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-outlook-binary"
    evidence_id = persist_document(
        session,
        original,
        "clinic-message.msg",
        "application/vnd.ms-outlook",
    )
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 422, response.text
    assert "Outlook message could not be read" in response.json()["detail"]
    retrieved = test_client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
    assert retrieved.status_code == 200
    assert retrieved.content == original


def test_scanned_image_extraction_preserves_batch_page_number(client, monkeypatch):
    import hashlib
    import app.api.document_lifecycle as lifecycle

    test_client, session = client
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    original = _scanned_text_image()
    observed = {}

    def fake_ocr(image):
        observed["size"] = image.size
        return "Follow-up appointment on November 20."

    monkeypatch.setattr(lifecycle, "_ocr_image", fake_ocr)
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "evidence_type": "document",
            "capture_method": "scan",
            "capture_timestamp": "2026-11-12T06:20:00Z",
            "capture_page_number": "3",
            "scan_rotation_degrees": "90",
            "scan_crop_json": '{"top":10,"right":20,"bottom":10,"left":10}',
        },
        files={"file": ("scan_page_3.png", original, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    extracted = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert extracted.status_code == 201, extracted.text
    detail = test_client.get(
        f"/api/v1/documents/analyses/{extracted.json()['analysis_id']}", headers=auth
    )
    assert detail.status_code == 200, detail.text
    assert "[Page 3 OCR]" in detail.json()["extracted_text"]
    assert "[Page 1 OCR]" not in detail.json()["extracted_text"]
    with TestSessionLocal() as db:
        upload = db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).one()
        assert upload.content == original
        assert upload.sha256 == hashlib.sha256(original).hexdigest()
        saved_evidence = db.query(Evidence).filter_by(id=evidence_id).one()
        metadata = json.loads(saved_evidence.extra_metadata or "{}")
        assert metadata["capture"]["method"] == "scan"
        assert metadata["capture"]["page_number"] == 3
        assert metadata["scan_processing"]["rotation_degrees"] == 90
        assert metadata["scan_processing"]["crop_insets_percent"] == {
            "top": 10.0, "right": 20.0, "bottom": 10.0, "left": 10.0
        }
        assert metadata["scan_processing"]["client_declared_review_status"] == "not_provided"
    assert observed["size"] != Image.open(io.BytesIO(original)).size


def test_docx_extraction_includes_body_table_header_and_footer():
    import zipfile
    from app.api.document_lifecycle import _extract_docx

    document_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Discharge summary</w:t></w:r></w:p>
        <w:tbl><w:tr>
          <w:tc><w:p><w:r><w:t>Mobility</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>Needs walking frame</w:t></w:r></w:p></w:tc>
        </w:tr></w:tbl>
      </w:body>
    </w:document>"""
    header_xml = b"""<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:p><w:r><w:t>City Hospital</w:t></w:r></w:p></w:hdr>"""
    footer_xml = b"""<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:p><w:r><w:t>Confidential</w:t></w:r></w:p></w:ftr>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/header1.xml", header_xml)
        archive.writestr("word/footer1.xml", footer_xml)
    extracted = _extract_docx(buffer.getvalue())
    assert "Discharge summary" in extracted
    assert "Mobility | Needs walking frame" in extracted
    assert "City Hospital" in extracted
    assert "Confidential" in extracted


def test_legacy_doc_extraction_uses_local_bounded_antiword(monkeypatch):
    from types import SimpleNamespace
    import app.api.document_lifecycle as lifecycle

    seen = {}
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: "/usr/bin/antiword")

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["timeout"] = kwargs["timeout"]
        with open(command[-1], "rb") as source:
            seen["input"] = source.read()
        return SimpleNamespace(returncode=0, stdout=b"Discharge summary\nMobility: walking frame", stderr=b"")

    monkeypatch.setattr(lifecycle.subprocess, "run", fake_run)
    result = lifecycle._extract_legacy_doc(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy-doc")
    assert "Mobility: walking frame" in result
    assert seen["command"][:2] == ["/usr/bin/antiword", "-w"]
    assert seen["timeout"] == 15
    assert seen["input"].startswith(b"\xd0\xcf\x11\xe0")


def test_legacy_doc_extraction_fails_clearly_when_antiword_missing(monkeypatch):
    from fastapi import HTTPException
    import app.api.document_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: None)
    with pytest.raises(HTTPException) as error:
        lifecycle._extract_legacy_doc(b"legacy")
    assert error.value.status_code == 503


def test_snap_upload_is_linked_to_care_event_without_claiming_ocr(client):
    from app.models.care import CareEvent

    test_client, session = client
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    image_bytes = _scanned_text_image()
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "evidence_type": "care_photo",
            "capture_method": "snap",
            "capture_timestamp": "2026-11-12T06:40:00Z",
        },
        files={"file": ("current-moment.png", image_bytes, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]

    captured = test_client.post(
        "/api/situation",
        headers=auth,
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "raw_input": "",
            "documents": [{
                "id": "snap-local-1",
                "evidence_id": evidence_id,
                "name": "current-moment.png",
                "mime_type": "image/png",
                "extracted_text": "",
                "entry_method": "snap",
            }],
            "provenance": {
                "input_type": "document",
                "entry_method": "snap",
                "captured_at": "2026-11-12T06:40:00Z",
            },
            "timestamp": "2026-11-12T06:40:00Z",
            "care_session_id": "snap-e2e-session-1",
        },
    )
    assert captured.status_code == 200, captured.text
    body = captured.json()
    assert body["care_event_id"]
    with TestSessionLocal() as db:
        event = db.query(CareEvent).filter_by(id=body["care_event_id"]).one()
        assert evidence_id in (event.evidence_ids or [])
        evidence = db.query(Evidence).filter_by(id=evidence_id).one()
        assert evidence.type == "care_photo"
        assert evidence.source_text in (None, "")


def test_situation_rejects_snap_evidence_from_another_anonymous_session(client):
    test_client, first = client
    second_response = test_client.post("/api/v1/sessions/anonymous", json={
        "name": "Separate care recipient", "caregiver_name": "Separate caregiver"
    })
    assert second_response.status_code == 201, second_response.text
    auth = {"Authorization": f"Bearer {first['access_token']}"}
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "evidence_type": "care_photo",
            "capture_method": "snap",
        },
        files={"file": ("private-photo.png", _scanned_text_image(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    foreign = second_response.json()
    response = test_client.post(
        "/api/situation",
        headers={"Authorization": f"Bearer {foreign['access_token']}"},
        json={
            "person_id": foreign["person"]["id"],
            "caregiver_id": foreign["caregiver"]["id"],
            "raw_input": "",
            "documents": [{
                "id": "foreign-snap",
                "evidence_id": evidence_id,
                "name": "private-photo.png",
                "mime_type": "image/png",
                "extracted_text": "",
                "entry_method": "snap",
            }],
            "provenance": {"input_type": "document", "entry_method": "snap"},
        },
    )
    assert response.status_code == 400, response.text


def test_document_processing_status_does_not_leak_across_anonymous_sessions(client):
    test_client, first = client
    first_auth = {"Authorization": f"Bearer {first['access_token']}"}
    uploaded = test_client.post(
        "/api/v1/upload",
        headers=first_auth,
        data={
            "person_id": first["person"]["id"],
            "caregiver_id": first["caregiver"]["id"],
            "evidence_type": "document",
            "capture_method": "upload",
        },
        files={"file": ("private-note.txt", b"Private care note.", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]
    original_job_id = uploaded.json()["document_processing_job_id"]
    with TestSessionLocal() as db:
        stored = db.query(Evidence).filter_by(id=evidence_id).one()
        assert stored.person_id == first["person"]["id"]
        assert stored.uploaded_by_caregiver_id == first["caregiver"]["id"]
        assert stored.type == "document"

    queued = test_client.post(f"/api/v1/documents/{evidence_id}/queue", headers=first_auth, json={})
    assert queued.status_code == 202, queued.text
    assert queued.json()["job_id"] == original_job_id
    assert queued.json()["idempotent_replay"] is True

    from app.models.qwen_background_job import QwenBackgroundJob
    with TestSessionLocal() as db:
        job = db.query(QwenBackgroundJob).filter_by(id=original_job_id).one()
        job.status = "failed"
        job.attempt_count = 3
        job.error_code = "temporary_failure"
        db.commit()

    retried = test_client.post(f"/api/v1/documents/{evidence_id}/queue", headers=first_auth, json={})
    assert retried.status_code == 202, retried.text
    assert retried.json()["job_id"] == original_job_id
    assert retried.json()["status"] == "queued"
    assert retried.json()["idempotent_replay"] is False

    second = test_client.post("/api/v1/sessions/anonymous", json={
        "name": "Different care recipient",
        "caregiver_name": "Different caregiver",
    })
    assert second.status_code == 201, second.text
    second_auth = {"Authorization": f"Bearer {second.json()['access_token']}"}
    cross_record_retry = test_client.post(
        f"/api/v1/documents/{evidence_id}/queue", headers=second_auth, json={}
    )
    assert cross_record_retry.status_code == 404
    status = test_client.get("/api/v1/documents/intelligence", headers=second_auth)
    assert status.status_code == 200, status.text
    assert all(item["evidence_id"] != evidence_id for item in status.json()["processing_jobs"])
    assert all(item["source_evidence_id"] != evidence_id for item in status.json()["items"])

def test_outlook_msg_extracts_headers_and_body_from_a_real_msg_round_trip(tmp_path):
    from aspose.email_foss.msg import MapiMessage
    from app.api import document_lifecycle

    msg_path = tmp_path / "appointment.msg"
    generated = MapiMessage.create(
        "Appointment confirmation",
        "Appointment confirmed for Tuesday.",
    )
    generated.save(str(msg_path))
    generated.close()

    class Upload:
        mime_type = "application/vnd.ms-outlook"
        filename = "appointment.msg"

    extracted, method = document_lifecycle._extract(Upload(), msg_path.read_bytes())

    assert method == "server_outlook_msg"
    assert "Subject: Appointment confirmation" in extracted
    assert "Appointment confirmed for Tuesday." in extracted
    assert len(extracted) < 100_000

