"""Forward repair for the document caregiver review event table.

Revision ID: 20261107_03_repair_doc_review_events
Revises: 20261107_02_handoff_drafts

The prior revision may already be recorded as applied on a database whose
document_review_events table is absent. This forward-only repair restores the
missing table from the canonical ORM definition without touching care records.
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261107_03_repair_doc_review_events"
down_revision = "20261107_02_handoff_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Import the canonical model so columns, foreign keys, uniqueness and
    # indexes come from one source of truth rather than a second hand-written
    # schema definition.
    from app.models.capture_analysis import DocumentReviewEvent  # noqa: F401
    from app.core.database import Base

    table = Base.metadata.tables["document_review_events"]
    inspector = inspect(bind)
    if not inspector.has_table("document_review_events"):
        table.create(bind=bind, checkfirst=True)

    inspector = inspect(bind)
    required_columns = set(table.columns.keys())
    actual_columns = {
        column["name"] for column in inspector.get_columns("document_review_events")
    }
    missing_columns = required_columns - actual_columns
    if missing_columns:
        raise RuntimeError(
            "document_review_events repair found an incompatible existing table; "
            "missing columns: " + ", ".join(sorted(missing_columns))
        )

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_document_review_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'document review events are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "DROP TRIGGER IF EXISTS trg_document_review_events_append_only "
            "ON document_review_events"
        )
        op.execute("""
            CREATE TRIGGER trg_document_review_events_append_only
            BEFORE UPDATE OR DELETE ON document_review_events
            FOR EACH ROW EXECUTE FUNCTION prevent_document_review_event_mutation()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_document_review_events_no_update
            BEFORE UPDATE ON document_review_events
            BEGIN SELECT RAISE(ABORT, 'document review events are append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_document_review_events_no_delete
            BEFORE DELETE ON document_review_events
            BEGIN SELECT RAISE(ABORT, 'document review events are append-only'); END
        """)


def downgrade() -> None:
    raise RuntimeError(
        "This repair is forward-only: document caregiver review history must never be dropped."
    )
