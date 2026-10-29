"""Persist Companion idempotency keys and response provenance.

Revision ID: 20261029_30_comp_turn_idem
Revises: 20261029_29_ev_rel_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_30_comp_turn_idem"
down_revision = "20261029_29_ev_rel_scope"
branch_labels = None
depends_on = None

TABLE = "conversation_messages"
INDEX = "uq_conversation_messages_client_turn_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Required conversation_messages table is missing; refusing to create a disconnected schema")

    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if "client_turn_id" not in columns:
        op.add_column(TABLE, sa.Column("client_turn_id", sa.String(length=36), nullable=True))
    if "response_metadata_json" not in columns:
        op.add_column(TABLE, sa.Column("response_metadata_json", sa.Text(), nullable=True))

    refreshed = inspect(bind)
    indexes = {item["name"] for item in refreshed.get_indexes(TABLE) if item.get("name")}
    constraints = {item.get("name") for item in refreshed.get_unique_constraints(TABLE)}
    if INDEX not in indexes and INDEX not in constraints:
        op.create_index(INDEX, TABLE, ["client_turn_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    indexes = {item["name"] for item in inspector.get_indexes(TABLE) if item.get("name")}
    if INDEX in indexes:
        op.drop_index(INDEX, table_name=TABLE)
    columns = {column["name"] for column in inspect(bind).get_columns(TABLE)}
    if "response_metadata_json" in columns:
        op.drop_column(TABLE, "response_metadata_json")
    if "client_turn_id" in columns:
        op.drop_column(TABLE, "client_turn_id")
