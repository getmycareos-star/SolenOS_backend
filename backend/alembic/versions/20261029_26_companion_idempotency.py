"""Persist Companion turn idempotency and response provenance.

Revision ID: 20261029_26_comp_idem
Revises: 20261029_25_freshness
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_26_comp_idem"
down_revision = "20261029_25_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return

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
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("conversation_messages")}
    if "uq_conversation_message_client_turn_role" in indexes:
        op.drop_index("uq_conversation_message_client_turn_role", table_name="conversation_messages")
    columns = {column["name"] for column in inspect(bind).get_columns("conversation_messages")}
    if "response_metadata_json" in columns:
        op.drop_column("conversation_messages", "response_metadata_json")
    if "client_turn_id" in columns:
        op.drop_column("conversation_messages", "client_turn_id")
