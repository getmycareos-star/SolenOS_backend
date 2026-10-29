"""Persist typed source references alongside Companion messages.

Revision ID: 20261029_26_companion_refs
Revises: 20261029_25_freshness
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_26_companion_refs"
down_revision = "20261029_25_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("conversation_messages must exist before source reference provenance can be added")
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "source_record_refs_json" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column(
                "source_record_refs_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop Companion source-record provenance. "
        "Use a forward-only compatibility migration to preserve audit history."
    )
