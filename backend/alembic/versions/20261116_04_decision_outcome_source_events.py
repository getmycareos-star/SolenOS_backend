"""Persist decision outcome source care-event references.

Revision ID: 20261116_04_decision_outcome_source_events
Revises: 20261116_03_decision_outcome_idempotency
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261116_04_decision_outcome_source_events"
down_revision = "20261116_03_decision_outcome_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("decision_outcomes"):
        raise RuntimeError("Cannot add outcome source references: decision_outcomes is missing")
    columns = {column["name"] for column in inspect(bind).get_columns("decision_outcomes")}
    if "source_event_ids" not in columns:
        op.add_column(
            "decision_outcomes",
            sa.Column("source_event_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("decision_outcomes"):
        columns = {column["name"] for column in inspect(bind).get_columns("decision_outcomes")}
        if "source_event_ids" in columns:
            op.drop_column("decision_outcomes", "source_event_ids")
