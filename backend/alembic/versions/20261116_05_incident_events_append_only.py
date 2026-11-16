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
                -- Explicit, reviewed privacy erasure may remove subject-linked
                -- incident events; ordinary UPDATE/DELETE remains prohibited.
                IF TG_OP = 'DELETE'
                   AND current_setting('solenos.privacy_erasure', true) = 'on' THEN
                    RETURN OLD;
                END IF;
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
    # SQLite is a local/test compatibility database and has no equivalent
    # transaction-local privacy-erasure authorization variable. Do not install
    # a trigger there that would make the reviewed erasure workflow impossible.


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
