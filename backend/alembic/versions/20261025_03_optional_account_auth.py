"""Add optional caregiver account authentication and scoped account sessions.

Revision ID: 20261025_03
Revises: 20261025_02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261025_03"
down_revision = "20261025_02"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("auth_accounts"):
        op.create_table(
            "auth_accounts",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("email", sa.String(length=254), nullable=False, unique=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False, unique=True),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False, unique=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_auth_accounts_email", "auth_accounts", ["email"])
    if not inspector.has_table("auth_sessions"):
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column("account_id", sa.String(), sa.ForeignKey("auth_accounts.id"), nullable=False),
            sa.Column("token_digest", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_auth_sessions_account_id", "auth_sessions", ["account_id"])
        op.create_index("ix_auth_sessions_token_digest", "auth_sessions", ["token_digest"])
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    columns = {item["name"] for item in inspector.get_columns("anonymous_sessions")} if inspector.has_table("anonymous_sessions") else set()
    if "account_id" not in columns:
        op.add_column("anonymous_sessions", sa.Column("account_id", sa.String(), sa.ForeignKey("auth_accounts.id"), nullable=True))
        op.create_index("ix_anonymous_sessions_account_id", "anonymous_sessions", ["account_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("anonymous_sessions"):
        columns = {item["name"] for item in inspector.get_columns("anonymous_sessions")}
        if "account_id" in columns:
            indexes = {item["name"] for item in inspector.get_indexes("anonymous_sessions")}
            if "ix_anonymous_sessions_account_id" in indexes:
                op.drop_index("ix_anonymous_sessions_account_id", table_name="anonymous_sessions")
            op.drop_column("anonymous_sessions", "account_id")
    if inspector.has_table("auth_sessions"):
        indexes = {item["name"] for item in inspector.get_indexes("auth_sessions")}
        for name in ("ix_auth_sessions_expires_at", "ix_auth_sessions_token_digest", "ix_auth_sessions_account_id"):
            if name in indexes:
                op.drop_index(name, table_name="auth_sessions")
        op.drop_table("auth_sessions")
    if inspector.has_table("auth_accounts"):
        indexes = {item["name"] for item in inspector.get_indexes("auth_accounts")}
        if "ix_auth_accounts_email" in indexes:
            op.drop_index("ix_auth_accounts_email", table_name="auth_accounts")
        op.drop_table("auth_accounts")
