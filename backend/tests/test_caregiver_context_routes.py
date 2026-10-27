from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Table, Text, Integer, UniqueConstraint
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
    if "living_care_records" not in metadata.tables:
        Table(
            "living_care_records", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False, unique=True),
            Column("record_status", String(32), nullable=False, default="active"),
            Column("schema_version", String(40), nullable=False, default="lcr.v1"),
            Column("created_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True)),
            Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        )
    if "care_record_versions" not in metadata.tables:
        Table(
            "care_record_versions", metadata,
            Column("id", String(36), primary_key=True),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("version_number", Integer, nullable=False),
            Column("change_set_id", String(36)),
            Column("record_digest", String(128)),
            Column("snapshot_payload", JSON, nullable=False),
            Column("created_at", DateTime(timezone=True)),
            Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("reason", Text),
            UniqueConstraint("care_record_id", "version_number", name="uq_care_record_version"),
        )
    if "care_record_snapshots" not in metadata.tables:
        Table(
            "care_record_snapshots", metadata,
            Column("id", String(36), primary_key=True),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("version_id", String(36), ForeignKey("care_record_versions.id")),
            Column("as_of", DateTime(timezone=True), nullable=False),
            Column("snapshot_payload", JSON, nullable=False),
            Column("record_digest", String(128)),
            Column("created_at", DateTime(timezone=True)),
            Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
        )
    if "record_provenance" not in metadata.tables:
        Table(
            "record_provenance", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id")),
            Column("source_type", String(80), nullable=False),
            Column("source_id", String(36)),
            Column("author_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("author_label", String(240)),
            Column("method", String(80), nullable=False, default="manual"),
            Column("recorded_at", DateTime(timezone=True)),
            Column("occurred_at", DateTime(timezone=True)),
            Column("effective_from", DateTime(timezone=True)),
            Column("effective_to", DateTime(timezone=True)),
            Column("reason", Text),
            Column("metadata", JSON),
            Column("created_at", DateTime(timezone=True)),
        )
    if "record_provenance_links" not in metadata.tables:
        Table(
            "record_provenance_links", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("provenance_id", String(36), ForeignKey("record_provenance.id"), nullable=False),
            Column("target_type", String(100), nullable=False),
            Column("target_id", String(36), nullable=False),
            Column("relationship", String(40), nullable=False, default="originates"),
            Column("created_at", DateTime(timezone=True)),
        )
    if "care_record_identifiers" not in metadata.tables:
        Table(
            "care_record_identifiers", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("identifier_type", String(80), nullable=False),
            Column("identifier_fingerprint", String(128), nullable=False),
            Column("masked_value", String(128)),
            Column("source_type", String(80)),
            Column("source_id", String(36)),
            Column("status", String(32), nullable=False, server_default="active"),
            Column("valid_from", DateTime(timezone=True)),
            Column("valid_to", DateTime(timezone=True)),
            Column("created_at", DateTime(timezone=True)),
            Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            UniqueConstraint("care_record_id", "identifier_type", "identifier_fingerprint"),
        )
    if "care_record_sections" not in metadata.tables:
        Table(
            "care_record_sections", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("section_key", String(100), nullable=False),
            Column("section_payload", JSON, nullable=False),
            Column("version_number", Integer, nullable=False, server_default="1"),
            Column("updated_at", DateTime(timezone=True)),
            Column("updated_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            UniqueConstraint("care_record_id", "section_key"),
        )
    if "care_record_links" not in metadata.tables:
        Table(
            "care_record_links", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("source_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("target_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("relationship", String(60), nullable=False),
            Column("created_at", DateTime(timezone=True)),
            Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            UniqueConstraint("source_record_id", "target_record_id", "relationship"),
        )
    if "care_record_status_history" not in metadata.tables:
        Table(
            "care_record_status_history", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("previous_status", String(32)),
            Column("new_status", String(32), nullable=False),
            Column("reason", Text),
            Column("changed_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
            Column("changed_at", DateTime(timezone=True)),
        )
    for target_table in ("care_facts", "care_assertions", "care_decisions", "care_events", "caregiver_observations", "changes", "situations", "evidence"):
        if target_table not in metadata.tables:
            Table(
                target_table, metadata,
                Column("id", String(36), primary_key=True),
                Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            )
    if "care_record_change_sets" not in metadata.tables:
        Table(
            "care_record_change_sets", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id")),
            Column("operation", String(60), nullable=False),
            Column("status", String(32), nullable=False, server_default="committed"),
            Column("reason", Text),
            Column("author_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
            Column("created_at", DateTime(timezone=True)),
        )
    if "care_record_change_items" not in metadata.tables:
        Table(
            "care_record_change_items", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("change_set_id", String(36), ForeignKey("care_record_change_sets.id"), nullable=False),
            Column("target_type", String(100), nullable=False),
            Column("target_id", String(36), nullable=False),
            Column("operation", String(32), nullable=False),
            Column("before_value", JSON),
            Column("after_value", JSON),
            Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
            Column("created_at", DateTime(timezone=True)),
        )
    if "care_record_retractions" not in metadata.tables:
        Table(
            "care_record_retractions", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("target_type", String(100), nullable=False),
            Column("target_id", String(36), nullable=False),
            Column("reason", Text, nullable=False),
            Column("status", String(32), nullable=False, server_default="retracted"),
            Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
            Column("retracted_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("retracted_at", DateTime(timezone=True)),
        )
    if "care_record_restorations" not in metadata.tables:
        Table(
            "care_record_restorations", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("care_record_id", String(36), ForeignKey("living_care_records.id"), nullable=False),
            Column("retraction_id", String(36), ForeignKey("care_record_retractions.id"), nullable=False),
            Column("reason", Text, nullable=False),
            Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
            Column("restored_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("restored_at", DateTime(timezone=True)),
        )
    revision_specs = (
        ("care_fact_revisions", "care_fact_id", "care_facts"),
        ("care_assertion_revisions", "assertion_id", "care_assertions"),
        ("care_decision_revisions", "decision_id", "care_decisions"),
        ("care_event_revisions", "event_id", "care_events"),
        ("care_observation_revisions", "observation_id", "caregiver_observations"),
        ("care_change_revisions", "change_id", "changes"),
        ("care_situation_revisions", "situation_id", "situations"),
        ("care_evidence_revisions", "evidence_id", "evidence"),
    )
    for revision_table, target_column, target_table in revision_specs:
        if revision_table not in metadata.tables:
            Table(
                revision_table, metadata,
                Column("id", String(36), primary_key=True),
                Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
                Column(target_column, String(36), ForeignKey(f"{target_table}.id"), nullable=False),
                Column("revision_number", Integer, nullable=False),
                Column("previous_payload", JSON),
                Column("current_payload", JSON),
                Column("revision_reason", Text),
                Column("provenance_id", String(36), ForeignKey("record_provenance.id")),
                Column("changed_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
                Column("changed_at", DateTime(timezone=True)),
                UniqueConstraint(target_column, "revision_number"),
            )
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


    # These names already exist in the ORM with an older care-network contract.
    # Extend test metadata to match the additive production migration rather than
    # replacing those legacy table definitions.
    additions = {
        "caregiver_contexts": {
            "reported_by_caregiver_id": Column("reported_by_caregiver_id", String(36), ForeignKey("caregivers.id"), nullable=True),
            "context_type": Column("context_type", String(100), nullable=True),
            "context_payload": Column("context_payload", JSON, nullable=True),
            "epistemic_status": Column("epistemic_status", String(32), nullable=True),
            "source_type": Column("source_type", String(80), nullable=True),
            "source_id": Column("source_id", String(36), nullable=True),
            "recorded_at": Column("recorded_at", DateTime(timezone=True), nullable=True),
            "effective_from": Column("effective_from", DateTime(timezone=True), nullable=True),
            "effective_to": Column("effective_to", DateTime(timezone=True), nullable=True),
            "notes": Column("notes", Text, nullable=True),
            "updated_at": Column("updated_at", DateTime(timezone=True), nullable=True),
        },
        "caregiver_perspectives": {
            "reported_by_caregiver_id": Column("reported_by_caregiver_id", String(36), ForeignKey("caregivers.id"), nullable=True),
            "topic": Column("topic", String(160), nullable=True),
            "perspective_text": Column("perspective_text", Text, nullable=True),
            "epistemic_status": Column("epistemic_status", String(32), nullable=True),
            "evidence_ids": Column("evidence_ids", JSON, nullable=True),
            "source_type": Column("source_type", String(80), nullable=True),
            "source_id": Column("source_id", String(36), nullable=True),
            "recorded_at": Column("recorded_at", DateTime(timezone=True), nullable=True),
            "effective_from": Column("effective_from", DateTime(timezone=True), nullable=True),
            "effective_to": Column("effective_to", DateTime(timezone=True), nullable=True),
            "supersedes_id": Column("supersedes_id", String(36), nullable=True),
        },
        "caregiver_observations": {
            "reported_by_caregiver_id": Column("reported_by_caregiver_id", String(36), ForeignKey("caregivers.id"), nullable=True),
            "observation_category": Column("observation_category", String(100), nullable=True),
            "description": Column("description", Text, nullable=True),
            "observed_value": Column("observed_value", JSON, nullable=True),
            "observed_at": Column("observed_at", DateTime(timezone=True), nullable=True),
            "recorded_at": Column("recorded_at", DateTime(timezone=True), nullable=True),
            "epistemic_status": Column("epistemic_status", String(32), nullable=True),
            "evidence_ids": Column("evidence_ids", JSON, nullable=True),
            "source_type": Column("source_type", String(80), nullable=True),
            "source_id": Column("source_id", String(36), nullable=True),
        },
    }
    for table_name, columns in additions.items():
        table = metadata.tables[table_name]
        if "caregiver_id" in table.c:
            table.c.caregiver_id.nullable = True
        for column_name, column in columns.items():
            if column_name not in table.c:
                table.append_column(column)


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
            "recorded_at": "2000-01-01T00:00:00+00:00",
        },
    )
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["person_id"] == first["person"]["id"]
    assert row["recorded_at"] != "2000-01-01T00:00:00+00:00"
    assert row["reported_by_caregiver_id"] == first["caregiver"]["id"]
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
        json={"observation_category": "sleep", "description": "Caregiver-reported observation."},
    )
    assert perspective.status_code == 201, perspective.text
    assert observation.status_code == 201, observation.text
    assert perspective.json()["reported_by_caregiver_id"] == first["caregiver"]["id"]
    assert observation.json()["reported_by_caregiver_id"] == first["caregiver"]["id"]
    assert perspective.json()["person_id"] == first["person"]["id"]
    assert observation.json()["person_id"] == first["person"]["id"]

def test_lcr_version_endpoint_persists_immutable_person_scoped_snapshot():
    client = TestClient(app)
    first = _session(client, "Primary caregiver")
    second = _session(client, "Other caregiver")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    record_response = client.post(
        "/api/v1/care-continuity/living-records",
        headers=first_headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    assert record_response.status_code == 201, record_response.text
    record_id = record_response.json()["id"]

    version_response = client.post(
        "/api/v1/care-continuity/record-versions",
        headers=first_headers,
        json={"care_record_id": record_id, "reason": "test snapshot"},
    )
    assert version_response.status_code == 201, version_response.text
    version = version_response.json()
    assert version["version_number"] == 1
    assert len(version["record_digest"]) == 64
    assert "record_provenance" in version["tables_captured"]

    versions = client.get("/api/v1/care-continuity/record-versions", headers=first_headers)
    snapshots = client.get("/api/v1/care-continuity/record-snapshots", headers=first_headers)
    hidden_versions = client.get("/api/v1/care-continuity/record-versions", headers=second_headers)
    assert versions.status_code == 200, versions.text
    assert snapshots.status_code == 200, snapshots.text
    assert hidden_versions.status_code == 200, hidden_versions.text
    assert len(versions.json()) == 1
    assert len(snapshots.json()) == 1
    assert hidden_versions.json() == []

    denied = client.post(
        "/api/v1/care-continuity/record-versions",
        headers=second_headers,
        json={"care_record_id": record_id},
    )
    assert denied.status_code == 404, denied.text

def test_provenance_link_cannot_reference_another_persons_provenance():
    client = TestClient(app)
    first = _session(client, "First caregiver")
    second = _session(client, "Second caregiver")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}

    record = client.post(
        "/api/v1/care-continuity/living-records",
        headers=first_headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    assert record.status_code == 201, record.text

    source = client.post(
        "/api/v1/care-continuity/record-provenance",
        headers=second_headers,
        json={"source_type": "caregiver_report", "reason": "isolated test source"},
    )
    assert source.status_code == 201, source.text

    denied = client.post(
        "/api/v1/care-continuity/record-provenance-links",
        headers=first_headers,
        json={
            "provenance_id": source.json()["id"],
            "target_type": "living_care_record",
            "target_id": record.json()["id"],
            "relationship": "supports",
        },
    )
    assert denied.status_code == 404, denied.text
