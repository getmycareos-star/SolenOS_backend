"""Regression test for the Companion assertion event-time schema repair."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text


def test_missing_care_assertion_event_time_text_column_is_added_without_rewriting_rows():
    backend_dir = Path(__file__).resolve().parents[1]
    path = backend_dir / "alembic" / "versions" / "20261107_04_assert_event_time.py"
    spec = importlib.util.spec_from_file_location("assert_event_time_repair", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(migration)

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE care_assertions (id VARCHAR PRIMARY KEY, assertion_text TEXT)"))
        connection.execute(text("INSERT INTO care_assertions (id, assertion_text) VALUES ('a1', 'Existing assertion')"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        columns = {item["name"]: item for item in inspect(connection).get_columns("care_assertions")}
        assert "event_time_text" in columns
        assert columns["event_time_text"]["nullable"] is True
        assert "TEXT" in str(columns["event_time_text"]["type"]).upper()
        assert connection.execute(
            text("SELECT assertion_text, event_time_text FROM care_assertions WHERE id='a1'")
        ).one() == ("Existing assertion", None)

        # The forward repair is safe to re-run if deployment retries the migration.
        with Operations.context(context):
            migration.upgrade()

    engine.dispose()
