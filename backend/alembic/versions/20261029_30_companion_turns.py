"""Persist Companion idempotency keys and replayable response provenance.

Revision ID: 20261029_30_comp_turns
Revises: 20261029_29_ev_rel_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_30_comp_turns"
down_revision = "20261029_29_ev_rel_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("conversation_messages must exist before Companion retry metadata is added")
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "client_turn_id" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column("client_turn_id", sa.String(length=128), nullable=True),
        )
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
    indexes = {item["name"] for item in inspect(bind).get_indexes("conversation_messages")}
    if "uq_conversation_messages_client_turn_id" not in indexes:
        op.create_index(
            "uq_conversation_messages_client_turn_id",
            "conversation_messages",
            ["client_turn_id"],
            unique=True,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to drop Companion retry keys or response provenance; use a forward-only migration."
    )
