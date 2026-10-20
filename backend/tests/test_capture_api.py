"""Integration tests for authenticated daily capture and uploaded image evidence."""
import base64
import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.auth_identity import AccountPersonAccess, AuthAccount, AuthSession, CaregiverAccountLink
from app.models.care import CareEvent as CareEventModel, Caregiver as CaregiverModel, Evidence as EvidenceModel, Person
from app.models.capture import DailyCapture, EvidenceFile
from app.models.change import Situation as SituationModel
from app.services.auth_service import create_session


@pytest.fixture
def capture_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [
        Person.__table__,
        CaregiverModel.__table__,
        EvidenceModel.__table__,
        SituationModel.__table__,
        CareEventModel.__table__,
        AuthAccount.__table__,
        AuthSession.__table__,
        AccountPersonAccess.__table__,
        CaregiverAccountLink.__table__,
        DailyCapture.__table__,
        EvidenceFile.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    db = TestingSession()
    account = AuthAccount(email="capture-owner@example.test", password_hash="test-only-hash", display_name="Capture Owner")
    person = Person(name="Care recipient")
    db.add_all([account, person])
    db.flush()
    caregiver = CaregiverModel(
        name="Authorized caregiver",
        email="capture-caregiver@example.test",
        relationship="family",
        person_id=person.id,
        timezone="UTC",
    )
    db.add(caregiver)
    db.flush()
    grant = AccountPersonAccess(
        account_id=account.id,
        person_id=person.id,
        role="owner",
        permissions=["read", "write", "admin"],
        is_active=True,
        granted_by_account_id=account.id,
    )
    link = CaregiverAccountLink(
        account_id=account.id,
        caregiver_id=caregiver.id,
        person_id=person.id,
        linked_by_account_id=account.id,
        is_active=True,
    )
    db.add_all([grant, link])
    db.commit()
    token, _session = create_session(db, account)
    context = {"person_id": person.id, "caregiver_id": caregiver.id, "account_id": account.id, "token": token}
    db.close()

    def override_get_db():
        request_db = TestingSession()
        try:
            yield request_db
        finally:
            request_db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client, context, TestingSession
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()
        Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
        engine.dispose()


def test_daily_capture_requires_authentication(capture_client):
    client, context, _session_factory = capture_client
    response = client.post(
        "/api/v1/daily/log",
        json={"person_id": context["person_id"], "caregiver_id": context["caregiver_id"], "notes": "Test note"},
    )
    assert response.status_code == 401, response.text


def test_daily_capture_persists_payload_and_lcr_event(capture_client):
    client, context, session_factory = capture_client
    response = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {context['token']}"},
        json={
            "person_id": context["person_id"],
            "caregiver_id": context["caregiver_id"],
            "medications": [{
                "medication_name": "Medication recorded by caregiver",
                "status": "given",
                "dose": "5 mg",
                "scheduled_for": "2026-10-20T08:00:00Z",
                "administered_at": "2026-10-20T08:05:00Z",
                "note": "Recorded, not a recommendation",
            }],
            "vitals": [{
                "vital_type": "heart_rate",
                "value": 72,
                "measured_at": "2026-10-20T08:10:00Z",
                "unit": "bpm",
            }],
            "scales": [{
                "axis": "mood",
                "score": 3,
                "observed_at": "2026-10-20T08:15:00Z",
                "note": "Caregiver-reported score",
            }],
            "notes": "Caregiver's own daily note.",
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["success"] is True
    assert result["daily_capture_id"]
    assert result["care_event_id"]

    db = session_factory()
    try:
        capture = db.query(DailyCapture).filter(DailyCapture.id == result["daily_capture_id"]).one()
        assert capture.person_id == context["person_id"]
        assert capture.caregiver_id == context["caregiver_id"]
        assert capture.recorded_by_account_id == context["account_id"]
        assert capture.medications[0]["medication_name"] == "Medication recorded by caregiver"
        assert capture.vitals[0]["value"] == 72
        assert capture.scales[0]["score"] == 3
        assert capture.notes == "Caregiver's own daily note."
        event = db.query(CareEventModel).filter(CareEventModel.id == result["care_event_id"]).one()
        assert event.person_id == context["person_id"]
        assert event.created_by_caregiver_id == context["caregiver_id"]
        assert f"daily_capture_id:{capture.id}" in event.tags
    finally:
        db.close()


def test_daily_capture_rejects_foreign_record_photo_evidence(capture_client):
    client, context, session_factory = capture_client
    db = session_factory()
    try:
        foreign_person = Person(name="Unrelated care recipient")
        db.add(foreign_person)
        db.flush()
        foreign_caregiver = CaregiverModel(
            name="Unrelated caregiver",
            email="foreign-caregiver@example.test",
            relationship="family",
            person_id=foreign_person.id,
            timezone="UTC",
        )
        db.add(foreign_caregiver)
        db.flush()
        foreign_evidence = EvidenceModel(
            person_id=foreign_person.id,
            type="image",
            source_text="Foreign evidence",
            uploaded_by_caregiver_id=foreign_caregiver.id,
        )
        db.add(foreign_evidence)
        db.commit()
        foreign_evidence_id = foreign_evidence.id
    finally:
        db.close()

    response = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {context['token']}"},
        json={
            "person_id": context["person_id"],
            "caregiver_id": context["caregiver_id"],
            "medications": [{
                "medication_name": "Medication entry",
                "status": "given",
                "photo_evidence_id": foreign_evidence_id,
            }],
        },
    )
    assert response.status_code == 400, response.text


def test_image_upload_stores_bytes_and_scoped_download(capture_client):
    client, context, session_factory = capture_client
    png_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")
    response = client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {context['token']}"},
        data={"person_id": context["person_id"], "caregiver_id": context["caregiver_id"]},
        files={"file": ("care-photo.png", png_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["text_extracted"] is False
    assert "not been performed" in result["message"]

    db = session_factory()
    try:
        evidence = db.query(EvidenceModel).filter(EvidenceModel.id == result["evidence_id"]).one()
        stored = db.query(EvidenceFile).filter(EvidenceFile.evidence_id == evidence.id).one()
        assert evidence.person_id == context["person_id"]
        assert evidence.uploaded_by_caregiver_id == context["caregiver_id"]
        assert stored.content == png_bytes
        assert stored.sha256 == hashlib.sha256(png_bytes).hexdigest()
        assert stored.size_bytes == len(png_bytes)
    finally:
        db.close()

    download = client.get(
        f"/api/v1/upload/{result['evidence_id']}",
        headers={"Authorization": f"Bearer {context['token']}"},
    )
    assert download.status_code == 200
    assert download.content == png_bytes
    assert download.headers["content-type"].startswith("image/png")


def test_image_upload_rejects_mismatched_file_signature(capture_client):
    client, context, _session_factory = capture_client
    response = client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {context['token']}"},
        data={"person_id": context["person_id"], "caregiver_id": context["caregiver_id"]},
        files={"file": ("not-really.png", b"not a png", "image/png")},
    )
    assert response.status_code == 415, response.text


def test_image_upload_rejects_unlinked_caregiver_identity(capture_client):
    client, context, session_factory = capture_client
    db = session_factory()
    try:
        unlinked = CaregiverModel(
            name="Unlinked caregiver",
            email="unlinked-caregiver@example.test",
            relationship="family",
            person_id=context["person_id"],
            timezone="UTC",
        )
        db.add(unlinked)
        db.commit()
        unlinked_id = unlinked.id
    finally:
        db.close()

    png_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")
    response = client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {context['token']}"},
        data={"person_id": context["person_id"], "caregiver_id": unlinked_id},
        files={"file": ("care-photo.png", png_bytes, "image/png")},
    )
    assert response.status_code == 403, response.text


def test_uploaded_image_download_denies_account_without_care_access(capture_client):
    client, context, session_factory = capture_client
    png_bytes = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC")
    uploaded = client.post(
        "/api/v1/upload",
        headers={"Authorization": f"Bearer {context['token']}"},
        data={"person_id": context["person_id"], "caregiver_id": context["caregiver_id"]},
        files={"file": ("care-photo.png", png_bytes, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text

    db = session_factory()
    try:
        unrelated = AuthAccount(email="unrelated-account@example.test", password_hash="test-only-hash")
        db.add(unrelated)
        db.commit()
        unrelated_token, _session = create_session(db, unrelated)
    finally:
        db.close()

    download = client.get(
        f"/api/v1/upload/{uploaded.json()['evidence_id']}",
        headers={"Authorization": f"Bearer {unrelated_token}"},
    )
    assert download.status_code == 403, download.text
