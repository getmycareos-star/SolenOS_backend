"""Add explicit permission for recipient-scoped Companion conversation history.

Revision ID: 20261126_02_companion_conversation_permission
Revises: 20261126_01_companion_turn_leases
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261126_02_companion_conversation_permission"
down_revision = "20261126_01_companion_turn_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Companion conversation permissions require PostgreSQL.")
    inspector = inspect(bind)
    if not inspector.has_table("caregiver_access_grants"):
        raise RuntimeError("Cannot grant conversation access; caregiver_access_grants is missing.")
    columns = {column["name"] for column in inspector.get_columns("caregiver_access_grants")}
    required = {"permissions", "access_scope", "status"}
    if not required.issubset(columns):
        raise RuntimeError("Caregiver access grant schema is incomplete.")

    # Conversation history is a distinct permission from reading LCR evidence.
    # Existing active full-record grants receive the new capability because
    # conversation access was previously part of the ordinary Companion flow;
    # documents-only grants are intentionally excluded.
    bind.execute(text("""
        UPDATE caregiver_access_grants
        SET permissions = (
            COALESCE(permissions::jsonb, '[]'::jsonb)
            || '["view_conversations"]'::jsonb
        )::json
        WHERE access_scope = 'full_record'
          AND status = 'active'
          AND NOT (
              COALESCE(permissions::jsonb, '[]'::jsonb)
              ? 'view_conversations'
          )
    """))


def downgrade() -> None:
    # Retain the additive permission value. Removing it during downgrade could
    # revoke a conversation permission deliberately assigned after this migration.
    pass
