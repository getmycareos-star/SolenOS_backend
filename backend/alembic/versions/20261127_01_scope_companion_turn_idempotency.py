"""Scope Companion turn idempotency uniqueness to its conversation.

Revision ID: 20261127_01_scope_companion_turn_idempotency
Revises: 20261126_03_companion_proposal_idempotency
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261127_01_scope_companion_turn_idempotency"
down_revision = "20261126_03_companion_proposal_idempotency"
branch_labels = None
depends_on = None

OLD_INDEX = "uq_conversation_messages_client_turn_id"
NEW_INDEX = "uq_conversation_messages_session_client_turn"
LOOKUP_INDEX = "ix_conversation_messages_client_turn_id"


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
    inspector = inspect(bind)
    indexes = {item["name"]: item for item in inspector.get_indexes("conversation_messages")}
    lookup = indexes.get(LOOKUP_INDEX)
    if lookup is None:
        op.create_index(
            LOOKUP_INDEX,
            "conversation_messages",
            ["client_turn_id"],
            unique=False,
        )
    elif lookup.get("column_names") != ["client_turn_id"] or lookup.get("unique"):
        raise RuntimeError("Companion turn lookup index exists with an unexpected definition.")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    indexes = {item["name"]: item for item in inspector.get_indexes("conversation_messages")}
    if OLD_INDEX not in indexes:
        duplicate = bind.execute(text(
            "SELECT client_turn_id FROM conversation_messages "
            "WHERE client_turn_id IS NOT NULL "
            "GROUP BY client_turn_id HAVING COUNT(*) > 1 LIMIT 1"
        )).first()
        if duplicate is not None:
            raise RuntimeError(
                "Cannot restore global Companion turn uniqueness: duplicate client_turn_id values exist across conversations."
            )
    if LOOKUP_INDEX in indexes:
        op.drop_index(LOOKUP_INDEX, table_name="conversation_messages")
    if NEW_INDEX in indexes:
        op.drop_index(NEW_INDEX, table_name="conversation_messages")
    if OLD_INDEX not in indexes:
        op.create_index(
            OLD_INDEX,
            "conversation_messages",
            ["client_turn_id"],
            unique=True,
        )
