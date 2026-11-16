"""Make security incident timeline events append-only.

Revision ID: 20261116_05_incident_events_append_only
Revises: 20261116_04_decision_outcome_source_events
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261116_05_incident_events_append_only"
down_revision = "20261116_04_decision_outcome_source_events"
branch_labels = None
depends_on = None

TABLE = "privacy_incident_events"
TRIGGER = "trg_privacy_incident_events_append_only"
FUNCTION = "reject_privacy_incident_event_mutation"


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Cannot protect incident timeline: privacy_incident_events is missing")

    if bind.dialect.name == "postgresql":
        op.execute(f"""
            CREATE OR REPLACE FUNCTION {FUNCTION}()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'security incident timeline events are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
        op.execute(f"""
            CREATE TRIGGER {TRIGGER}
            BEFORE UPDATE OR DELETE ON {TABLE}
            FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}_update")
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}_delete")
        op.execute(f"""
            CREATE TRIGGER {TRIGGER}_update
            BEFORE UPDATE ON {TABLE}
            BEGIN
                SELECT RAISE(ABORT, 'security incident timeline events are append-only');
            END
        """)
        op.execute(f"""
            CREATE TRIGGER {TRIGGER}_delete
            BEFORE DELETE ON {TABLE}
            BEGIN
                SELECT RAISE(ABORT, 'security incident timeline events are append-only');
            END
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
    elif bind.dialect.name == "sqlite":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}_update")
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER}_delete")
