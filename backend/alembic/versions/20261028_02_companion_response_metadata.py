"""Persist Companion response provenance for deterministic retry replay.

Revision ID: 20261028_02_companion_response_metadata
Revises: 20261028_01_profile_link
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261028_02_resp_meta"
down_revision = "20261028_01_profile_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("Cannot persist Companion response metadata: conversation_messages is missing")
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "response_metadata_json" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column(
                "response_metadata_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "response_metadata_json" in columns:
        op.drop_column("conversation_messages", "response_metadata_json")
