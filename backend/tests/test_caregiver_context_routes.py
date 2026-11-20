from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Table, Text, Integer, UniqueConstraint, func
from sqlalchemy import create_engine, select
import os
import tempfile
from datetime import datetime, timezone
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.core.database import Base, get_db
from app.main import app
import app.main as main_module
from app.models.care import CareEvent, Evidence


_test_db_file = tempfile.NamedTemporaryFile(prefix="solenos-care-continuity-", suffix=".sqlite", delete=False)
_test_db_file.close()
_test_db_path = _test_db_file.name
engine = create_engine(
    f"sqlite:///{_test_db_path}",
    connect_args={"check_same_thread": False},
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
    if "care_facts" not in metadata.tables:
        Table(
            "care_facts", metadata,
            Column("id", String(36), primary_key=True),
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("subject_type", String(80), nullable=False),
            Column("subject_id", String(36)),
            Column("fact_type", String(100), nullable=False),
            Column("fact_value", Text, nullable=False),
            Column("epistemic_status", String(32), nullable=False, server_default="reported"),
            Column("effective_at", DateTime(timezone=True)),
            Column("recorded_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
            Column("superseded_by_fact_id", String(36), ForeignKey("care_facts.id")),
            Column("created_by_caregiver_id", String(36), ForeignKey("caregivers.id")),
            Column("review_notes", Text),
            Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
            Column("updated_at", DateTime(timezone=True)),
        )
    if "care_fact_evidence" not in metadata.tables:
        Table(
            "care_fact_evidence", metadata,
            Column("person_id", String(36), ForeignKey("persons.id"), nullable=False),
            Column("fact_id", String(36), ForeignKey("care_facts.id"), nullable=False),
            Column("evidence_id", String(36), ForeignKey("evidence.id"), nullable=False),
            Column("evidence_role", String(24), nullable=False, default="supports"),
            Column("source_span", Text),
            Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
            UniqueConstraint("fact_id", "evidence_id", "evidence_role", name="uq_test_care_fact_evidence_link"),
        )
    for target_table in ("care_assertions", "care_decisions", "care_events", "caregiver_observations", "changes", "situations", "evidence"):
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
    engine.dispose()
    try:
        os.unlink(_test_db_path)
    except FileNotFoundError:
        pass


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
    assert observation.json()["observation_type"] == "SECOND_HAND"

    direct = client.post(
        "/api/v1/care-continuity/caregiver-observations",
        headers=headers,
        json={"observation_category": "mobility", "description": "Caregiver directly observed the transfer.", "observation_type": "direct", "observed_at": "2026-10-28T09:00:00Z"},
    )
    assert direct.status_code == 201, direct.text
    assert direct.json()["observation_type"] == "DIRECT"

    context = client.post(
        "/api/v1/care-continuity/caregiver-contexts",
        headers=headers,
        json={"context_type": "daily_routine", "context_payload": {"preferred_time": "morning"}},
    )
    assert context.status_code == 201, context.text

    db = TestingSessionLocal()
    try:
        revisions = Base.metadata.tables["care_observation_revisions"]
        saved_revisions = db.execute(
            select(revisions).where(
                revisions.c.person_id == first["person"]["id"],
                revisions.c.observation_id.in_([observation.json()["id"], direct.json()["id"]]),
            )
        ).mappings().all()
        assert len(saved_revisions) == 2
        assert {row["observation_id"] for row in saved_revisions} == {
            observation.json()["id"], direct.json()["id"],
        }
        assert all(row["revision_number"] == 1 for row in saved_revisions)
        assert all(row["previous_payload"] is None for row in saved_revisions)
        assert all(row["current_payload"] for row in saved_revisions)

        unified = Base.metadata.tables["care_state_revisions"]
        unified_rows = db.execute(
            select(unified).where(
                unified.c.person_id == first["person"]["id"],
                unified.c.target_type.in_([
                    "caregiver_observations", "caregiver_perspectives", "caregiver_contexts",
                ]),
            )
        ).mappings().all()
        unified_by_target = {(row["target_type"], row["target_id"]): row for row in unified_rows}
        expected_targets = {
            ("caregiver_observations", observation.json()["id"]),
            ("caregiver_observations", direct.json()["id"]),
            ("caregiver_perspectives", perspective.json()["id"]),
            ("caregiver_contexts", context.json()["id"]),
        }
        assert expected_targets.issubset(set(unified_by_target))
        for target in expected_targets:
            revision = unified_by_target[target]
            assert revision["revision_number"] == 1
            assert revision["change_kind"] == "created"
            assert revision["before_state"] is None
            assert revision["after_state"]
    finally:
        db.close()

    invalid = client.post(
        "/api/v1/care-continuity/caregiver-observations",
        headers=headers,
        json={"observation_category": "mobility", "description": "Unsupported basis.", "observation_type": "guess"},
    )
    assert invalid.status_code == 422

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
    db = TestingSessionLocal()
    record_table = Base.metadata.tables["living_care_records"]
    persisted_record = db.execute(
        select(record_table.c.id, record_table.c.person_id).where(record_table.c.id == record_id)
    ).first()
    assert persisted_record is not None, "Living Care Record insert must persist across request sessions"
    assert persisted_record.person_id == first["person"]["id"]
    db.close()

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

    historical = client.get(
        "/api/v1/care-continuity/record-snapshots/as-of",
        headers=first_headers,
        params={"as_of": version["captured_at"]},
    )
    assert historical.status_code == 200, historical.text
    historical_body = historical.json()
    assert historical_body["snapshot_id"] == version["snapshot_id"]
    assert historical_body["integrity_status"] == "verified"
    assert historical_body["coverage_status"] == "saved_snapshot_only"
    assert historical_body["state"]["person_id"] == first["person"]["id"]

    hidden_historical = client.get(
        "/api/v1/care-continuity/record-snapshots/as-of",
        headers=second_headers,
        params={"as_of": version["captured_at"]},
    )
    assert hidden_historical.status_code == 404, hidden_historical.text

    no_earlier_snapshot = client.get(
        "/api/v1/care-continuity/record-snapshots/as-of",
        headers=first_headers,
        params={"as_of": "2020-01-01T00:00:00Z"},
    )
    assert no_earlier_snapshot.status_code == 404, no_earlier_snapshot.text

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

def test_change_item_appends_same_person_fact_revision():
    client = TestClient(app)
    session = _session(client, "Revision caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    record = client.post(
        "/api/v1/care-continuity/living-records",
        headers=headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    assert record.status_code == 201, record.text
    fact = client.post(
        "/api/v1/care-continuity/facts",
        headers=headers,
        json={"subject_type": "care_recipient", "subject_id": record.json()["id"],
              "fact_type": "care_need", "fact_value": "needs support"},
    )
    assert fact.status_code == 201, fact.text

    evidence_id = "revision-evidence-source"
    db = TestingSessionLocal()
    try:
        db.add(Evidence(
            id=evidence_id,
            person_id=session["person"]["id"],
            type="caregiver_report",
            source_text="Family caregiver report supports this recorded care need.",
            uploaded_by_caregiver_id=session["caregiver"]["id"],
            evidence_status="reported",
            source_type="caregiver_report",
        ))
        db.commit()
    finally:
        db.close()

    provenance = client.post(
        "/api/v1/care-continuity/record-provenance",
        headers=headers,
        json={
            "source_type": "evidence",
            "source_id": evidence_id,
            "author_label": "Family caregiver",
            "method": "manual",
            "reason": "Source for the care-fact revision",
        },
    )
    assert provenance.status_code == 201, provenance.text

    change_set = client.post(
        "/api/v1/care-continuity/record-change-sets",
        headers=headers,
        json={
            "care_record_id": record.json()["id"],
            "operation": "update",
            "reason": "Correction review",
            "provenance_id": provenance.json()["id"],
        },
    )
    assert change_set.status_code == 201, change_set.text

    first = client.post(
        "/api/v1/care-continuity/record-change-items",
        headers=headers,
        json={"change_set_id": change_set.json()["id"], "target_type": "care_fact",
              "target_id": fact.json()["id"], "operation": "update",
              "before_value": {"fact_value": "needs support"},
              "after_value": {"fact_value": "needs daily support"},
              "provenance_id": provenance.json()["id"]},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/care-continuity/record-change-items",
        headers=headers,
        json={"change_set_id": change_set.json()["id"], "target_type": "care_fact",
              "target_id": fact.json()["id"], "operation": "update",
              "before_value": {"fact_value": "needs daily support"},
              "after_value": {"fact_value": "needs daily support with supervision"},
              "provenance_id": provenance.json()["id"]},
    )
    assert second.status_code == 201, second.text
    revisions = client.get("/api/v1/care-continuity/fact-revisions", headers=headers)
    assert revisions.status_code == 200, revisions.text
    assert [row["revision_number"] for row in revisions.json()] == [3, 2, 1]
    assert revisions.json()[0]["current_payload"]["fact_value"] == "needs daily support with supervision"
    assert revisions.json()[2]["current_payload"]["fact_value"] == "needs support"
    timeline = client.get("/api/v1/care-continuity/record-revisions", headers=headers)
    assert timeline.status_code == 200, timeline.text
    assert len(timeline.json()) == 3
    assert timeline.json()[0]["revision_type"] == "care_fact_revisions"

    reconstructed = client.get(
        "/api/v1/care-continuity/state-reconstruction/as-of",
        headers=headers,
        params={"as_of": datetime.now(timezone.utc).isoformat()},
    )
    assert reconstructed.status_code == 200, reconstructed.text
    fact_state = next(
        item for item in reconstructed.json()["items"]
        if item["target_type"] == "care_facts" and item["target_id"] == fact.json()["id"]
    )
    assert fact_state["revision_number"] == 3
    assert fact_state["state"]["subject_type"] == "care_recipient"
    assert fact_state["state"]["fact_type"] == "care_need"
    assert fact_state["state"]["fact_value"] == "needs daily support with supervision"
    assert fact_state["evidence_ids"] == [evidence_id]


def test_lcr_sections_upsert_and_record_status_history_is_persisted():
    client = TestClient(app)
    session = _session(client, "Section caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    record = client.post(
        "/api/v1/care-continuity/living-records",
        headers=headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    assert record.status_code == 201, record.text
    record_id = record.json()["id"]

    first = client.post(
        "/api/v1/care-continuity/record-sections",
        headers=headers,
        json={"care_record_id": record_id, "section_key": "current-situation",
              "section_payload": {"summary": "Needs assistance"}},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/api/v1/care-continuity/record-sections",
        headers=headers,
        json={"care_record_id": record_id, "section_key": "current-situation",
              "section_payload": {"summary": "Needs daily assistance"}},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version_number"] == 2
    history = client.get("/api/v1/care-continuity/record-status-history", headers=headers)
    assert history.status_code == 200, history.text
    assert len(history.json()) == 1
    assert history.json()[0]["new_status"] == "active"


def test_identifier_fingerprint_and_retraction_restoration_lifecycle():
    client = TestClient(app)
    session = _session(client, "Record governance caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    record = client.post(
        "/api/v1/care-continuity/living-records",
        headers=headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    assert record.status_code == 201, record.text
    record_id = record.json()["id"]

    invalid_identifier = client.post(
        "/api/v1/care-continuity/record-identifiers",
        headers=headers,
        json={"care_record_id": record_id, "identifier_type": "external_reference",
              "identifier_fingerprint": "raw-id-must-not-be-stored"},
    )
    assert invalid_identifier.status_code == 422, invalid_identifier.text
    identifier = client.post(
        "/api/v1/care-continuity/record-identifiers",
        headers=headers,
        json={"care_record_id": record_id, "identifier_type": "external_reference",
              "identifier_fingerprint": "a" * 64, "masked_value": "…1234"},
    )
    assert identifier.status_code == 201, identifier.text

    retraction = client.post(
        "/api/v1/care-continuity/record-retractions",
        headers=headers,
        json={"care_record_id": record_id, "target_type": "living_care_record",
              "target_id": record_id, "reason": "Test retraction"},
    )
    assert retraction.status_code == 201, retraction.text
    restoration = client.post(
        "/api/v1/care-continuity/record-restorations",
        headers=headers,
        json={"care_record_id": record_id, "retraction_id": retraction.json()["id"],
              "reason": "Restored after review"},
    )
    assert restoration.status_code == 201, restoration.text
    listed = client.get("/api/v1/care-continuity/record-retractions", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["status"] == "restored"


def test_lcr_record_link_rejects_cross_person_target():
    client = TestClient(app)
    first = _session(client, "Record link first caregiver")
    second = _session(client, "Record link second caregiver")
    first_headers = {"Authorization": f"Bearer {first['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second['access_token']}"}
    first_record = client.post(
        "/api/v1/care-continuity/living-records",
        headers=first_headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    second_record = client.post(
        "/api/v1/care-continuity/living-records",
        headers=second_headers,
        json={"record_status": "active", "schema_version": "lcr.v1"},
    )
    assert first_record.status_code == 201, first_record.text
    assert second_record.status_code == 201, second_record.text
    denied = client.post(
        "/api/v1/care-continuity/record-links",
        headers=first_headers,
        json={"source_record_id": first_record.json()["id"],
              "target_record_id": second_record.json()["id"], "relationship": "related_to"},
    )
    assert denied.status_code == 404, denied.text



def test_shared_lcr_context_is_bounded_and_person_scoped():
    client = TestClient(app)
    first = _session(client, "Context Caregiver One")
    second = _session(client, "Context Caregiver Two")

    db = TestingSessionLocal()
    try:
        event = CareEvent(
            id="lcr-context-event-one",
            person_id=first["person"]["id"],
            event_type="observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Breakfast was mostly uneaten.",
            description="Caregiver reported the meal was mostly untouched.",
            created_by_caregiver_id=first["caregiver"]["id"],
        )
        evidence = Evidence(
            id="lcr-context-evidence-one",
            person_id=first["person"]["id"],
            type="caregiver_note",
            source_text="Caregiver observed that breakfast was mostly uneaten.",
            uploaded_by_caregiver_id=first["caregiver"]["id"],
            source_type="caregiver_report",
        )
        db.add_all([event, evidence])
        db.flush()
        facts = Base.metadata.tables["care_facts"]
        db.execute(facts.insert().values(
            id="lcr-context-fact-one",
            person_id=first["person"]["id"],
            subject_type="person",
            subject_id=first["person"]["id"],
            fact_type="routine",
            fact_value="Usually wakes around 7 AM",
            epistemic_status="reported",
            created_by_caregiver_id=first["caregiver"]["id"],
        ))
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/api/v1/care-continuity/context?limit_per_source=10",
        headers={"Authorization": f"Bearer {first['access_token']}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["history_coverage"] == "bounded_recent_subset"
    assert body["person_id"] == first["person"]["id"]
    assert body["sources"]["events"]["returned_count"] == 1
    assert body["sources"]["events"]["items"][0]["title"] == "Breakfast was mostly uneaten."
    assert body["sources"]["facts"]["returned_count"] == 1
    assert body["sources"]["facts"]["items"][0]["fact_value"] == "Usually wakes around 7 AM"
    assert body["sources"]["evidence"]["returned_count"] == 1
    evidence_row = body["sources"]["evidence"]["items"][0]
    assert evidence_row["source_text"] == "Caregiver observed that breakfast was mostly uneaten."
    assert "original_file_path" not in evidence_row
    assert body["schema_status"] in {"complete", "partial"}

    other = client.get(
        "/api/v1/care-continuity/context",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert other.status_code == 200, other.text
    other_body = other.json()
    assert other_body["person_id"] == second["person"]["id"]
    assert other_body["sources"]["events"]["items"] == []
    assert other_body["sources"]["facts"]["items"] == []
    assert other_body["sources"]["evidence"]["items"] == []

    denied = client.get("/api/v1/care-continuity/context")
    assert denied.status_code == 401


def test_claim_evidence_links_reject_duplicates_and_flag_duplicate_content_without_claiming_corroboration():
    client = TestClient(app)
    session = _session(client, "Evidence reviewer")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]

    fact_response = client.post(
        "/api/v1/care-continuity/facts",
        headers=headers,
        json={"subject_type": "daily_care", "subject_id": "sleep-1", "fact_type": "sleep_pattern", "fact_value": "Sleep timing is disputed."},
    )
    assert fact_response.status_code == 201, fact_response.text
    fact_id = fact_response.json()["id"]

    db = TestingSessionLocal()
    try:
        db.add_all([
            Evidence(id="duplicate-evidence-a", person_id=person_id, type="caregiver_report", source_text="The same copied source note.", uploaded_by_caregiver_id=caregiver_id, source_type="caregiver_report"),
            Evidence(id="duplicate-evidence-b", person_id=person_id, type="caregiver_report", source_text="The same copied source note.", uploaded_by_caregiver_id=caregiver_id, source_type="caregiver_report"),
        ])
        db.commit()
    finally:
        db.close()

    first_link = client.post(
        "/api/v1/care-continuity/fact-evidence",
        headers=headers,
        json={"fact_id": fact_id, "evidence_id": "duplicate-evidence-a", "evidence_role": "supports"},
    )
    assert first_link.status_code == 201, first_link.text
    duplicate_link = client.post(
        "/api/v1/care-continuity/fact-evidence",
        headers=headers,
        json={"fact_id": fact_id, "evidence_id": "duplicate-evidence-a", "evidence_role": "supports"},
    )
    assert duplicate_link.status_code == 409

    second_link = client.post(
        "/api/v1/care-continuity/fact-evidence",
        headers=headers,
        json={"fact_id": fact_id, "evidence_id": "duplicate-evidence-b", "evidence_role": "supports"},
    )
    assert second_link.status_code == 201, second_link.text

    review_payload = {
        "review_type": "reliability",
        "outcome": "supported",
        "rationale": "Reviewer inspected the source record and recorded the source relationship.",
        "policy_version": "evidence-review-v1",
        "criteria": [
            {"criterion": "source attribution", "result": "present", "rationale": "The caregiver source is attributed."},
            {"criterion": "independent corroboration", "result": "independent_source_linked", "rationale": "Reviewer marked the source as independent; the content still needs duplicate checking."},
            {"criterion": "temporal context", "result": "specified", "rationale": "The source record contains its stated timing."},
            {"criterion": "evidence freshness", "result": "undetermined", "rationale": "Current relevance has not been independently established."},
            {"criterion": "record completeness", "result": "partial", "rationale": "Only the source text is present; corroborating context is not attached."},
        ],
    }
    for evidence_id in ("duplicate-evidence-a", "duplicate-evidence-b"):
        review_response = client.post(
            f"/api/v1/care-continuity/evidence/{evidence_id}/reviews",
            headers=headers,
            json=review_payload,
        )
        assert review_response.status_code == 201, review_response.text

    integrity = client.get(
        f"/api/v1/care-continuity/facts/{fact_id}/evidence-integrity",
        headers=headers,
    )
    assert integrity.status_code == 200, integrity.text
    result = integrity.json()
    assert result["linked_evidence_count"] == 2
    assert result["independent_corroboration_status"] == "not_established"
    assert result["evidence"][0]["source_independence"] == "independent_source_linked"
    assert len(result["duplicate_content_candidates"]) == 1
    assert set(result["duplicate_content_candidates"][0]["evidence_ids"]) == {"duplicate-evidence-a", "duplicate-evidence-b"}

    db = TestingSessionLocal()
    try:
        db.add_all([
            Evidence(id="independent-evidence-c", person_id=person_id, type="caregiver_report", source_text="A separate observation about the same care question.", uploaded_by_caregiver_id=caregiver_id, source_type="caregiver_report"),
            Evidence(id="independent-evidence-d", person_id=person_id, type="document", source_text="A distinct dated document describing the care question.", uploaded_by_caregiver_id=caregiver_id, source_type="document"),
        ])
        db.commit()
    finally:
        db.close()

    for evidence_id in ("independent-evidence-c", "independent-evidence-d"):
        link = client.post(
            "/api/v1/care-continuity/fact-evidence",
            headers=headers,
            json={"fact_id": fact_id, "evidence_id": evidence_id, "evidence_role": "supports"},
        )
        assert link.status_code == 201, link.text
        reviewed = client.post(
            f"/api/v1/care-continuity/evidence/{evidence_id}/reviews",
            headers=headers,
            json={**review_payload, "rationale": "A reviewer explicitly assessed this distinct source record."},
        )
        assert reviewed.status_code == 201, reviewed.text

    reviewed_integrity = client.get(
        f"/api/v1/care-continuity/facts/{fact_id}/evidence-integrity",
        headers=headers,
    )
    assert reviewed_integrity.status_code == 200, reviewed_integrity.text
    assert reviewed_integrity.json()["independent_corroboration_status"] == "reviewed_multiple_distinct_sources_candidate"


def test_lcr_context_api_applies_explicit_previous_calendar_year_window():
    """The real authenticated LCR route must apply temporal filters, not lexical-only search."""
    client = TestClient(app)
    session = _session(client, "Temporal history caregiver")
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]

    with TestingSessionLocal() as db:
        db.add_all([
            CareEvent(
                id="lcr-temporal-2025-event",
                person_id=person_id,
                event_type="observation",
                status="recorded",
                occurred_at=datetime(2025, 6, 10, 9, 0, tzinfo=timezone.utc),
                title="Care observation from 2025",
                description="A persisted observation in the previous calendar year.",
                created_by_caregiver_id=caregiver_id,
            ),
            CareEvent(
                id="lcr-temporal-2024-event",
                person_id=person_id,
                event_type="observation",
                status="recorded",
                occurred_at=datetime(2024, 6, 10, 9, 0, tzinfo=timezone.utc),
                title="Care observation from 2024",
                description="A persisted observation outside the previous calendar year.",
                created_by_caregiver_id=caregiver_id,
            ),
        ])
        db.commit()

    response = client.get(
        "/api/v1/care-continuity/context",
        headers=headers,
        params={"query": "What happened last year?", "limit_per_source": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["temporal_window"]["basis"] == "explicit_previous_calendar_year"
    event_rows = body["sources"]["events"]["items"]
    event_ids = {row["id"] for row in event_rows}
    assert "lcr-temporal-2025-event" in event_ids
    assert "lcr-temporal-2024-event" not in event_ids
    assert body["retrieval_contract"]["caregiver_scope_applied"] is True
    assert body["sources"]["companion_conversation"]["is_evidence"] is False

def test_decision_and_outcome_history_support_stable_offset_pagination():
    client = TestClient(app)
    first = _session(client, "Decision history caregiver")
    second = _session(client, "Other decision history caregiver")
    db = TestingSessionLocal()
    decision_ids = [f"decision-history-page-{index}" for index in range(3)]
    outcome_ids = [f"outcome-history-page-{index}" for index in range(3)]
    try:
        decisions = Base.metadata.tables["care_decisions"]
        outcomes = Base.metadata.tables["care_action_outcomes"]
        for index, decision_id in enumerate(decision_ids):
            db.execute(decisions.insert().values(
                id=decision_id,
                person_id=first["person"]["id"],
                title=f"Care decision {index}",
                decision_text=f"Persisted decision history record {index}",
                decision_status="recorded",
                created_at=datetime(2026, 11, 20, 10 + index, tzinfo=timezone.utc),
                created_by_caregiver_id=first["caregiver"]["id"],
            ))
        for index, outcome_id in enumerate(outcome_ids):
            db.execute(outcomes.insert().values(
                id=outcome_id,
                person_id=first["person"]["id"],
                action_id=f"decision-action-{index}",
                action_type="care_decision",
                outcome_status="recorded",
                notes=f"Persisted outcome history record {index}",
                evidence_ids=[],
                created_at=datetime(2026, 11, 20, 13 + index, tzinfo=timezone.utc),
                created_by_caregiver_id=first["caregiver"]["id"],
            ))
        db.commit()
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {first['access_token']}"}
    decision_first = client.get(
        "/api/v1/care-continuity/decisions?limit=2&offset=0",
        headers=headers,
    )
    decision_second = client.get(
        "/api/v1/care-continuity/decisions?limit=2&offset=2",
        headers=headers,
    )
    outcome_first = client.get(
        f"/api/v1/care-continuity/outcomes/{first['person']['id']}?limit=2&offset=0",
        headers=headers,
    )
    outcome_second = client.get(
        f"/api/v1/care-continuity/outcomes/{first['person']['id']}?limit=2&offset=2",
        headers=headers,
    )
    for response in (decision_first, decision_second, outcome_first, outcome_second):
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)
    assert {row["id"] for row in decision_first.json() + decision_second.json()} == set(decision_ids)
    assert {row["id"] for row in outcome_first.json() + outcome_second.json()} == set(outcome_ids)
    assert len(decision_first.json() + decision_second.json()) == 3
    assert len(outcome_first.json() + outcome_second.json()) == 3

    foreign = client.get(
        f"/api/v1/care-continuity/outcomes/{first['person']['id']}?limit=2&offset=0",
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert foreign.status_code == 403

