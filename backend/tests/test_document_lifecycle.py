import pytest
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


def test_extract_rejects_scanned_image_without_claiming_ocr(client):
    test_client, session = client
    evidence_id = persist_document(session, b"\x89PNG\r\n\x1a\ninvalid", "scan.png", "image/png")
    auth = {"Authorization": f"Bearer {session['access_token']}"}
    response = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert response.status_code == 415, response.text


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
        },
        files={"file": ("uploaded-care-note.txt", b"Uploaded through the real endpoint.", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]

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

    route = next(
        route for route in test_client.app.routes
        if getattr(route, "path", "") == "/api/v1/documents/{evidence_id}/extract"
    )
    original_verify = route.endpoint.__globals__["_verified_content"]
    calls = 0

    def fail_on_retry(upload):
        nonlocal calls
        calls += 1
        if calls >= 1:
            raise HTTPException(status_code=500, detail="Stored evidence failed its integrity check")
        return original_verify(upload)

    monkeypatch.setitem(route.endpoint.__globals__, "_verified_content", fail_on_retry)
    retry = test_client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth)
    assert retry.status_code == 500, retry.text
    assert retry.json()["detail"] == "Stored evidence failed its integrity check"
