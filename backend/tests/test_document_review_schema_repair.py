"""Regression test for the production document-review schema recovery migration."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError


def _load_repair_migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20261108_01_repair_document_review_events.py"
    )
    spec = importlib.util.spec_from_file_location("document_review_schema_repair", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_repair_migration_recreates_missing_document_review_table_and_append_only_guards():
    engine = sa.create_engine("sqlite://")
    migration = _load_repair_migration()
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        for table in ("persons", "caregivers", "evidence", "care_assertions", "capture_analyses"):
            connection.execute(text(f'CREATE TABLE "{table}" (id VARCHAR PRIMARY KEY)'))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        inspector = inspect(connection)
        assert inspector.has_table("document_review_events")
        columns = {column["name"] for column in inspector.get_columns("document_review_events")}
        assert columns == migration._REQUIRED_COLUMNS
        foreign_keys = {
            (column, item.get("referred_table"))
            for item in inspector.get_foreign_keys("document_review_events")
            for column in (item.get("constrained_columns") or [])
        }
        assert migration._REQUIRED_FOREIGN_KEYS <= foreign_keys
        index_names = {item["name"] for item in inspector.get_indexes("document_review_events")}
        assert {
            "ix_document_review_events_analysis_id",
            "ix_document_review_events_person_id",
            "ix_document_review_events_caregiver_id",
            "ix_document_review_events_person_analysis",
        } <= index_names

        connection.execute(text("INSERT INTO persons (id) VALUES ('p1')"))
        connection.execute(text("INSERT INTO caregivers (id) VALUES ('c1')"))
        connection.execute(text("INSERT INTO evidence (id) VALUES ('e1')"))
        connection.execute(text("INSERT INTO care_assertions (id) VALUES ('a1')"))
        connection.execute(text("INSERT INTO capture_analyses (id) VALUES ('an1')"))
        connection.execute(text("""
            INSERT INTO document_review_events
            (id, analysis_id, person_id, caregiver_id, source_evidence_id,
             candidate_index, decision_version, decision, before_state, after_state)
            VALUES ('r1', 'an1', 'p1', 'c1', 'e1', 0, 1, 'accepted', '{}', '{}')
        """))
        with pytest.raises(DBAPIError):
            connection.execute(text(
                "UPDATE document_review_events SET rationale='mutated' WHERE id='r1'"
            ))
        with pytest.raises(DBAPIError):
            connection.execute(text("DELETE FROM document_review_events WHERE id='r1'"))
        assert connection.execute(
            text("SELECT COUNT(*) FROM document_review_events WHERE id='r1'")
        ).scalar_one() == 1

    engine.dispose()
