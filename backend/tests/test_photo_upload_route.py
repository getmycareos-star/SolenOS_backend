import hashlib
from io import BytesIO
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.authentication import current_account
from app.core.database import Base, get_db
from app.main import app
from app.models.auth_identity import AuthAccount, AccountPersonAccess, CaregiverAccountLink
from app.models.capture import EvidenceFile
from app.models.care import Caregiver, Evidence, Person


def test_photo_upload_persists_private_file_and_download_is_scoped():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = testing_session()
    try:
        person = Person(id="photo-route-person", name="Photo Route Person")
        caregiver = Caregiver(
            id="photo-route-caregiver",
            name="Photo Route Caregiver",
            email="photo-route@example.invalid",
            relationship="Family",
            person_id=person.id,
            timezone="UTC",
        )
        account = AuthAccount(
            id="photo-route-account",
            email="photo-route@example.invalid",
            password_hash="test-only",
            is_active=True,
        )
        db.add_all([person, caregiver, account])
        db.flush()
        db.add(
            AccountPersonAccess(
                account_id=account.id,
                person_id=person.id,
                role="owner",
                permissions=["read", "write", "admin"],
                is_active=True,
                granted_by_account_id=account.id,
            )
        )
        db.add(
            CaregiverAccountLink(
                account_id=account.id,
                caregiver_id=caregiver.id,
                person_id=person.id,
                linked_by_account_id=account.id,
                is_active=True,
            )
        )
        db.commit()
        account_stub = SimpleNamespace(id=account.id)
    finally:
        db.close()

    def override_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_account] = lambda: account_stub
    client = TestClient(app)

    image_buffer = BytesIO()
    Image.new("RGB", (2, 2), color=(180, 40, 20)).save(image_buffer, format="JPEG")
    image_bytes = image_buffer.getvalue()

    try:
        response = client.post(
            "/api/v1/upload",
            data={
                "person_id": "photo-route-person",
                "caregiver_id": "photo-route-caregiver",
            },
            files={"file": ("medication.jpg", image_bytes, "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        evidence_id = response.json()["evidence_id"]

        db = testing_session()
        try:
            evidence = db.query(Evidence).filter_by(id=evidence_id).one()
            stored_file = db.query(EvidenceFile).filter_by(evidence_id=evidence_id).one()
            assert evidence.person_id == "photo-route-person"
            assert evidence.uploaded_by_caregiver_id == "photo-route-caregiver"
            assert evidence.source_type == "image_upload"
            assert evidence.evidence_status == "uploaded"
            assert stored_file.uploaded_by_account_id == "photo-route-account"
            assert stored_file.content == image_bytes
            assert stored_file.sha256 == hashlib.sha256(image_bytes).hexdigest()
        finally:
            db.close()

        download = client.get(f"/api/v1/upload/{evidence_id}")
        assert download.status_code == 200, download.text
        assert download.content == image_bytes
        assert download.headers["content-type"] == "image/jpeg"
        assert download.headers["cache-control"] == "private, no-store"
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
