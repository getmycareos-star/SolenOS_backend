"""Persist structured source context for caregiver-reviewed document assertions.

Revision ID: 20261119_01_document_statement_context
Revises: 20261116_07_audit_events_append_only
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261119_01_document_statement_context"
down_revision = "20261116_07_audit_events_append_only"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("care_assertions"):
        raise RuntimeError("Cannot add document source context: care_assertions is missing")
    columns = {column["name"] for column in inspect(bind).get_columns("care_assertions")}
    if "source_context" not in columns:
        op.add_column(
            "care_assertions",
            sa.Column(
                "source_context",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("care_assertions"):
        columns = {column["name"] for column in inspect(bind).get_columns("care_assertions")}
        if "source_context" in columns:
            op.drop_column("care_assertions", "source_context")
