from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Table, Text
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app
import app.main as main_module


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _ensure_test_tables():
    metadata = Base.metadata
    if "caregiver_contexts" not in metadata.tables:
        Table(
            "caregiver_contexts", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("caregiver_id", String(36), ForeignKey("caregivers.id"), nullable=False),
            Column("context_type", String(100), nullable=False),
            Column("context_payload", JSON, nullable=False),
            Column("epistemic_status", String(32), nullable=False, default="reported"),
            Column("source_type", String(80)),
            Column("source_id", String(36)),
            Column("recorded_at", DateTime(timezone=True)),
            Column("effective_from", DateTime(timezone=True)),
            Column("effective_to", DateTime(timezone=True)),
            Column("notes", Text),
            Column("created_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True)),
        )
    if "caregiver_perspectives" not in metadata.tables:
        Table(
            "caregiver_perspectives", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("caregiver_id", String(36), ForeignKey("caregivers.id"), nullable=False),
            Column("topic", String(160), nullable=False),
            Column("perspective_text", Text, nullable=False),
            Column("epistemic_status", String(32), nullable=False, default="reported"),
            Column("evidence_ids", JSON, nullable=False, default=list),
            Column("source_type", String(80)),
            Column("source_id", String(36)),
            Column("recorded_at", DateTime(timezone=True)),
            Column("effective_from", DateTime(timezone=True)),
            Column("effective_to", DateTime(timezone=True)),
            Column("supersedes_id", String(36)),
            Column("created_at", DateTime(timezone=True)),
        )
    if "caregiver_observations" not in metadata.tables:
        Table(
            "caregiver_observations", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("caregiver_id", String(36), ForeignKey("caregivers.id"), nullable=False),
            Column("observation_type", String(100), nullable=False),
            Column("description", Text, nullable=False),
            Column("observed_value", JSON),
            Column("observed_at", DateTime(timezone=True)),
            Column("recorded_at", DateTime(timezone=True)),
            Column("epistemic_status", String(32), nullable=False, default="reported"),
            Column("evidence_ids", JSON, nullable=False, default=list),
            Column("source_type", String(80)),
            Column("source_id", String(36)),
            Column("created_at", DateTime(timezone=True)),
        )


def _override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_function():
    _ensure_test_tables()
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = _override_db
    main_module.SessionLocal = TestingSessionLocal


def teardown_function():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def _session(client, caregiver_name):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": caregiver_name},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_caregiver_context_api_binds_identity_to_bearer_session():
    client = TestClient(app)
    first = _session(client, "Caregiver One")
    second = _session(client, "Caregiver Two")

    created = client.post(
        "/api/v1/care-continuity/caregiver-contexts",
        headers={"Authorization": f"Bearer {first['access_token']}"},
        json={
            "person_id": second["person"]["id"],
            "caregiver_id": second["caregiver"]["id"],
            "context_type": "routine",
            "context_payload": {"reported": "prefers morning visits"},
            "epistemic_status": "reported",
        },
    )
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["person_id"] == first["person"]["id"]
    assert row["caregiver_id"] == first["caregiver"]["id"]
    assert row["context_payload"] == {"reported": "prefers morning visits"}

    own = client.get(
        "/api/v1/care-continuity/caregiver-contexts",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    other = client.get(
        "/api/v1/care-continuity/caregiver-contexts",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert own.status_code == 200, own.text
    assert other.status_code == 200, other.text
    assert [item["id"] for item in own.json()] == [row["id"]]
    assert other.json() == []


def test_caregiver_perspective_and_observation_routes_are_writable_and_scoped():
    client = TestClient(app)
    first = _session(client, "Family caregiver")
    headers = {"Authorization": f"Bearer {first['access_token']}"}

    perspective = client.post(
        "/api/v1/care-continuity/caregiver-perspectives",
        headers=headers,
        json={"topic": "sleep", "perspective_text": "Caregiver reports a change in sleep routine."},
    )
    observation = client.post(
        "/api/v1/care-continuity/caregiver-observations",
        headers=headers,
        json={"observation_type": "sleep", "description": "Caregiver-reported observation."},
    )
    assert perspective.status_code == 201, perspective.text
    assert observation.status_code == 201, observation.text
    assert perspective.json()["caregiver_id"] == first["caregiver"]["id"]
    assert observation.json()["caregiver_id"] == first["caregiver"]["id"]
    assert perspective.json()["person_id"] == first["person"]["id"]
    assert observation.json()["person_id"] == first["person"]["id"]
