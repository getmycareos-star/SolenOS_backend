from datetime import datetime, timezone

import pytest
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
            connection.execute(text("""
                CREATE TRIGGER trg_privacy_incident_events_append_only_update
                BEFORE UPDATE ON privacy_incident_events
                BEGIN
                    SELECT RAISE(ABORT, 'security incident timeline events are append-only');
                END
            """))
            connection.execute(text("""
                CREATE TRIGGER trg_privacy_incident_events_append_only_delete
                BEFORE DELETE ON privacy_incident_events
                BEGIN
                    SELECT RAISE(ABORT, 'security incident timeline events are append-only');
                END
            """))
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
