"""Persist Companion turn idempotency keys and response provenance.

Revision ID: 20261029_26_comp_turn_id
Revises: 20261029_25_freshness
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_26_comp_turn_id"
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
            sa.Column("response_metadata_json", sa.Text(), nullable=True),
        )

    inspector = inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("conversation_messages")}
    if "ix_conversation_messages_client_turn_id" not in indexes:
        op.create_index(
            "ix_conversation_messages_client_turn_id",
            "conversation_messages",
            ["client_turn_id"],
            unique=False,
        )

    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("conversation_messages")
        if constraint.get("name")
    }
    if "uq_conversation_message_client_turn_role" not in constraints:
        op.create_unique_constraint(
            "uq_conversation_message_client_turn_role",
            "conversation_messages",
            ["client_turn_id", "role"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("conversation_messages")}
    if "ix_conversation_messages_client_turn_id" in indexes:
        op.drop_index("ix_conversation_messages_client_turn_id", table_name="conversation_messages")
    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("conversation_messages")
        if constraint.get("name")
    }
    if "uq_conversation_message_client_turn_role" in constraints:
        op.drop_constraint(
            "uq_conversation_message_client_turn_role",
            "conversation_messages",
            type_="unique",
        )
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "response_metadata_json" in columns:
        op.drop_column("conversation_messages", "response_metadata_json")
    if "client_turn_id" in columns:
        op.drop_column("conversation_messages", "client_turn_id")
