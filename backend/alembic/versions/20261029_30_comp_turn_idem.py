"""Persist Companion idempotency keys and replay provenance.

Revision ID: 20261029_30_comp_turn_idem
Revises: 20261029_29_evidence_relationship_scope
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_30_comp_turn_idem"
down_revision = "20261029_29_evidence_relationship_scope"
branch_labels = None
depends_on = None

TABLE = "conversation_messages"
INDEX = "uq_conversation_messages_client_turn_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("conversation_messages must exist before Companion retry provenance is added")

    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    if "client_turn_id" not in columns:
        op.add_column(TABLE, sa.Column("client_turn_id", sa.String(length=36), nullable=True))
    if "response_metadata_json" not in columns:
        op.add_column(TABLE, sa.Column("response_metadata_json", sa.Text(), nullable=True))

    inspector = inspect(bind)
    indexes = inspector.get_indexes(TABLE)
    constraints = inspector.get_unique_constraints(TABLE)
    has_unique_turn_id = any(
        item.get("name") == INDEX
        or (
            item.get("unique", False)
            and set(item.get("column_names") or []) == {"client_turn_id"}
        )
        for item in indexes + constraints
    )
    if not has_unique_turn_id:
        op.create_index(INDEX, TABLE, ["client_turn_id"], unique=True)


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove Companion idempotency and response provenance. "
        "Use a forward-only compatibility migration to preserve retry audit history."
    )
