"""Merge the restored Companion-turn and evidence-relationship migration branches.

This is a graph reconciliation only. The upgrade also verifies Companion-turn
columns and uniqueness because an existing production database may already be
stamped at the restored revision while its physical schema needs reconciliation.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261030_01_companion_turn_merge"
down_revision = (
    "20261029_29_comp_turn_id",
    "20261029_29_ev_rel_scope",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("conversation_messages is required for Companion turn reconciliation")

    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "client_turn_id" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column("client_turn_id", sa.String(length=36), nullable=True),
        )
    if "response_metadata_json" not in columns:
        op.add_column(
            "conversation_messages",
            sa.Column("response_metadata_json", sa.Text(), nullable=True),
        )

    inspector = inspect(bind)
    indexes = inspector.get_indexes("conversation_messages")
    constraints = inspector.get_unique_constraints("conversation_messages")
    unique_turn_id = any(
        item.get("unique") is True
        and set(item.get("column_names") or []) == {"client_turn_id"}
        for item in indexes
    ) or any(
        set(item.get("column_names") or []) == {"client_turn_id"}
        for item in constraints
    )
    if not unique_turn_id:
        index_name = "uq_conversation_messages_client_turn_id"
        if any(item.get("name") == index_name for item in indexes):
            raise RuntimeError(
                "Companion turn idempotency index exists with an unexpected definition"
            )
        op.create_index(
            index_name,
            "conversation_messages",
            ["client_turn_id"],
            unique=True,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to reverse the migration-graph repair or remove Companion turn provenance."
    )
