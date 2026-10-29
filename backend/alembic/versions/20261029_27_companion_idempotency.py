"""Persist Companion turn idempotency and response provenance.

Revision ID: 20261029_27_comp_idem
Revises: 20261029_26_companion_refs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_27_comp_idem"
down_revision = "20261029_26_companion_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("conversation_messages must exist before idempotency metadata is added")

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

    indexes = {index["name"] for index in inspect(bind).get_indexes("conversation_messages")}
    if "uq_conversation_message_client_turn_role" not in indexes:
        op.create_index(
            "uq_conversation_message_client_turn_role",
            "conversation_messages",
            ["client_turn_id", "role"],
            unique=True,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove Companion idempotency and response provenance. "
        "Use a forward-only compatibility migration to preserve conversation history."
    )
