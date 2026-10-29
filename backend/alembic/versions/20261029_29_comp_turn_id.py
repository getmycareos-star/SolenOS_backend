"""Persist Companion idempotency keys and original response provenance.

Revision ID: 20261029_29_comp_turn_id
Revises: 20261029_28_conflict_remainder
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_29_comp_turn_id"
down_revision = "20261029_28_conflict_remainder"
branch_labels = None
depends_on = None


def _columns():
    return {column["name"] for column in inspect(op.get_bind()).get_columns("conversation_messages")}


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    existing = _columns()
    if "client_turn_id" not in existing:
        op.add_column("conversation_messages", sa.Column("client_turn_id", sa.String(length=36), nullable=True))
    existing = _columns()
    if "request_fingerprint" not in existing:
        op.add_column("conversation_messages", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    existing = _columns()
    if "response_metadata_json" not in existing:
        op.add_column("conversation_messages", sa.Column("response_metadata_json", sa.Text(), nullable=True))
    index_names = {index["name"] for index in inspect(bind).get_indexes("conversation_messages")}
    if "uq_conversation_messages_session_turn" not in index_names:
        op.create_index(
            "uq_conversation_messages_session_turn",
            "conversation_messages",
            ["session_id", "client_turn_id"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        return
    index_names = {index["name"] for index in inspector.get_indexes("conversation_messages")}
    if "uq_conversation_messages_session_turn" in index_names:
        op.drop_index("uq_conversation_messages_session_turn", table_name="conversation_messages")
    existing = {column["name"] for column in inspector.get_columns("conversation_messages")}
    for name in ("response_metadata_json", "request_fingerprint", "client_turn_id"):
        if name in existing:
            op.drop_column("conversation_messages", name)
