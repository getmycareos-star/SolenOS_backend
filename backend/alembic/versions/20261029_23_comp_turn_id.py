"""Persist idempotency keys and source provenance for Companion turns.

Revision ID: 20261029_23_comp_turn_id
Revises: 20261029_22_conflict_hist
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_23_comp_turn_id"
down_revision = "20261029_22_conflict_hist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("conversation_messages must exist before Companion idempotency is added")
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "client_turn_id" not in columns:
        op.add_column("conversation_messages", sa.Column("client_turn_id", sa.String(length=36), nullable=True))
    if "response_metadata_json" not in columns:
        op.add_column("conversation_messages", sa.Column("response_metadata_json", sa.Text(), nullable=True))

    inspector = inspect(bind)
    indexes = inspector.get_indexes("conversation_messages")
    constraints = inspector.get_unique_constraints("conversation_messages")
    exists = any(
        set(item.get("column_names") or []) == {"client_turn_id"}
        for item in [*indexes, *constraints]
        if item.get("unique", True)
    )
    if not exists:
        op.create_index(
            "uq_conversation_messages_client_turn_id",
            "conversation_messages",
            ["client_turn_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    indexes = inspector.get_indexes("conversation_messages")
    constraints = inspector.get_unique_constraints("conversation_messages")
    if any(index.get("name") == "uq_conversation_messages_client_turn_id" for index in indexes):
        op.drop_index("uq_conversation_messages_client_turn_id", table_name="conversation_messages")
    elif any(constraint.get("name") == "uq_conversation_messages_client_turn_id" for constraint in constraints):
        op.drop_constraint("uq_conversation_messages_client_turn_id", "conversation_messages", type_="unique")
    columns = {column["name"] for column in inspect(bind).get_columns("conversation_messages")}
    if "response_metadata_json" in columns:
        op.drop_column("conversation_messages", "response_metadata_json")
    if "client_turn_id" in columns:
        op.drop_column("conversation_messages", "client_turn_id")
