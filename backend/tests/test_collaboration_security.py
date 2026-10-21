from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.auth import AuthAccount
from app.models.care import Caregiver, Person
from app.models.collaboration import CommunityPost, ProfessionalAccessGrant

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_records():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    db = TestingSessionLocal()
    person = Person(id="person-collab-test", name="Care Collaboration Test")
    caregiver = Caregiver(
        id="caregiver-collab-test",
        name="Family Caregiver",
        email="family@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    professional_person = Person(id="person-professional-test", name="Professional Profile")
    professional = Caregiver(
        id="professional-collab-test",
        name="Professional",
        email="professional@example.invalid",
        relationship="Professional",
        person_id=professional_person.id,
    )
    db.add_all([person, caregiver, professional_person, professional])
    db.commit()
    db.close()
    return person_id, caregiver_id, professional_id


def teardown_records():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_community_post_cannot_claim_a_verified_role_or_publish_immediately():
    _, caregiver_id, _ = setup_records()
    try:
        client = TestClient(app)
        forged = client.post(
            "/api/v1/community",
            json={
                "caregiver_id": caregiver_id,
                "author_type": "team",
                "author_label": "SolenOS Team",
                "content": "A forged team post",
            },
        )
        assert forged.status_code == 403

        created = client.post(
            "/api/v1/community",
            json={
                "caregiver_id": caregiver_id,
                "author_type": "peer_caregiver",
                "author_label": "Forged public identity",
                "content": "A caregiver experience awaiting review",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["author_type"] == "peer_caregiver"
        assert created.json()["author_label"] == "Anonymous caregiver"
        assert created.json()["moderation_status"] == "pending"

        db = TestingSessionLocal()
        row = db.query(CommunityPost).filter_by(id=created.json()["id"]).one()
        assert row.moderation_status == "pending"
        db.close()
    finally:
        teardown_records()


def test_professional_grant_rejects_caregiver_without_verified_professional_account():
    person_id, caregiver_id, professional_id = setup_records()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/professional/access-grants",
            json={
                "professional_caregiver_id": professional_id,
                "person_id": person_id,
                "granted_by_caregiver_id": caregiver_id,
                "status": "active",
            },
        )
        assert response.status_code == 403
        assert "verified professional account" in response.json()["detail"].lower()
    finally:
        teardown_records()


def test_professional_grant_requires_active_family_caregiver_and_verified_professional():
    person_id, caregiver_id, professional_id = setup_records()
    db = TestingSessionLocal()
    db.add_all([
        AuthAccount(
            id="account-family-test",
            caregiver_id=caregiver_id,
            email="family@example.invalid",
            password_hash="test-only-hash",
            role="caregiver",
            is_active=True,
        ),
        AuthAccount(
            id="account-professional-test",
            caregiver_id=professional_id,
            email="professional@example.invalid",
            password_hash="test-only-hash",
            role="professional",
            is_active=True,
        ),
    ])
    db.commit()
    db.close()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/professional/access-grants",
            json={
                "professional_caregiver_id": professional_id,
                "person_id": person_id,
                "granted_by_caregiver_id": caregiver_id,
                "status": "active",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "active"
        db = TestingSessionLocal()
        grant = db.query(ProfessionalAccessGrant).filter_by(
            professional_caregiver_id=professional_id, person_id=person_id
        ).one()
        assert grant.granted_by_caregiver_id == caregiver_id
        db.close()
    finally:
        teardown_records()
