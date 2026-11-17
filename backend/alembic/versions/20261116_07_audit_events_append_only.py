"""Protect audit event rows from ordinary mutation.

Revision ID: 20261116_07_audit_events_append_only
Revises: 20261116_06_operational_worker_heartbeats
"""
from alembic import op

revision = "20261116_07_audit_events_append_only"
down_revision = "20261116_06_operational_worker_heartbeats"
branch_labels = None
depends_on = None

TABLE = "audit_log_events"
FUNCTION = "reject_audit_log_event_mutation"
TRIGGER = "trg_audit_log_events_append_only"


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"""
            CREATE OR REPLACE FUNCTION {FUNCTION}()
            RETURNS trigger AS $$
            BEGIN
                -- A reviewed privacy-erasure transaction may redact identifiers
                -- and metadata, but must not delete the audit event itself.
                IF TG_OP = 'UPDATE'
                   AND current_setting('solenos.privacy_erasure', true) = 'on' THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'audit log events are append-only outside approved privacy erasure redaction';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
        op.execute(f"""
            CREATE TRIGGER {TRIGGER}
            BEFORE UPDATE OR DELETE ON {TABLE}
            FOR EACH ROW EXECUTE FUNCTION {FUNCTION}()
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {TRIGGER} ON {TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
