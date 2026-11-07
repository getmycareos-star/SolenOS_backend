"""Reconcile the Companion assertion event-time text column.

Revision ID: 20261107_04_assert_event_time
Revises: 20261107_03_repair_doc_review_events

The prior Companion LCR migration may already be stamped on databases where
care_assertions.event_time_text was not created. Add only that nullable text
column when absent; preserve all existing assertion rows unchanged.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261107_04_assert_event_time"
down_revision = "20261107_03_repair_doc_review_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_assertions"):
        raise RuntimeError("Cannot reconcile event_time_text: care_assertions table is missing")

    columns = {item["name"]: item for item in inspector.get_columns("care_assertions")}
    if "event_time_text" not in columns:
        op.add_column(
            "care_assertions",
            sa.Column("event_time_text", sa.Text(), nullable=True),
        )

    refreshed = {
        item["name"]: item for item in inspect(bind).get_columns("care_assertions")
    }
    column = refreshed.get("event_time_text")
    if column is None:
        raise RuntimeError("care_assertions.event_time_text was not created")
    if not column.get("nullable", True):
        raise RuntimeError("care_assertions.event_time_text must remain nullable")
    actual_type = str(column["type"]).upper()
    if "TEXT" not in actual_type:
        raise RuntimeError(
            "care_assertions.event_time_text has an incompatible type: " + actual_type
        )


def downgrade() -> None:
    # Deliberately retain the nullable text column. This reconciliation may
    # be replayed after a test rollback; dropping it would discard event-time
    # provenance. The subsequent upgrade is safe because upgrade() is
    # idempotent when the column already exists.
    return None
