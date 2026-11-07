"""Regression coverage for production document-review schema recovery."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError


def _load_repair_migration():
    backend_dir = Path(__file__).resolve().parents[1]
    path = backend_dir / "alembic" / "versions" / "20261107_03_repair_doc_review_events.py"
    spec = importlib.util.spec_from_file_location("repair_doc_review_events", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_missing_document_review_table_is_recreated_with_canonical_constraints_and_guards():
    engine = sa.create_engine("sqlite://")
    migration = _load_repair_migration()
    with engine.begin() as connection:
        for name in ("persons", "caregivers", "evidence", "care_assertions", "capture_analyses"):
            connection.execute(text(f'CREATE TABLE "{name}" (id VARCHAR PRIMARY KEY)'))

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        inspector = inspect(connection)
        assert inspector.has_table("document_review_events")
        model_table = migration.Base.metadata.tables["document_review_events"]
        assert {
            column.name for column in model_table.columns
        } == {
            column["name"] for column in inspector.get_columns("document_review_events")
        }

        foreign_keys = {
            (column, item.get("referred_table"))
            for item in inspector.get_foreign_keys("document_review_events")
            for column in (item.get("constrained_columns") or [])
        }
        assert {
            ("analysis_id", "capture_analyses"),
            ("person_id", "persons"),
            ("caregiver_id", "caregivers"),
            ("source_evidence_id", "evidence"),
            ("resulting_assertion_id", "care_assertions"),
        } <= foreign_keys

        unique_constraints = inspector.get_unique_constraints("document_review_events")
        assert any(
            item.get("name") == "uq_document_review_candidate_version"
            and item.get("column_names") == ["analysis_id", "candidate_index", "decision_version"]
            for item in unique_constraints
        )

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
                "UPDATE document_review_events SET rationale='changed' WHERE id='r1'"
            ))
        with pytest.raises(DBAPIError):
            connection.execute(text("DELETE FROM document_review_events WHERE id='r1'"))
        assert connection.execute(
            text("SELECT COUNT(*) FROM document_review_events WHERE id='r1'")
        ).scalar_one() == 1

    engine.dispose()
