from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_incident_timeline_rejects_update_and_delete():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE privacy_incident_events (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at DATETIME NOT NULL,
                    actor_id TEXT,
                    notes_redacted TEXT
                )
            """))
            migration_path = (
                Path(__file__).parents[1]
                / "alembic"
                / "versions"
                / "20261116_05_incident_events_append_only.py"
            )
            spec = importlib.util.spec_from_file_location(
                "incident_events_append_only_migration", migration_path
            )
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(text("""
                INSERT INTO privacy_incident_events
                    (id, incident_id, event_type, occurred_at, actor_id, notes_redacted)
                VALUES ('event-1', 'incident-1', 'opened', :now, 'synthetic', 'Synthetic test only')
            """), {"now": datetime.now(timezone.utc)})

        with engine.begin() as connection:
            with pytest.raises(Exception, match="append-only"):
                connection.execute(text("""
                    UPDATE privacy_incident_events SET notes_redacted='changed' WHERE id='event-1'
                """))
        with engine.begin() as connection:
            with pytest.raises(Exception, match="append-only"):
                connection.execute(text("DELETE FROM privacy_incident_events WHERE id='event-1'"))
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT COUNT(*) FROM privacy_incident_events WHERE id='event-1'"
            )).scalar_one() == 1
    finally:
        engine.dispose()
