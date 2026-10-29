"""Enforce append-only conflict resolution history at the database boundary.

Revision ID: 20261029_22_conflict_history_immutable
Revises: 20261029_21_loop_occ
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261029_22_conflict_history_immutable"
down_revision = "20261029_21_loop_occ"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("contradiction_resolution_events"):
        raise RuntimeError("Conflict resolution history must exist before immutability is enabled")

    if bind.dialect.name == "postgresql":
        bind.execute(text("""
            CREATE OR REPLACE FUNCTION reject_conflict_resolution_history_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Conflict resolution history is append-only; record a new transition';
            END;
            $$ LANGUAGE plpgsql
        """))
        bind.execute(text("""
            DROP TRIGGER IF EXISTS trg_conflict_resolution_events_immutable
            ON contradiction_resolution_events
        """))
        bind.execute(text("""
            CREATE TRIGGER trg_conflict_resolution_events_immutable
            BEFORE UPDATE OR DELETE ON contradiction_resolution_events
            FOR EACH ROW EXECUTE FUNCTION reject_conflict_resolution_history_mutation()
        """))
    elif bind.dialect.name == "sqlite":
        bind.execute(text("""
            CREATE TRIGGER IF NOT EXISTS trg_conflict_resolution_events_no_update
            BEFORE UPDATE ON contradiction_resolution_events
            BEGIN
                SELECT RAISE(ABORT, 'Conflict resolution history is append-only; record a new transition');
            END
        """))
        bind.execute(text("""
            CREATE TRIGGER IF NOT EXISTS trg_conflict_resolution_events_no_delete
            BEFORE DELETE ON contradiction_resolution_events
            BEGIN
                SELECT RAISE(ABORT, 'Conflict resolution history is append-only; record a new transition');
            END
        """))


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove conflict-history immutability; the audit guarantee must not be silently weakened."
    )
