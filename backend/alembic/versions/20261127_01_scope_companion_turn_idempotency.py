"""Scope Companion turn idempotency uniqueness to its conversation.

Revision ID: 20261127_01_scope_companion_turn_idempotency
Revises: 20261126_03_companion_proposal_idempotency
"""
from alembic import op
from sqlalchemy import inspect

revision = "20261127_01_scope_companion_turn_idempotency"
down_revision = "20261126_03_companion_proposal_idempotency"
branch_labels = None
depends_on = None

OLD_INDEX = "uq_conversation_messages_client_turn_id"
NEW_INDEX = "uq_conversation_messages_session_client_turn"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("Companion conversation_messages table is missing.")
    indexes = {item["name"]: item for item in inspector.get_indexes("conversation_messages")}
    old = indexes.get(OLD_INDEX)
    if old is not None:
        op.drop_index(OLD_INDEX, table_name="conversation_messages")
    inspector = inspect(bind)
    indexes = {item["name"]: item for item in inspector.get_indexes("conversation_messages")}
    current = indexes.get(NEW_INDEX)
    if current is None:
        op.create_index(
            NEW_INDEX,
            "conversation_messages",
            ["session_id", "client_turn_id"],
            unique=True,
        )
    elif current.get("column_names") != ["session_id", "client_turn_id"] or not current.get("unique"):
        raise RuntimeError("Companion turn uniqueness index exists with an unexpected definition.")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    indexes = {item["name"]: item for item in inspector.get_indexes("conversation_messages")}
    if NEW_INDEX in indexes:
        op.drop_index(NEW_INDEX, table_name="conversation_messages")
    if OLD_INDEX not in indexes:
        op.create_index(
            OLD_INDEX,
            "conversation_messages",
            ["client_turn_id"],
            unique=True,
        )
