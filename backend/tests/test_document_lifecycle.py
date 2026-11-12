import io

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
from app.models.collaboration import EvidenceUpload

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
    retrieved = test_client.get(
        f"/api/v1/documents/analyses/{response.json()['analysis_id']}", headers=auth
    )
    assert retrieved.status_code == 200, retrieved.text
    extracted = retrieved.json()["extracted_text"]
    assert "[Page 1 OCR]" in extracted
    assert "Patient walked 20 metres" in extracted


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
    monkeypatch.setattr(document_lifecycle.pytesseract, "image_to_string", lambda *args, **kwargs: "")
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

    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 422, response.text
    assert "No readable text" in response.json()["detail"]

    original_download = test_client.get(f"/api/v1/upload/{evidence_id}", headers=auth)
    assert original_download.status_code == 200, original_download.text
    assert original_download.content == original_bytes
    with TestSessionLocal() as db:
        assert db.query(EvidenceUpload).filter_by(evidence_id=evidence_id).count() == 1
        assert db.query(CaptureAnalysis).filter_by(source_evidence_id=evidence_id).count() == 0



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
            "Needs a walking frame.",
        ),
        (
            "clinic-email.eml",
            "message/rfc822",
            b"From: clinic@example.org\\r\\nTo: family@example.org\\r\\nSubject: Follow-up\\r\\nMIME-Version: 1.0\\r\\nContent-Type: text/plain; charset=utf-8\\r\\n\\r\\nPlease arrange a review next Tuesday.",
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


def test_legacy_doc_is_preserved_but_reports_explicit_unsupported_extraction(client):
    test_client, session = client
    evidence_id = persist_document(
        session,
        b"\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1legacy-word-binary",
        "legacy.doc",
        "application/msword",
    )
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 415, response.text
    assert ".doc files are preserved" in response.json()["detail"]


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

    replay = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=form,
        files={"file": ("care-note.txt", payload, "text/plain")},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["evidence_id"] == first_id
    assert replay.json()["idempotent_replay"] is True

    changed = test_client.post(
        "/api/v1/upload",
        headers=auth,
        data=form,
        files={"file": ("care-note.txt", payload + b" changed", "text/plain")},
    )
    assert changed.status_code == 409, changed.text

    with TestSessionLocal() as db:
        assert db.query(Evidence).filter_by(person_id=session["person"]["id"]).count() == 1


def test_outlook_msg_is_preserved_but_reports_explicit_unsupported_extraction(client):
    test_client, session = client
    evidence_id = persist_document(
        session,
        b"\\xd0\\xcf\\x11\\xe0\\xa1\\xb1\\x1a\\xe1legacy-outlook-binary",
        "clinic-message.msg",
        "application/vnd.ms-outlook",
    )
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 415, response.text
    assert ".msg files are preserved" in response.json()["detail"]


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
        files={"file": ("scan_page_3.jpg", original, "image/jpeg")},
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
    assert observed["size"] != Image.open(io.BytesIO(original)).size
